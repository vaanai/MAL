#!/usr/bin/env python3
"""r4-b walk-2 SEALED READ TOOL (EXP-027, DEC-030 slot 5, push-family book 1; PLAN r4b-readtool section 4-5, builder task 7).

One read, no interim looks. This file is part of the pre-registration: its sha256 is pinned in the EXP (EXP027_SHA256_R4B_READ). NOTHING HERE IS EVIDENCE OF EDGE. A PASS
would be "forward simulated paper, not money made".

Order of events (the order is part of the contract; tests pin it)
  1. PREFLIGHT, before any outcome row is opened. Every reason is collected; on any reason: one REFUSED line on stderr, exit 2, NOTHING written.
       EXP pins (each EXP027_* line matches exactly once, file clean vs HEAD, route not PENDING, W0 / k / stop rule), code sha256 of every r4b module, model md5/sha256,
       E-a..E-d md5 records, decoder blobs (walk 2's job ref), E1 record (sha256, n >= 20), input views and the data/tries.jsonl entry, the mint-account fetch file (sha256),
       the fee-arm record (sha256 if pinned), the calendar (E = W0 + 480 h has passed), every hour of the allowlist [H0, E + 3h) sealed / strict-verified / sha256- and
       row-count-matched, counts.jsonl covering every counted hour with equal hashes, the count job's aggregate precount refusals (PDA match < 95 %, < 100 marks with >= 10 live,
       a date with graduations and 0 decisions), mixed null tx_index, unmeasurable SPH, no earlier started / completed / aborted line, no lock file.
     Pinned inputs (QP R4-R8, R10): the EXP file and the models dir are repo constants (no CLI flag); the EXP must be tracked by git and equal HEAD; the walk dir must be
       EXP027_WALK_DIR = /data/mal/blocks/forward-1016; EXP027_SELL_BUILDER and EXP027_A3_RECORD are path@sha256 pins; the E1 scale is 5 in code (the record must carry
       stake_lamports 50,000,000); the allowlist is [2026-10-16T03, E + 3h) and W0 >= 2026-10-18T03.
  1b. STRUCTURE PRE-CHECK (R9), still before the lock: the count job's structure-only build over [H0, E + 3h) with trade_end E + 3h; any refusal refuses the read (nothing spent).
      Scratch left by an OOM-killed run is removed here (the scratch dir is out_dir/scratch; no `started` line exists, else the read refuses).
  2. LOCK: O_EXCL READ.lock, then a `started` line in READS.jsonl. From here a failure BEFORE `completed` writes an `aborted` line (the read is spent: a second run refuses).
     A start at or after E + 48 h (R3) or an A3 halt in [W0, E] (R10) writes `completed` with NOT_DECIDABLE and opens no outcome row.
  3. BUILD the intermediates with r4b_layout (event V; null V on a member print and v0-missing share > 2 % refuse), compute forward-mode features
     (grads_rule asof_pda_first_print, forward_order), drop decision rows with T < block_time(s0) + 600 s (count reported), score with the 5 boosters (float64),
     run the xs10 book from W0 marks to the last entry mark <= E - 1h, sell everything at mark E, price the cost stack (section 5), build the 4 cells, gate (section 6).
  4. VERDICT to stderr, THEN the result file (serialized first, temp file + fsync + os.link, never half-written), THEN the `completed` line. The verdict is never produced after
     a file write. The clock is checked again when the verdict is formed (>= E + 48 h gives NOT_DECIDABLE).
  5. REPORT-ONLY steps (rent leg, no-auto-stranded, 55k, 4.0 s, block sums, LP counts, null block_time, gap table, asof-vs-frozen dual run), AFTER `completed`. Each step is its own
     try/except, writes report-<step>.json (and a report_completed / report_failed ledger line) and an entry in report-only.json. A crash here cannot change the deciding file.

Universe exits (f): the book is hourly; UNIVERSE_EXITS_AT_MARKS_ONLY = True. A token that leaves the universe between marks (dead, 48 h passed) is not ranked at the next mark and is
sold there by xs_book's rank rule (or its gap rule), as in harness.book_xs. Unpriced exit (e): a held token whose exit reserve state is <= 0 or missing is a total loss of the stake plus
the buy send plus the stranded rent of the mint's token program (RENT_STRANDED_T22 2,074,080 for Token-2022 and unknown mints, RENT_STRANDED_SPL 2,039,280; no sell send), exactly as a strand-class mint (QP ruling iii), marked stranded, and stays in the book. Forward features (a): mint_features(grads_rule="asof_pda_first_print",
mint_filters="asof"); the training defaults are frozen. Allowlist (g): [H0 = 2026-10-16T03, E + 1h) for decisions plus the 2 extra trade hours (E + 3h exclusive, as r4b_count's trade_end).

Deciding stack (EXP section 5): 505,000-lamport sends (160,000 and the buy-lag shift only if the pinned fee-arm record says Test A and Test B both PASS), rent 0 per filled trip,
auto-stranded (simulated proceeds below one send fee), strand-class mints from the pinned mint-account fetch file (deny by default: strand-class unless fetch ok, no freeze
authority and owner SPL Token, or Token-2022 with every extension in the pinned allowlist; a mint missing from the file is strand-class and counted), VERIFY haircut
or the larger E1 penalty max(0, -5 * rbar) per filled trip, flat 15 % and pressure legs (intercept refit per cell, mean p 0.289), latencies 1.3 s and 1.9 s,
k = np.round(lat / slot_s [+ delta]), slot_s per UTC date = 3600 / median hourly SPH. Gate: all 4 cells n >= 100, >= 5 dates with a positive majority, trade CI90 lo > 0
(1,000 draws, seed 1, order (T0, mid)), ex-top-3 > 0, ex-best-24h-block > 0, ex-best-UTC-date > 0, and the t over per-attempt 24 h block means: the larger p of the
4 cells <= 0.025 / k. NOT_DECIDABLE: fewer than 20 blocks with attempts or 125 attempts, fewer than 100 trades, decision keys not a subset of the count job's live keys
(or its live md5 differs), a pressure-leg gap. There is no CLI override of any pinned value; the CLI takes only the PATHS of the pinned inputs.

Needs duckdb (/data/mal/audit-1008/venv), lightgbm and solders (/data/mal/venv). Seals: this tool opens only allowlisted hours of the walk-2 directory it is pointed at.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

if __package__ in (None, ""):  # `python tools/r4b_read.py` from the repo root
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import exp012_forward as ef  # noqa: E402
from tools import r4b_book as bk  # noqa: E402
from tools import r4b_count as rc  # noqa: E402
from tools import r4b_features as rf  # noqa: E402
from tools import r4b_frozen as FZ  # noqa: E402
from tools import r4b_layout as rl  # noqa: E402
from tools import r4b_score as rs  # noqa: E402
from tools.latency_curve import _hour_file  # noqa: E402

SCHEMA = "r4b_read_v1"
ROOT = Path(__file__).resolve().parents[1]
EXP_FILE = ROOT / "EXP" / "EXP-027-r4b-walk2-prereg.md"
DEFAULT_OUT = Path("/data/mal/r4b")
PINNED_WALK_DIR = "/data/mal/blocks/forward-1016"  # R5: EXP-027 section 3.4 allows only this dataset directory (resolved); the line EXP027_WALK_DIR must equal it
READS = "READS.jsonl"
LOCK = "READ.lock"
RESULT = "read-result.json"
TRIES = ROOT / "data" / "tries.jsonl"

# ---- pins (changing one changes the registered read) -------------------------------------------------------------------------------------
STOP_HOURS = 480  # E = W0 + 480 h
MIN_BLOCKS = 20  # N: 24 h blocks from W0 holding an attempt
MIN_ATTEMPTS = 125  # A
EXTRA_TRADE_HOURS = 2  # hours after E's exit hour that are sealed and read (allowlist end = E + 3h)
EXIT_HORIZON_H = 1
H0 = "2026-10-16T03"  # the count job's creates start (tools/r4b_count.H0); 10-16T01/T02 are never opened by the read
UNIVERSE_EXITS_AT_MARKS_ONLY = True  # (f) the book is hourly: a token that leaves the universe between marks is sold at the NEXT mark by the rank / gap rule (xs_book), never in between
EARLIEST_W0 = "2026-10-18T03"  # R6: creates start at H0 = 10-16T03; the 48 h universe ramp completes at 10-18T03, so no earlier mark has a complete universe
BLOCK_S = 86400
MARK_S = 3600
D3_DROP_S = 600  # decision rows with T < block_time(s0) + 600 s are dropped
GATE_N = 100
GATE_DATES = 5
ALPHA_FAMILY = 0.025
SEND_FEE_160K = 160_000
LP_TOLS = (1e-6, 1e-2)
LATS = FZ.LATENCIES_S
LAT_REPORT_LONG = 4.0
DEADLINE_HOURS = 48  # EXP027_DEADLINE = E + 48 h; a read that starts (or forms its verdict) at or after it is NOT_DECIDABLE (section 7.1.3)
E1_STAKE_LAMPORTS = 50_000_000  # R8: the E1 live-vs-sim gap was measured at this stake
E1_SCALE = float(FZ.STAKE_LAMPORTS) / E1_STAKE_LAMPORTS  # 250,000,000 / 50,000,000 = 5, fixed in code; the record cannot change it
SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
# R11: deny by default. A Token-2022 mint is NOT strand-class only if EVERY extension is in this set. Filled from the outcome-blind mint-account census
# (tools/mint_account_fetch.py; /data/mal/hunt-1008/push-1016/hunt6/r4b-readtool/mint-census/summary.json): 295 of 300 sampled mints are Token-2022 with exactly
# metadataPointer + tokenMetadata, the other 5 are classic SPL; 0 have a freeze authority. Any other extension still strands, and so does a freeze authority.
TOKEN2022_EXT_ALLOWLIST: frozenset = frozenset({"metadataPointer", "tokenMetadata"})
# Rent by token program (QP ruling on #683). tools/r4b_frozen.py RENT_STRANDED = RENT_REPORT = 2_039_280 is (165 + 128) * 6960, the rent of a 165-byte SPL token
# account. A Token-2022 ATA carries the ImmutableOwner extension, so it is 170 bytes: (170 + 128) * 6960 = 2_074_080, 34_800 more. Checked on chain 2026-10-16:
# the largest accounts of a census Token-2022 mint are owned by TokenzQd..., have data length 170 and extensions [immutableOwner] (PR body has mint, account, slot).
# The frozen constants stay as they are (the frozen E-c replay uses them); this read prices a stranded position and the report-only rent leg by the mint's program.
# A mint with an unknown program (failed fetch, not in the file) is stranded already and is priced at the higher figure.
RENT_STRANDED_SPL = FZ.RENT_STRANDED      # 2_039_280, 165-byte SPL Token account
RENT_STRANDED_T22 = (170 + 128) * 6960    # 2_074_080, 170-byte Token-2022 ATA (ImmutableOwner)
RENT_REPORT_SPL = FZ.RENT_REPORT          # 2_039_280
RENT_REPORT_T22 = RENT_STRANDED_T22       # 2_074_080
assert RENT_STRANDED_T22 == 2_074_080 and RENT_STRANDED_SPL == (165 + 128) * 6960 == 2_039_280
# E1 mode (owner decision pending; the EXP pins it with EXP027_E1_MODE, which must equal this constant):
#   "A"      haircut only (no E1 penalty);
#   "B"      a fixed charge of max(haircut, E1_FIXED_LAMPORTS) per filled, non-stranded trip;
#   "record" max(haircut, max(0, -5 * rbar)) from the pinned E1 record (n >= 20, stake 50,000,000).
E1_MODE = "record"
E1_FIXED_LAMPORTS = 1_920_110
K_RATIO_NEAR_LO, K_RATIO_NEAR_HI = 1 - 1e-3, 1 - 1e-6  # report-only: exits whose k ratio sits just inside the C8 tolerance
CODE_MODULES = ("r4b_frozen", "r4b_score", "r4b_book", "r4b_features", "r4b_layout", "r4b_count", "r4b_read")
ARTIFACT_NAMES = tuple(FZ.ARTIFACT_MD5)

REQUIRED_KEYS = (
    "EXP027_BOOK_ID", "EXP027_FAMILY", "EXP027_ROUTE", "EXP027_COUNT_START", "EXP027_LAST_W0", "EXP027_STOP_RULE", "EXP027_EXIT_HORIZON_H", "EXP027_CAP_END",
    "EXP027_DEADLINE", "EXP027_ALPHA", "EXP027_DECISION_P", "EXP027_SEND_FEE_LAMPORTS", "EXP027_RENT_DECIDING",
    # lines the manager adds at registration / before the read (the draft has them only as TO FILL prose)
    "EXP027_W0", "EXP027_K", "EXP027_DECODER_REF", "EXP027_TIERS_SHA256", "EXP027_E1_RECORD", "EXP027_INPUT_VIEWS", "EXP027_TRIES_ENTRY", "EXP027_MINT_ACCOUNTS",
    "EXP027_FEE_ARM", "EXP027_E_A_MD5", "EXP027_E_B_MD5", "EXP027_E_C_MD5", "EXP027_E_D_MD5",
    "EXP027_WALK_DIR", "EXP027_SELL_BUILDER", "EXP027_A3_RECORD", "EXP027_E1_MODE",  # R5, R7, R10, E1 mode
) + tuple(f"EXP027_SHA256_{m.upper()}" for m in CODE_MODULES)


def required_keys() -> tuple[str, ...]:
    """The E1 record line is required only in mode "record"."""
    return tuple(k for k in REQUIRED_KEYS if not (k == "EXP027_E1_RECORD" and E1_MODE != "record"))


HEX64, HEX32 = re.compile(r"^[0-9a-f]{64}$"), re.compile(r"^[0-9a-f]{32}$")


class CountBuildDivergence(Exception):
    """The read's own build contradicts counts.jsonl (a structure check the count job passed fails on the same hours): NOT_DECIDABLE, a real anomaly."""

    def __init__(self, reasons: Sequence[str]):
        self.reasons = list(reasons)
        super().__init__("count_build_divergence: " + "; ".join(self.reasons))


class ReadRefused(Exception):
    def __init__(self, reasons: Sequence[str] | str):
        self.reasons = [reasons] if isinstance(reasons, str) else list(reasons)
        super().__init__("r4b_read refused: " + "; ".join(self.reasons))


# ---- small helpers -------------------------------------------------------------------------------------------------------------------------
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(p: Path) -> str:
    return FZ.file_sha256(str(p))


def stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=_json_default)


def _json_default(o: Any) -> Any:
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))


def fee_tiers_sha256() -> str:
    return hashlib.sha256(json.dumps([list(t) for t in FZ.FEE_TIERS], separators=(",", ":")).encode()).hexdigest()


def split_pair(val: str) -> tuple[str, str]:
    """'path@sha256' -> (path, sha256)."""
    p, _, h = val.rpartition("@")
    return p, h


# ---- EXP pins ---------------------------------------------------------------------------------------------------------------------------------
def exp_lines(text: str) -> tuple[dict[str, str], list[str]]:
    """Each EXP027_* key must match `^KEY: value$` exactly once. Returns (values, problems)."""
    out, probs = {}, []
    for k in required_keys():
        m = re.findall(rf"^{k}: (.*\S)\s*$", text, flags=re.M)
        if len(m) != 1:
            probs.append(f"EXP pin {k}: matches {len(m)} times (need exactly 1)")
        else:
            out[k] = m[0]
    return out, probs


def exp_clean_against_head(exp_file: Path = EXP_FILE, repo: Path = ROOT) -> list[str]:
    """The EXP must exist, be TRACKED by git and equal HEAD. `git diff --quiet HEAD` alone returns 0 for an untracked or a missing file (QP R4), so tracking is checked first."""
    if not Path(exp_file).exists():
        return [f"{Path(exp_file).name} does not exist"]
    tracked = subprocess.run(["git", "-C", str(repo), "ls-files", "--error-unmatch", "--", str(exp_file)], stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
    if tracked.returncode != 0:
        return [f"{Path(exp_file).name} is not tracked by git (git ls-files --error-unmatch failed)"]
    r = subprocess.run(["git", "-C", str(repo), "diff", "--quiet", "HEAD", "--", str(exp_file)], stdin=subprocess.DEVNULL, timeout=60)
    return [] if r.returncode == 0 else [f"{Path(exp_file).name} differs from HEAD"]


def parse_hour(s: str) -> datetime | None:
    try:
        return ef.hour_dt(s)
    except ValueError:
        return None


def pin_problems(v: Mapping[str, str], code_dir: Path) -> tuple[list[str], dict[str, Any]]:
    """Semantic checks of the pinned values. Returns (problems, parsed)."""
    probs: list[str] = []
    P: dict[str, Any] = {}
    if v.get("EXP027_ROUTE") not in ("R1", "R2"):
        probs.append(f"EXP027_ROUTE is {v.get('EXP027_ROUTE')!r} (PENDING or unknown): the route is not decided")
    if v.get("EXP027_BOOK_ID") != "r4-b":
        probs.append("EXP027_BOOK_ID is not r4-b")
    if v.get("EXP027_EXIT_HORIZON_H") != str(EXIT_HORIZON_H):
        probs.append(f"EXP027_EXIT_HORIZON_H is {v.get('EXP027_EXIT_HORIZON_H')!r}, the tool pins {EXIT_HORIZON_H}")
    if f"{STOP_HOURS} h" not in v.get("EXP027_STOP_RULE", "") or str(MIN_BLOCKS) not in v.get("EXP027_STOP_RULE", "") or str(MIN_ATTEMPTS) not in v.get("EXP027_STOP_RULE", ""):
        probs.append(f"EXP027_STOP_RULE does not state E = W0 + {STOP_HOURS} h with N = {MIN_BLOCKS} and A = {MIN_ATTEMPTS}")
    if not v.get("EXP027_ALPHA", "").startswith(f"{ALPHA_FAMILY}/k"):
        probs.append(f"EXP027_ALPHA is not {ALPHA_FAMILY}/k")
    if "505000" not in v.get("EXP027_SEND_FEE_LAMPORTS", "") or "160000" not in v.get("EXP027_SEND_FEE_LAMPORTS", ""):
        probs.append("EXP027_SEND_FEE_LAMPORTS does not state 505000 / 160000")
    if not v.get("EXP027_RENT_DECIDING", "").startswith("0 "):
        probs.append("EXP027_RENT_DECIDING is not 0 per filled trip (rent deciding is not what this tool prices)")
    if f"{DEADLINE_HOURS} h" not in v.get("EXP027_DEADLINE", ""):
        probs.append(f"EXP027_DEADLINE does not state E + {DEADLINE_HOURS} h")
    w0 = parse_hour(v.get("EXP027_W0", ""))
    last = parse_hour(v.get("EXP027_LAST_W0", ""))
    if w0 is None:
        probs.append(f"EXP027_W0 {v.get('EXP027_W0')!r} is not YYYY-MM-DDTHH")
    else:
        P["w0"] = w0
        if w0 < ef.hour_dt(EARLIEST_W0):
            probs.append(f"W0 {v['EXP027_W0']} is before {EARLIEST_W0}")
        if last is not None and w0 > last:
            probs.append(f"W0 {v['EXP027_W0']} is after EXP027_LAST_W0 {v['EXP027_LAST_W0']}")
    try:
        P["k"] = int(v.get("EXP027_K", ""))
        if P["k"] < 1:
            raise ValueError
    except ValueError:
        probs.append(f"EXP027_K {v.get('EXP027_K')!r} is not an integer >= 1")
    # code sha256, E-md5, tiers
    shas = {}
    for m in CODE_MODULES:
        want = v.get(f"EXP027_SHA256_{m.upper()}", "")
        got = sha256_file(code_dir / f"{m}.py") if (code_dir / f"{m}.py").exists() else None
        shas[m] = got
        if not HEX64.match(want):
            probs.append(f"EXP027_SHA256_{m.upper()} is not a sha256 (TO FILL?)")
        elif got != want:
            probs.append(f"code sha256 of {m}.py is {got}, the EXP pins {want}")
    P["code_sha256"] = shas
    for e in ("A", "B", "C", "D"):
        if not HEX32.match(v.get(f"EXP027_E_{e}_MD5", "")):
            probs.append(f"E-{e.lower()} md5 record is missing or not an md5 (EXP027_E_{e}_MD5)")
    if v.get("EXP027_TIERS_SHA256") != fee_tiers_sha256():
        probs.append(f"FEE_TIERS sha256 {fee_tiers_sha256()} != EXP027_TIERS_SHA256 {v.get('EXP027_TIERS_SHA256')}")
    return probs, P


def pinned_file(val: str, label: str, probs: list[str]) -> Path | None:
    p, h = split_pair(val)
    if not p or not HEX64.match(h):
        probs.append(f"{label}: {val!r} is not 'path@sha256'")
        return None
    path = Path(p)
    if not path.exists():
        probs.append(f"{label}: {p} does not exist")
        return None
    if sha256_file(path) != h:
        probs.append(f"{label}: sha256 of {p} is {sha256_file(path)}, pinned {h}")
        return None
    return path


# ---- records: E1, input views, tries, mint accounts, fee arm -------------------------------------------------------------------------------
E1_MIN_N = 20


def load_e1(v: Mapping[str, str], probs: list[str]) -> dict[str, Any] | None:
    p = pinned_file(v["EXP027_E1_RECORD"], "E1 record", probs)
    if p is None:
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    n, rbar = d.get("n"), d.get("pnl_gap_lamports_live_minus_sim_mean")
    if not isinstance(n, int) or n < E1_MIN_N:
        probs.append(f"E1 record n is {n!r} (need an integer >= {E1_MIN_N})")
    if not isinstance(rbar, (int, float)) or isinstance(rbar, bool) or not math.isfinite(rbar):
        probs.append("E1 record has no numeric pnl_gap_lamports_live_minus_sim_mean")
        return None
    if d.get("stake_lamports") != E1_STAKE_LAMPORTS or isinstance(d.get("stake_lamports"), bool):
        probs.append(f"E1 record stake_lamports is {d.get('stake_lamports')!r} (need {E1_STAKE_LAMPORTS}: the scale to the 0.25 SOL stake is fixed at {E1_SCALE:g} in code)")
        return None
    scale = E1_SCALE  # R8: never read from the record
    return {"mode": "record", "n": n, "rbar_lamports": float(rbar), "scale": scale, "penalty_lamports": max(0.0, -scale * float(rbar)), "path": str(p), "sha256": split_pair(v["EXP027_E1_RECORD"])[1]}


def e1_fixed_mode(v: Mapping[str, str], probs: list[str]) -> dict[str, Any] | None:
    """Modes A and B carry no record. The EXP line EXP027_E1_MODE must equal the tool's E1_MODE constant."""
    if E1_MODE not in ("A", "B"):
        probs.append(f"E1_MODE {E1_MODE!r} is not A, B or record")
        return None
    return {"mode": E1_MODE, "penalty_lamports": 0.0 if E1_MODE == "A" else float(E1_FIXED_LAMPORTS), "scale": None, "n": None}


def load_sell_builder(v: Mapping[str, str], probs: list[str]) -> dict[str, str] | None:
    """R7: rent 0 holds only because the sell tx closes the token ATA (EXP027_RENT_DECIDING condition i). The sell-builder file is pinned path@sha256, computed and matched."""
    p = pinned_file(v["EXP027_SELL_BUILDER"], "sell builder", probs)
    return None if p is None else {"path": str(p), "sha256": split_pair(v["EXP027_SELL_BUILDER"])[1]}


def parse_iso(s: Any) -> datetime | None:
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def load_a3(v: Mapping[str, str], w0: datetime, e: datetime, probs: list[str]) -> list[dict[str, Any]] | None:
    """R10: the A3 structure-monitor record (EXP section 7.1.6). {schema, checked_from, checked_through, halts: [{at, reason}]}. It must cover [W0, E]; a halt with W0 <= at <= E
    makes the read NOT_DECIDABLE (returned here; the read writes that result after the lock). An unparsable record refuses."""
    p = pinned_file(v["EXP027_A3_RECORD"], "A3 record", probs)
    if p is None:
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        probs.append(f"A3 record {p} is not JSON")
        return None
    lo, hi = parse_iso(d.get("checked_from")), parse_iso(d.get("checked_through"))
    halts = d.get("halts")
    if lo is None or hi is None or not isinstance(halts, list):
        probs.append("A3 record needs checked_from, checked_through (YYYY-MM-DDTHH:MM:SSZ) and a halts list")
        return None
    if lo > w0 or hi < e:
        probs.append(f"A3 record covers [{iso(lo)}, {iso(hi)}], not [W0 {iso(w0)}, E {iso(e)}]")
        return None
    inside = []
    for h in halts:
        at = parse_iso(h.get("at")) if isinstance(h, dict) else None
        if at is None:
            probs.append(f"A3 record has a halt without a parsable 'at': {h!r}")
            return None
        if w0 <= at <= e:
            inside.append({"at": iso(at), "reason": h.get("reason")})
    return inside


def check_inputs(v: Mapping[str, str], tries_path: Path, probs: list[str]) -> dict[str, str]:
    out = {}
    for item in [x for x in v["EXP027_INPUT_VIEWS"].split(";") if x.strip()]:
        p = pinned_file(item.strip(), "input view", probs)
        if p is not None:
            out[str(p)] = split_pair(item.strip())[1]
    if not out:
        probs.append("EXP027_INPUT_VIEWS lists no input view")
    key = v["EXP027_TRIES_ENTRY"]
    ok = False
    if tries_path.exists():
        for ln in tries_path.read_text(encoding="utf-8").splitlines():
            if ln.strip() and key in ln:
                ok = True
    if not ok:
        probs.append(f"{tries_path} has no entry containing {key!r}")
    return out


def mint_is_strand_class(r: Any, allow: frozenset | None = None) -> bool:
    """R11, deny by default. NOT strand-class only if the fetch succeeded, there is no freeze authority, and the owner program is SPL Token, or Token-2022 with an extension
    list (a list, possibly empty) every member of which is in the pinned allowlist. Anything else (unknown owner, missing field, malformed entry) is strand-class."""
    allow = TOKEN2022_EXT_ALLOWLIST if allow is None else allow
    if not isinstance(r, dict) or r.get("fetch_ok") is not True or r.get("freeze_authority"):
        return True
    owner = r.get("owner_program")
    if owner == SPL_TOKEN_PROGRAM:
        return False
    if owner == TOKEN_2022_PROGRAM:
        ext = r.get("token2022_extensions")
        return not (isinstance(ext, list) and all(isinstance(x, str) and x in allow for x in ext))
    return True


def mint_rent_is_t22(r: Any) -> bool:
    """Rent class of a mint: SPL Token only if the record says so with a successful fetch; Token-2022 and anything unknown (failed fetch, malformed, other owner) is priced
    at the higher Token-2022 figure."""
    return not (isinstance(r, dict) and r.get("fetch_ok") is True and r.get("owner_program") == SPL_TOKEN_PROGRAM)


def load_mint_accounts(v: Mapping[str, str], probs: list[str]) -> tuple[dict[str, bool], dict[str, Any], dict[str, bool]] | None:
    """({mint: strand_class}, info, {mint: rent_t22}). Schema: {fetch_slot, mints: {mint: {fetch_ok, owner_program, freeze_authority, token2022_extensions}}}. A mint that is not in the file is
    strand-class (the read counts those, no silent default). info carries fetch_slot and the failed-fetch share."""
    p = pinned_file(v["EXP027_MINT_ACCOUNTS"], "mint-account file", probs)
    if p is None:
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    slot = d.get("fetch_slot")
    if not isinstance(slot, int) or isinstance(slot, bool):
        probs.append("mint-account file has no integer fetch_slot")
        return None
    mints = d.get("mints")
    if not isinstance(mints, dict):
        probs.append("mint-account file has no mints object")
        return None
    out = {m: mint_is_strand_class(r) for m, r in mints.items()}
    t22 = {m: mint_rent_is_t22(r) for m, r in mints.items()}
    failed = sum(1 for r in mints.values() if not isinstance(r, dict) or r.get("fetch_ok") is not True)
    info = {"fetch_slot": slot, "mints_in_file": len(mints), "failed_fetch": failed, "failed_fetch_share": (failed / len(mints)) if mints else None,
            "strand_class_in_file": int(sum(out.values())), "token2022_ext_allowlist": sorted(TOKEN2022_EXT_ALLOWLIST), "path": str(p), "sha256": split_pair(v["EXP027_MINT_ACCOUNTS"])[1],
            "rent_t22_in_file": int(sum(t22.values())), "rent_stranded_t22": RENT_STRANDED_T22, "rent_stranded_spl": RENT_STRANDED_SPL}
    return out, info, t22


def fee_arm_decision(v: Mapping[str, str], started: datetime, probs: list[str]) -> dict[str, Any]:
    """EXP section 5.3. 160k + lag shift only if the pinned record says Test A and Test B both PASS; anything else is 505k."""
    base = {"send": FZ.SEND_FEE_DECIDING, "delta_slots": 0.0, "source": "505k"}
    raw = v["EXP027_FEE_ARM"].strip()
    if raw.upper() == "NONE":
        return {**base, "reason": "no fee-arm record pinned"}
    p, h = split_pair(raw)
    path = Path(p)
    if not path.exists():
        return {**base, "reason": f"fee-arm record {p} missing: fallback 505k"}
    if not HEX64.match(h) or sha256_file(path) != h:
        probs.append(f"fee-arm record {p}: sha256 does not equal the pinned value")
        return base
    d = json.loads(path.read_text(encoding="utf-8"))
    at = d.get("verdict_at")
    try:
        at_dt = datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return {**base, "reason": "fee-arm record has no verdict_at: fallback 505k"}
    if at_dt >= started:
        return {**base, "reason": "fee-arm verdict is dated at or after the read start: fallback 505k"}
    a, b = d.get("test_a_pass"), d.get("test_b_pass")
    ub = d.get("test_b_upper95_slots")
    if a is True and b is True and isinstance(ub, (int, float)) and not isinstance(ub, bool) and math.isfinite(ub):
        return {"send": SEND_FEE_160K, "delta_slots": max(0.0, float(ub)), "source": "160k", "record": p, "sha256": h, "test_b_upper95_slots": float(ub)}
    return {**base, "reason": f"fee-arm Test A {a!r}, Test B {b!r}: not both PASS: 505k"}


# ---- slot time ----------------------------------------------------------------------------------------------------------------------------
def slot_s_by_date(sph_by_hour: Mapping[str, float]) -> dict[str, float]:
    """slot_s(date) = 3600 / median over the date's hours of SPH_h (QP-4 section 2)."""
    by: dict[str, list[float]] = {}
    for h, s in sph_by_hour.items():
        by.setdefault(h[:10], []).append(float(s))
    return {d: 3600.0 / float(np.median(v)) for d, v in by.items()}


def k_for(lat: float, slot_s: np.ndarray, delta: float = 0.0) -> np.ndarray:
    """Entry slots k = np.round(lat / slot_s + delta) (delta: the fee-arm buy-lag shift, 0 unless the 160k leg applies)."""
    return np.round(lat / np.asarray(slot_s, float) + float(delta)).astype(np.int64)


def entry_states_shifted(sl, QX, BX, ib, so, sd, s_dec, lat: float, delta: float, nearby_s: float = FZ.NEARBY_WINDOW_S):
    """The entry block of r4b_book.mint_states with k = round(lat / s + delta); at delta = 0 it equals mint_states (a test pins it)."""
    sl = np.asarray(sl)
    k = k_for(lat, s_dec, delta)
    x = np.asarray(sd) + k
    je = np.searchsorted(sl, x + 1, "left")
    ix = np.searchsorted(sl, x, "left")
    lo2 = np.searchsorted(sl, x - np.round(nearby_s / np.asarray(s_dec, float)).astype(np.int64), "left")
    cib = np.concatenate([[0], np.cumsum(ib)])
    cbv = np.concatenate([[0], np.cumsum(np.where(ib, so, 0.0))])
    return QX[je], BX[je], (cib[je] - cib[ix]).astype(float), cbv[je] - cbv[lo2]


# ---- ledger and lock ----------------------------------------------------------------------------------------------------------------------
def reads_events(out_dir: Path) -> list[dict[str, Any]]:
    p = out_dir / READS
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def ledger_event(out_dir: Path, event: str, **kw: Any) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / READS).open("a", encoding="utf-8") as f:
        f.write(stable_json({"event": event, "at": iso(now_utc()), **kw}) + "\n")
        f.flush()
        os.fsync(f.fileno())


def take_lock(out_dir: Path) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(out_dir / LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o444)
    except FileExistsError as exc:
        raise ReadRefused(f"{out_dir / LOCK} exists: the read was already started (no second run, no resume)") from exc
    token = os.urandom(8).hex()
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps({"token": token, "at": iso(now_utc())}) + "\n")
    return token


# ---- config and deps ----------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Paths:
    walk_dir: Path
    out_dir: Path = DEFAULT_OUT
    counts_dir: Path = DEFAULT_OUT
    exp_file: Path = EXP_FILE
    tries: Path = TRIES
    models_dir: Path | None = None


@dataclass
class Deps:
    """Everything the tests replace. The defaults are the production behaviour."""

    now_fn: Callable[[], datetime] = now_utc
    clean_fn: Callable[[Path], list[str]] = exp_clean_against_head
    decoder_fn: Callable[[str], list[str]] = lambda ref: rl.decoder_pin_problems(ref)
    problems_fn: rl.ProblemsFn | None = None
    verify: Mapping[str, Mapping[str, Any]] | None = None
    sph_fn: rc.SphFn | None = None
    canonical_fn: rl.CanonFn | None = None
    boosters_fn: Callable[[str | None], list] = rs.load_boosters
    models_check_fn: Callable[[str | None], list[str]] | None = None
    walk_pin: str = PINNED_WALK_DIR  # R5 (tests point it at their synthetic walk dir)
    head_fn: Callable[[], str] = lambda: subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60).stdout.strip()
    stderr: Callable[[str], None] = lambda s: print(s, file=sys.stderr, flush=True)
    memory_limit: str = "6GB"
    threads: int = 4
    code_dir: Path = Path(__file__).resolve().parent
    keep_scratch: bool = False


def default_models_check(models_dir: str | None) -> list[str]:
    probs = []
    for n in ARTIFACT_NAMES:
        try:
            rs.check_artifact(n, models_dir)
        except rs.PinMismatch as exc:
            probs.append(str(exc))
    try:
        rs.check_features(models_dir)
    except rs.PinMismatch as exc:
        probs.append(str(exc))
    return probs


@dataclass
class Plan:
    """What preflight establishes; the read uses only this."""

    v: dict[str, str]
    w0: datetime
    e: datetime
    k: int
    hours: list[str]            # the allowlist [H0, E + 3h)
    count_cfg: rc.Config
    counts: list[dict[str, Any]]
    e1: dict[str, Any]
    strand: dict[str, bool]
    inputs: dict[str, str]
    sph: dict[str, float] = field(default_factory=dict)
    deadline: datetime | None = None
    sell_builder: dict[str, str] = field(default_factory=dict)
    mint_info: dict[str, Any] = field(default_factory=dict)
    a3_halts: list = field(default_factory=list)
    rent_t22: dict[str, bool] = field(default_factory=dict)


# ---- preflight (nothing is written; no outcome row is opened) ---------------------------------------------------------------------------
def preflight(paths: Paths, deps: Deps, started: datetime) -> tuple[Plan | None, dict[str, Any], list[str]]:
    """All refusals that can be decided before any trade row is parsed. Returns (plan, fee_arm, reasons)."""
    probs: list[str] = []
    out_dir = Path(paths.out_dir)
    if (out_dir / LOCK).exists():
        probs.append(f"{out_dir / LOCK} exists: the read was already started (no second run)")
    ev = reads_events(out_dir)
    if ev:
        probs.append(f"{out_dir / READS} already holds {[e.get('event') for e in ev]}: no second run")
    if not paths.exp_file.exists():
        return None, {}, probs + [f"{paths.exp_file} does not exist"]
    text = paths.exp_file.read_text(encoding="utf-8")
    v, p1 = exp_lines(text)
    probs += p1
    probs += deps.clean_fn(paths.exp_file)
    if len(v) != len(required_keys()):
        return None, {}, probs
    p2, P = pin_problems(v, deps.code_dir)
    probs += p2
    probs += (deps.models_check_fn or default_models_check)(str(paths.models_dir) if paths.models_dir else None)
    probs += deps.decoder_fn(v["EXP027_DECODER_REF"])
    if v["EXP027_E1_MODE"] != E1_MODE:
        probs.append(f"EXP027_E1_MODE is {v['EXP027_E1_MODE']!r}, the tool is built for {E1_MODE!r}")
    e1 = load_e1(v, probs) if E1_MODE == "record" else e1_fixed_mode(v, probs)
    inputs = check_inputs(v, paths.tries, probs)
    mint_loaded = load_mint_accounts(v, probs)
    strand, mint_info, rent_t22 = mint_loaded if mint_loaded is not None else (None, {}, {})
    sell = load_sell_builder(v, probs)
    fee = fee_arm_decision(v, started, probs)
    if v["EXP027_WALK_DIR"] != deps.walk_pin or Path(paths.walk_dir).resolve() != Path(deps.walk_pin).resolve() or Path(v["EXP027_WALK_DIR"]).resolve() != Path(deps.walk_pin).resolve():
        probs.append(f"walk dir {Path(paths.walk_dir).resolve()} / EXP027_WALK_DIR {v['EXP027_WALK_DIR']!r} is not the pinned dataset {deps.walk_pin}")
    if "w0" not in P:
        return None, fee, probs
    w0 = P["w0"]
    e = w0 + timedelta(hours=STOP_HOURS)
    a3 = load_a3(v, w0, e, probs)
    if deps.now_fn() < e:
        probs.append(f"the calendar stop E = {ef.hour_key(e)} has not passed")
    end = e + timedelta(hours=EXIT_HORIZON_H + EXTRA_TRADE_HOURS)
    hours = rl.hour_range(H0, ef.hour_key(end))
    walk = Path(paths.walk_dir)
    try:
        rl.check_hours(walk, hours, deps.problems_fn, deps.verify)
        table = deps.verify if deps.verify is not None else ef.verified_hours(walk, strict_bad_lines=True)
    except rl.LayoutRefused as exc:
        probs += exc.reasons
        table = {}
    counts, count_cfg = [], None
    cdir = Path(paths.counts_dir)
    cfgp = cdir / rc.CONFIG
    if not cfgp.exists():
        probs.append(f"{cfgp} is missing: the count job has not run")
    else:
        pin = json.loads(cfgp.read_text(encoding="utf-8"))
        if pin.get("w0") != ef.hour_key(w0):
            probs.append(f"count job W0 {pin.get('w0')} != EXP W0 {ef.hour_key(w0)}")
        for m in ("r4b_count", "r4b_layout"):
            if (pin.get("code_sha256") or {}).get(f"{m}.py") != P["code_sha256"].get(m):
                probs.append(f"count job ran {m}.py at {(pin.get('code_sha256') or {}).get(f'{m}.py')}, the read pins {P['code_sha256'].get(m)}")
        want_pin = {"w0": ef.hour_key(w0), "stop_mode": rc.STOP_MODE, "h0": H0, "a_attempts": MIN_ATTEMPTS, "calendar_stop": ef.hour_key(e), "exit_horizon_h": EXIT_HORIZON_H}
        bad_pin = [f"{k}: count config {pin.get(k)!r} != EXP/read {v_!r}" for k, v_ in want_pin.items() if pin.get(k) != v_]
        if int(pin.get("n_blocks", -1)) * rc.BLOCK_S != STOP_HOURS * 3600:
            bad_pin.append(f"n_blocks: count config {pin.get('n_blocks')!r} blocks != the {STOP_HOURS} h stop")
        if bad_pin:
            probs.append("count config.json pins differ from the EXP's: " + "; ".join(bad_pin))
        count_cfg = rc.Config(Path(paths.walk_dir), cdir, ef.hour_key(w0), n_blocks=int(pin.get("n_blocks", rc.N_BLOCKS)), a_attempts=int(pin.get("a_attempts", rc.A_ATTEMPTS)), h0=H0)
        counts = rc.read_counts(cdir)
        rpath = cdir / rc.REFUSED
        if rpath.exists() and rpath.read_text(encoding="utf-8").strip():
            first = json.loads(rpath.read_text(encoding="utf-8").splitlines()[0])
            probs.append(f"the count job recorded refusal(s) in {rpath} (first: {'; '.join(first.get('reasons', []))[:300]}); structure checks (v0-missing, null-V member, mixed null tx_index, no-create) did not all pass")
        by_hour = {ln["hour"]: ln for ln in counts}
        need = rl.hour_range(H0, ef.hour_key(e + timedelta(hours=1)))  # [H0, E] inclusive
        miss = [h for h in need if h not in by_hour]
        if miss:
            probs.append(f"counts.jsonl lacks {len(miss)} hour(s) of [H0, E], first {miss[:3]}")
        for h in need:
            ln = by_hour.get(h)
            if ln is None:
                continue
            want_x = 0 if h == ef.hour_key(e) else (1 if h == ef.hour_key(e - timedelta(hours=1)) else rc.EXTRA_HOURS)
            if ln.get("extra_hours_loaded") != want_x:
                probs.append(f"hour {h}: extra_hours_loaded is {ln.get('extra_hours_loaded')!r}, the rule needs {want_x}")
        for h in need:
            ln = by_hour.get(h)
            if ln is None or h not in table:
                continue
            if any(ln["sha256"].get(s) != table[h]["sha256"].get(s) for s in rl.SUBS):
                probs.append(f"hour {h}: counts.jsonl sha256 differs from verify.jsonl")
        if not miss:
            probs += rc.aggregate_problems([by_hour[h] for h in need], count_cfg, int(e.timestamp()))
            mixed = sum(by_hour[h].get("mixed_null_txi_slots") or 0 for h in need)
            if mixed:
                probs.append(f"{mixed} (mint, slot) pair(s) mixing null and non-null tx_index in the count lines")
    sph: dict[str, float] = {}
    sph_fn = deps.sph_fn or rc.default_sph
    by_hour = {ln["hour"]: ln for ln in counts}
    for h in hours:
        if h in by_hour and by_hour[h].get("sph"):
            sph[h] = float(by_hour[h]["sph"])
            continue
        if h not in table:
            continue
        f = _hour_file(walk / "trades", "trades", h)
        s = sph_fn(f) if f is not None else None
        if s is None:
            probs.append(f"hour {h}: slots-per-hour is unmeasurable")
        else:
            sph[h] = float(s)
    if probs or e1 is None or strand is None or count_cfg is None or sell is None or a3 is None:
        return None, fee, probs
    return Plan(v, w0, e, P["k"], hours, count_cfg, counts, e1, strand, inputs, sph, e + timedelta(hours=DEADLINE_HOURS), sell, mint_info, a3, rent_t22), fee, probs


# ---- the computation -----------------------------------------------------------------------------------------------------------------------
def lp_discontinuities(isbuy, tok, b, mid) -> dict[str, int]:
    """Report-only (EXP 3.5): consecutive prints of one pool where the next print's pre-trade BASE reserve differs from the previous print's implied post-trade base reserve."""
    ib = np.asarray(isbuy, bool)
    tk = np.asarray(tok, float)
    bb = np.asarray(b, float)
    m = np.asarray(mid)
    if len(bb) < 2:
        return {"pairs": 0, **{f"gt_{t:g}": 0 for t in LP_TOLS}}
    post = np.where(ib, bb - tk, bb + tk)[:-1]
    same = m[1:] == m[:-1]
    rel = np.abs(bb[1:] - post) / np.maximum(np.abs(post), 1.0)
    out = {"pairs": int(same.sum())}
    for t in LP_TOLS:
        out[f"gt_{t:g}"] = int(((rel > t) & same).sum())
    return out


def live_keys(scratch: Path, marks: Sequence[tuple[int, int]], meta_mint: Mapping[int, str], mbt_by_mid: Mapping[int, int], con: Any) -> dict[int, dict[str, int]]:
    """The count job's live set per mark, recomputed with the same predicate as r4b_count.structure_hour_lines: T >= mbt + 600, T <= mbt + 48h, the latest bt of the
    canonical-pool prints with slot < sd lies in [T - 600, T). Returns {mark_t: {mint: prints before sd}}. mbt_by_mid holds only mints with mbt >= UNIVERSE_MIN_MBT."""
    out = {}
    con.sql(f"create or replace view utv as select mid, slot, bt from read_parquet('{scratch}/ut/*.parquet')")
    for t, sd in marks:
        rows = con.sql(f"""select mid, count(*) from utv where slot < {sd} group by mid having max(bt) >= {t - rc.LIVE_WINDOW_S} and max(bt) < {t}""").fetchall()
        s_ = {}
        for mid, n in rows:
            mb = mbt_by_mid.get(mid)
            if mb is not None and mb + rc.LIVE_LO_S <= t <= mb + rc.LIVE_HI_S:
                s_[meta_mint[mid]] = int(n)
        out[t] = s_
    return out


def gap_by_cause(live: Mapping[str, int], dec: set, d3: set, mint_mid: Mapping[str, int], t: int) -> dict[str, int]:
    """Live tokens at a mark that have no decision row, by cause: fewer than 3 prints before sd; dropped by the s0 + 600 s rule; no feature row (reserve state <= 0,
    asof filter, grid)."""
    out = {"lt3_prints": 0, "d3_drop": 0, "no_feature_row": 0}
    for m, n in live.items():
        if m in dec:
            continue
        if n < rc.MIN_PRINTS_DECISION:
            out["lt3_prints"] += 1
        elif (mint_mid[m], t) in d3:
            out["d3_drop"] += 1
        else:
            out["no_feature_row"] += 1
    return out


@dataclass
class Rows:
    mid: list = field(default_factory=list)
    T: list = field(default_factory=list)
    sd: list = field(default_factory=list)
    X: list = field(default_factory=list)
    sdec: list = field(default_factory=list)
    spot: list = field(default_factory=list)
    ent: dict = field(default_factory=dict)  # lat -> list of (Qe, Be, ssb, nearby) arrays
    marks: list = field(default_factory=list)  # (mid, t, Qx, Bx)


def ut_chunks(con: Any, scratch: Path, rows_per_chunk: int = 3_000_000):
    """Yield (mid_lo, mid_hi) ranges of ut with about rows_per_chunk rows."""
    cnt = con.sql(f"select mid, count(*) from read_parquet('{scratch}/ut/*.parquet') group by 1 order by 1").fetchall()
    lo, acc, last = None, 0, None
    for mid, n in cnt:
        if lo is None:
            lo = mid
        acc += n
        last = mid
        if acc >= rows_per_chunk:
            yield lo, mid
            lo, acc = None, 0
    if lo is not None:
        yield lo, last


def fnum(x: Any) -> float:
    return float("nan") if x is None or x is getattr(__import__("pandas"), "NA", object()) or (isinstance(x, float) and math.isnan(x)) else float(x)


def build_rows(scratch: Path, plan: Plan, fee: Mapping[str, Any], slot_s: Mapping[str, float], deps: Deps, universe_min_mbt: int) -> tuple[Rows, dict[str, Any], Any]:
    """Features -> hour-mark decision rows with entry states (per latency) and the mark table, mint by mint. Returns (Rows, report, duckdb con)."""
    import duckdb
    import pandas as pd

    work = scratch / "work"
    rl.prep_work(scratch, work, threads=deps.threads, memory_limit=deps.memory_limit)
    con = duckdb.connect()
    con.sql(f"SET memory_limit='{deps.memory_limit}'; SET threads={int(deps.threads)}; SET enable_progress_bar=false; SET preserve_insertion_order=true")
    ckb, cks = np.load(work / "clock_bt.npy"), np.load(work / "clock_slot.npy")
    mk = np.load(work / "mkt.npz")
    meta = pd.read_parquet(scratch / "meta.parquet")
    meta = meta[meta["mbt"] >= universe_min_mbt].reset_index(drop=True)
    mcn = pd.read_pickle(work / "mintconst.pkl").set_index("mid")
    prep = rf.build_prep(ckb, cks, mk["mnt"], {k: mk[k] for k in ("n_ps", "v_ps", "n_bd", "v_bd", "n_cr")}, meta["mbt"].to_numpy(np.int64),
                         meta["s0"].to_numpy(np.float64))
    w0, e = int(plan.w0.timestamp()), int(plan.e.timestamp())
    last_entry = e - MARK_S
    R = Rows()
    R.ent = {lat: [] for lat in (*LATS, LAT_REPORT_LONG)}
    rep = {"d3_keys": set(), "d3_dropped": 0, "d3_kept_pre_filter": 0, "mints": int(len(meta)), "mints_with_rows": 0, "lp": {"pairs": 0, **{f"gt_{t:g}": 0 for t in LP_TOLS}}}
    members = set(meta["mid"].tolist())
    dates_s = lambda T: np.array([slot_s[d] for d in bk.utc_dates(T)])  # noqa: E731
    delta = float(fee["delta_slots"])
    for lo, hi in ut_chunks(con, scratch):
        U = con.sql(f"""select mid, slot, txi, evi, bt, isbuy, sol, tok, q, b, th, vq from read_parquet('{scratch}/ut/*.parquet', filename=true, file_row_number=true)
                        where mid >= {lo} and mid <= {hi} order by mid, slot, txi, evi, filename, file_row_number""").fetchnumpy()
        mids = np.asarray(U["mid"], np.int64)
        U = {k: (np.ma.filled(a.astype(float), np.nan) if np.ma.isMaskedArray(a) and k in ("bt", "q", "b", "tok", "sol", "vq") else a) for k, a in U.items()}
        bounds = np.flatnonzero(np.diff(mids)) + 1
        starts, ends = np.concatenate([[0], bounds]), np.concatenate([bounds, [len(mids)]])
        for a, b in zip(starts, ends):
            mid = int(mids[a])
            if mid not in members:
                continue
            r = mcn.loc[mid]
            const = rf.MintConst(mid=mid, mbt=int(r.mbt), mslot=int(r.mslot), s0=int(r.s0), v=float(r.v), cbt=fnum(r.cbt), has_creator=not pd.isna(r.ch), nb=fnum(r.nb),
                                 ntr=fnum(r.ntr), bbuy=fnum(r.bbuy), bsell=fnum(r.bsell), cre_sell=fnum(r.cre_sell))
            sl = U["slot"][a:b]
            res = rf.mint_features(prep, const, None if pd.isna(r.ch) else r.ch, sl, U["bt"][a:b], U["isbuy"][a:b], U["sol"][a:b], U["tok"][a:b], U["q"][a:b],
                                   U["b"][a:b], U["th"][a:b], vq=U["vq"][a:b], seg_end=None, grads_rule="asof_pda_first_print", mint_filters="asof", return_state=True)
            try:  # report-only: an LP-count failure is recorded (report step lp_discontinuities), never raised into the deciding pass
                lpd = lp_discontinuities(U["isbuy"][a:b], U["tok"][a:b], U["b"][a:b], np.full(b - a, mid))
                for kk in rep["lp"]:
                    rep["lp"][kk] += lpd[kk]
            except Exception as exc:  # noqa: BLE001
                rep.setdefault("lp_error", f"{type(exc).__name__}: {exc}")
            if res is None:
                continue
            T = res.I[:, 1]
            sel = (T % MARK_S == 0) & (T >= w0) & (T <= last_entry)
            if not sel.any():
                continue
            bt0 = U["bt"][a:b]
            s0bt = float(bt0[0]) if np.isfinite(bt0[0]) else (float(bt0[np.isfinite(bt0)][0]) if np.isfinite(bt0).any() else float(r.mbt))
            rep["d3_kept_pre_filter"] += int(sel.sum())
            keep = sel & (T >= s0bt + D3_DROP_S)
            rep["d3_dropped"] += int((sel & ~keep).sum())
            rep["d3_keys"] |= {(mid, int(t_)) for t_ in T[sel & ~keep]}
            if not keep.any():
                continue
            rep["mints_with_rows"] += 1
            Tk, sdk = T[keep], res.I[keep, 2]
            s_dec = dates_s(Tk)
            st = res.state
            st_args = dict(sl=sl, isbuy=U["isbuy"][a:b], sol=U["sol"][a:b], tok=U["tok"][a:b], q=U["q"][a:b], b=U["b"][a:b], vq=U["vq"][a:b])
            tm = np.arange(((max(w0, int(r.mbt) + 600) + MARK_S - 1) // MARK_S) * MARK_S, min(e, int(r.mbt) + rl.HORIZON_S + 2 * MARK_S) + 1, MARK_S, dtype=np.int64)
            sm = dates_s(tm)
            cs = bk.clock_slot(tm, ckb, cks)
            S = bk.mint_states(**st_args, sd=sdk, slot_s_dec=s_dec, t_marks=tm, slot_s_marks=sm, clock_slot_marks=cs, lats=(*LATS, LAT_REPORT_LONG))
            R.mid.append(np.full(len(Tk), mid, np.int64))
            R.T.append(Tk)
            R.sd.append(sdk)
            R.X.append(res.X[keep])
            R.sdec.append(s_dec)
            R.spot.append(S["spot"])
            for lat in R.ent:
                if delta > 0 and lat in LATS:
                    Qe, Be, ssb, nb_ = entry_states_shifted(sl, st["QX"], st["BX"], st["ib"], np.asarray(U["sol"][a:b], float), sdk, s_dec, lat, delta)
                else:
                    Qe, Be, ssb, nb_ = S["Qe"][lat], S["Be"][lat], S["ssb"][lat], S["nearby"][lat]
                R.ent[lat].append((Qe, Be, ssb, nb_))
            R.marks.append((np.full(len(tm), mid, np.int64), tm, S["Qx"], S["Bx"]))
    return R, rep, con


def concat(parts: list, dtype=None) -> np.ndarray:
    return np.concatenate(parts) if parts else np.zeros(0, dtype or float)


def mark_lookup(marks: list, mid: np.ndarray, t1: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Qx, Bx at (mid, T1); NaN where the mint has no mark at T1 (an unpriced exit)."""
    if not marks or len(mid) == 0:
        return np.full(len(mid), np.nan), np.full(len(mid), np.nan)
    mm, tt, qx, bx = (np.concatenate([m[i] for m in marks]) for i in range(4))
    key = (mm.astype(np.int64) << 32) | tt.astype(np.int64)
    o = np.argsort(key, kind="stable")
    ks = key[o]
    kk = (np.asarray(mid, np.int64) << 32) | np.asarray(t1, np.int64)
    pos = np.minimum(np.searchsorted(ks, kk), len(ks) - 1)
    found = ks[pos] == kk
    j = o[pos]
    return np.where(found, qx[j], np.nan), np.where(found, bx[j], np.nan)


def price_variant(tr: Mapping[str, np.ndarray], lat: float, *, send: int, rent: int, auto: bool, e1_penalty: float, stake: int = FZ.STAKE_LAMPORTS) -> dict[str, Any]:
    """Deciding or report-only variant of one latency cell: no-fail, flat and pressure legs (intercept refit on this cell's fills). r4b_book.round_trip is called with its
    DEFAULT k_tol (C8 on, FZ.K_TOL); an invalid entry state and an invalid exit state (C2 state, C7 real-quote bound, C8 k-fall; a missing exit mark is NaN, so C2)
    are priced stranded (stake + buy send + the stranded rent of the mint's token program, no sell send) and flagged. The E1 charge is the larger of the haircut and `e1_penalty` per filled,
    non-stranded trip (0 = haircut only)."""
    Qe, Be, ssb, nb_ = (tr["ent"][lat][i] for i in range(4))
    stranded = tr["strand_class"]
    t22 = np.asarray(tr["rent_t22"], bool)
    rent_str = np.where(t22, RENT_STRANDED_T22, RENT_STRANDED_SPL)  # per trade, by the mint's token program
    if rent == 0:
        rent_fill = 0
    elif rent == FZ.RENT_REPORT:
        rent_fill = np.where(t22, RENT_REPORT_T22, RENT_REPORT_SPL)
    else:
        raise ValueError(f"price_variant: rent {rent} is neither the deciding 0 nor FZ.RENT_REPORT")
    stk = np.full(len(Qe), float(stake))
    kw = dict(stranded=stranded, auto_stranded=auto, rent_stranded=rent_str, return_stranded=True, return_invalid=True, return_exit_invalid=True, return_exit_cause=True)
    pnl, filled, st, bad, xbad, cause = bk.round_trip(stk, Qe, Be, tr["spot"], tr["Qx"], tr["Bx"], send, rent_fill, **kw)
    if e1_penalty > 0:
        pnl0 = bk.round_trip(stk, Qe, Be, tr["spot"], tr["Qx"], tr["Bx"], send, rent_fill, h_in=0.0, h_out=0.0, stranded=stranded, auto_stranded=auto, rent_stranded=rent_str)[0]
        hair = pnl0 - pnl
        extra = np.where(filled & ~st, np.maximum(0.0, e1_penalty - hair), 0.0)
        pnl = pnl - extra
    flat, press, c, p = bk.fail_legs(pnl, filled, ssb, nb_, send)
    return {"nofail": pnl, "flat": flat, "press": press, "filled": filled, "stranded": st, "intercept": c, "p": p, "entry_invalid": bad, "exit_invalid": xbad, "exit_cause": cause}


def cell_stats(pnl, T0, mid, w0: int, pv: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """book_stats of one leg; with pv, also entry_invalid, exit_invalid and exit_invalid_by_cause (C2 / C7 / C8) counts."""
    extra = {} if pv is None else dict(entry_invalid=pv["entry_invalid"], exit_invalid=pv["exit_invalid"], exit_cause=pv["exit_cause"])
    return bk.book_stats(pnl, T0, mid, w0=w0, **extra)


def gate(cells: Mapping[str, Mapping[str, Any]], alpha: float) -> dict[str, Any]:
    """Section 6.3 on all cells. Returns {'cells': {name: {item: bool}}, 'decision_p', 'pass'}."""
    out, ps = {}, []
    for name, s in cells.items():
        p = s.get("p_block")
        ps.append(1.0 if p is None or (isinstance(p, float) and math.isnan(p)) else float(p))
        out[name] = {
            "n": s.get("n", 0) >= GATE_N,
            "dates": s.get("dates", 0) >= GATE_DATES and bool(s.get("days_pos_majority")),
            "ci90_lo": (s.get("ci90_lo_sol") or 0) > 0,
            "ex_top3": s.get("ex_top3_sol", 0) > 0,
            "ex_best_block": s.get("ex_best_block_sol", 0) > 0,
            "ex_best_date": s.get("ex_best_date_sol", 0) > 0,
        }
    dp = max(ps) if ps else 1.0
    ok_items = all(all(v.values()) for v in out.values())
    return {"cells": out, "decision_p": dp, "alpha": alpha, "p_ok": dp <= alpha, "pass": bool(ok_items and dp <= alpha)}


def decide(cells: Mapping[str, Mapping[str, Any]], N: int, A: int, keys_ok: bool, press_gap: bool, alpha: float) -> dict[str, Any]:
    """PASS / FAIL / NOT_DECIDABLE (EXP 6.4, 7.1). NOT_DECIDABLE never comes from a seal; it comes from these structure conditions."""
    nd = []
    if N < MIN_BLOCKS:
        nd.append(f"{N} blocks hold an attempt (need {MIN_BLOCKS})")
    if A < MIN_ATTEMPTS:
        nd.append(f"{A} attempts (need {MIN_ATTEMPTS})")
    if any(s.get("n", 0) < GATE_N for s in cells.values()):
        nd.append(f"a cell has fewer than {GATE_N} trades")
    if not keys_ok:
        nd.append("decision keys are not a subset of the count job's live keys (or its live md5 differs)")
    if press_gap:
        nd.append("a counted trade lacks its pressure-leg P&L")
    g = gate(cells, alpha) if cells else {"pass": False, "decision_p": 1.0, "alpha": alpha, "cells": {}}
    if nd:
        return {"verdict": "NOT_DECIDABLE", "reasons": nd, "gate": g}
    return {"verdict": "PASS" if g["pass"] else "FAIL", "reasons": [], "gate": g}


def diverged_result(plan: Plan, fee: Mapping[str, Any], reasons: Sequence[str]) -> dict[str, Any]:
    return nd_result(plan, fee, ["count_build_divergence: " + r for r in reasons])


def nd_result(plan: Plan, fee: Mapping[str, Any], reasons: Sequence[str]) -> dict[str, Any]:
    """A NOT_DECIDABLE result that never opened an outcome row (late start, A3 halt) or whose build contradicted the count job."""
    return {"schema": SCHEMA, "verdict": "NOT_DECIDABLE", "reasons": list(reasons), "gate": {"pass": False, "decision_p": 1.0}, "N_blocks_with_attempts": 0,
            "A_attempts": 0, "decision_p": 1.0, "alpha": ALPHA_FAMILY / plan.k, "k": plan.k, "w0": ef.hour_key(plan.w0), "e": ef.hour_key(plan.e), "cells": {}, "fee_arm": dict(fee),
            "report_only": {}, "claim": "forward simulated paper; not money made; leads only to the DEC-018 path and the owner's yes"}


def dual_rows(scratch: Path, plan: Plan, deps: Deps, universe_min_mbt: int) -> tuple[list, list]:
    """(d) report-only, run AFTER the verdict is computed and printed: rows that exist only under the asof filters and only under the frozen (training) filters,
    hour marks in [W0, E - 1h]. Cannot alter the verdict (nothing it returns is read by the decision)."""
    import duckdb
    import pandas as pd

    work = scratch / "work"
    con = duckdb.connect()
    con.sql(f"SET memory_limit='{deps.memory_limit}'; SET threads={int(deps.threads)}; SET enable_progress_bar=false; SET preserve_insertion_order=true")
    ckb, cks = np.load(work / "clock_bt.npy"), np.load(work / "clock_slot.npy")
    mk = np.load(work / "mkt.npz")
    meta = pd.read_parquet(scratch / "meta.parquet")
    meta = meta[meta["mbt"] >= universe_min_mbt].reset_index(drop=True)
    mcn = pd.read_pickle(work / "mintconst.pkl").set_index("mid")
    prep = rf.build_prep(ckb, cks, mk["mnt"], {k: mk[k] for k in ("n_ps", "v_ps", "n_bd", "v_bd", "n_cr")}, meta["mbt"].to_numpy(np.int64), meta["s0"].to_numpy(np.float64))
    w0, e = int(plan.w0.timestamp()), int(plan.e.timestamp())
    members = set(meta["mid"].tolist())
    inwin = lambda T_: set(T_[(T_ % MARK_S == 0) & (T_ >= w0) & (T_ <= e - MARK_S)].tolist())  # noqa: E731
    ao, fo = [], []
    for lo, hi in ut_chunks(con, scratch):
        U = con.sql(f"""select mid, slot, txi, evi, bt, isbuy, sol, tok, q, b, th, vq from read_parquet('{scratch}/ut/*.parquet', filename=true, file_row_number=true)
                        where mid >= {lo} and mid <= {hi} order by mid, slot, txi, evi, filename, file_row_number""").fetchnumpy()
        mids = np.asarray(U["mid"], np.int64)
        U = {k: (np.ma.filled(a.astype(float), np.nan) if np.ma.isMaskedArray(a) and k in ("bt", "q", "b", "tok", "sol", "vq") else a) for k, a in U.items()}
        bounds = np.flatnonzero(np.diff(mids)) + 1
        for a, b in zip(np.concatenate([[0], bounds]), np.concatenate([bounds, [len(mids)]])):
            mid = int(mids[a])
            if mid not in members:
                continue
            r = mcn.loc[mid]
            const = rf.MintConst(mid=mid, mbt=int(r.mbt), mslot=int(r.mslot), s0=int(r.s0), v=float(r.v), cbt=fnum(r.cbt), has_creator=not pd.isna(r.ch), nb=fnum(r.nb),
                                 ntr=fnum(r.ntr), bbuy=fnum(r.bbuy), bsell=fnum(r.bsell), cre_sell=fnum(r.cre_sell))
            args = (prep, const, None if pd.isna(r.ch) else r.ch, U["slot"][a:b], U["bt"][a:b], U["isbuy"][a:b], U["sol"][a:b], U["tok"][a:b], U["q"][a:b], U["b"][a:b], U["th"][a:b])
            ra = rf.mint_features(*args, vq=U["vq"][a:b], seg_end=None, grads_rule="asof_pda_first_print", mint_filters="asof")
            rf_ = rf.mint_features(*args, vq=U["vq"][a:b], seg_end=None, grads_rule="frozen", mint_filters="frozen")
            Ta_ = inwin(ra.I[:, 1]) if ra is not None else set()
            Tf_ = inwin(rf_.I[:, 1]) if rf_ is not None else set()
            ao += [(mid, t_) for t_ in sorted(Ta_ - Tf_)]
            fo += [(mid, t_) for t_ in sorted(Tf_ - Ta_)]
    con.close()
    return ao, fo


def dual_report(ao: list, fo: list, tr_mid, tr_T0, cell_pnl) -> dict[str, Any]:
    asof_only = set(ao)
    entered = np.array([(int(m_), int(t_)) in asof_only for m_, t_ in zip(tr_mid, tr_T0)], bool)
    dual = {"asof_only_rows": len(ao), "frozen_only_rows": len(fo), "by_mark": {}, "by_date": {}, "asof_only_entered_top10": int(entered.sum()),
            "pnl_asof_only_trades": {}, "pnl_other_trades": {},
            "note": "report only, computed after E and after the verdict was printed; asof-only rows are NOT removed from the deciding book"}
    for key, lst in (("asof_only", ao), ("frozen_only", fo)):
        for m_, t_ in lst:
            dual["by_mark"].setdefault(str(t_), {"asof_only": 0, "frozen_only": 0})[key] += 1
            dual["by_date"].setdefault(str(bk.utc_dates([t_])[0]), {"asof_only": 0, "frozen_only": 0})[key] += 1
    for lat in LATS:
        for leg in ("flat", "press"):
            x = cell_pnl[lat][leg] / 1e9
            dual["pnl_asof_only_trades"][f"{leg}_{lat}"] = {"n": int(entered.sum()), "total_sol": float(x[entered].sum()), "mean_sol": float(x[entered].mean()) if entered.any() else None}
            dual["pnl_other_trades"][f"{leg}_{lat}"] = {"n": int((~entered).sum()), "total_sol": float(x[~entered].sum()), "mean_sol": float(x[~entered].mean()) if (~entered).any() else None}
    return dual


BLOCK_SUM_KEYS = ("block_sum_mean_sol", "block_sum_sd_sol", "t_block_sum", "p_block_sum")  # report-only (EXP 3.5): moved out of the deciding cells into the report file


@dataclass
class ReadCtx:
    """What the report-only steps need after the verdict is written. Owns the scratch dir (removed by close())."""

    scratch: Path
    plan: Plan
    fee: Mapping[str, Any]
    deps: Deps
    con: Any
    rep: dict
    tr: dict
    tr_mid: np.ndarray
    tr_T0: np.ndarray
    cell_pnl: dict
    cells_full: dict
    slot_s: dict
    e1p: float
    send: int
    lk: dict
    dkeys: dict
    mid_of: dict
    plan_hours: list
    rows_mid: np.ndarray
    rows_T: np.ndarray
    rows_score: np.ndarray

    def close(self) -> None:
        try:
            self.con.close()
        except Exception:  # noqa: BLE001
            pass
        if not self.deps.keep_scratch:
            shutil.rmtree(self.scratch, ignore_errors=True)


def clean_scratch(out_dir: Path) -> None:
    """The scratch dir is pinned (out_dir/scratch). An OOM-killed run skips every `finally`; the read only gets here when no READS line exists, so nothing of value is in it."""
    shutil.rmtree(Path(out_dir) / "scratch", ignore_errors=True)


def structure_precheck(paths: Paths, plan: Plan, deps: Deps) -> None:
    """R9 (QP rules Q2): BEFORE the lock, the structure-only build the count job repeats every hour, over the whole allowlist [H0, E + 3h) with trade_end E + 3h. It parses no side,
    amount or token column. A structure refusal here refuses the read (nothing spent); the same refusal after the lock is count_build_divergence."""
    out_dir = Path(paths.out_dir)
    scratch = out_dir / "scratch"
    if any(e.get("event") == "started" for e in reads_events(out_dir)):
        raise ReadRefused(["a started line exists: the scratch dir is not cleaned and nothing is re-run"])
    clean_scratch(out_dir)
    scratch.parent.mkdir(parents=True, exist_ok=True)
    end = ef.hour_key(ef.hour_dt(plan.hours[-1]) + timedelta(hours=1))
    walk = Path(paths.walk_dir)
    try:
        stats = rl.build(walk, plan.hours[0], end, scratch, v_source="event", canonical_fn=deps.canonical_fn, problems_fn=deps.problems_fn, verify=deps.verify,
                         memory_limit=deps.memory_limit, threads=deps.threads, tmp_dir=scratch / "tmp", structure_only=True, trade_end=end)
        table = deps.verify if deps.verify is not None else ef.verified_hours(walk, strict_bad_lines=True)
        _, probs = rc.structure_hour_lines(scratch, list(plan.hours), table, deps.sph_fn or rc.default_sph, walk, plan.count_cfg, stats)
    except rl.LayoutRefused as exc:
        raise ReadRefused(["structure pre-check (before the lock): " + r for r in exc.reasons]) from exc
    finally:
        if not deps.keep_scratch:
            shutil.rmtree(scratch, ignore_errors=True)
    if probs:
        raise ReadRefused(["structure pre-check (before the lock): " + r for r in probs])


def split_cells(cells: Mapping[str, Mapping[str, Any]]) -> tuple[dict, dict]:
    deciding = {n: {k: v for k, v in s.items() if k not in BLOCK_SUM_KEYS} for n, s in cells.items()}
    sums = {n: {k: s.get(k) for k in BLOCK_SUM_KEYS} for n, s in cells.items()}
    return deciding, sums


def run_deciding(paths: Paths, plan: Plan, fee: Mapping[str, Any], deps: Deps) -> tuple[dict[str, Any], ReadCtx | None]:
    """Post-lock: build, score, book, price, stats, decision. Returns (deciding result, ctx for the report-only steps). Nothing is written except scratch. A failure here raises
    (the caller writes `aborted`); a build that contradicts counts.jsonl returns a NOT_DECIDABLE count_build_divergence result and no ctx. Nothing report-only runs here
    except the counters that fall out of the feature pass; every report-only computation is a ctx step."""
    out_dir = Path(paths.out_dir)
    scratch = out_dir / "scratch"
    shutil.rmtree(scratch, ignore_errors=True)
    w0, e = int(plan.w0.timestamp()), int(plan.e.timestamp())
    slot_s = slot_s_by_date(plan.sph)
    con = None
    try:
        try:
            stats = rl.build(Path(paths.walk_dir), plan.hours[0], ef.hour_key(ef.hour_dt(plan.hours[-1]) + timedelta(hours=1)), scratch, v_source="event", canonical_fn=deps.canonical_fn,
                             problems_fn=deps.problems_fn, verify=deps.verify, memory_limit=deps.memory_limit, threads=deps.threads, tmp_dir=scratch / "tmp",
                             trade_end=ef.hour_key(ef.hour_dt(plan.hours[-1]) + timedelta(hours=1)))
        except rl.LayoutRefused as exc:
            raise CountBuildDivergence(exc.reasons) from exc
        struct_probs = []
        if stats.get("v0_missing_days_over"):
            struct_probs.append(f"v0-missing share above {rl.V0_MISSING_REFUSE_SHARE} on {stats['v0_missing_days_over']}")
        if stats.get("mixed_null_txi_slots"):
            struct_probs.append(f"{stats['mixed_null_txi_slots']} mixed null / non-null tx_index (mint, slot) pair(s)")
        if struct_probs:
            raise CountBuildDivergence(struct_probs)
        R, rep, con = build_rows(scratch, plan, fee, slot_s, deps, rc.UNIVERSE_MIN_MBT)
        boosters = deps.boosters_fn(str(paths.models_dir) if paths.models_dir else None)
        mid = concat(R.mid, np.int64)
        T = concat(R.T, np.int64)
        sd = concat(R.sd, np.int64)
        X = np.vstack(R.X) if R.X else np.zeros((0, FZ.N_FEATURES), np.float32)
        score = rs.bag_score(boosters, X) if len(X) else np.zeros(0)
        # (b) per count mark: recompute the live set exactly as r4b_count does, compare live_md5 / live_tokens / decision_rows, decision keys must be a subset
        marks = sorted({(int(ln["mark_t"]), int(ln["sd"])) for ln in plan.counts})
        meta = [m for m in con.sql(f"select mid, mint, mbt from read_parquet('{scratch}/meta.parquet')").fetchall() if m[2] >= rc.UNIVERSE_MIN_MBT]
        mint_of = {m[0]: m[1] for m in meta}
        mid_of = {m[1]: m[0] for m in meta}
        lk = live_keys(scratch, marks, mint_of, {m[0]: m[2] for m in meta}, con)
        dkeys: dict[int, set] = {}
        for mi, t in zip(mid.tolist(), T.tolist()):
            dkeys.setdefault(t, set()).add(mint_of[mi])
        by_hour = {ln["mark_t"]: ln for ln in plan.counts}
        notsub = sorted(t for t, ks in dkeys.items() if not ks <= set(lk.get(t, {})))
        md5_bad = [t for t in lk if rc.keys_md5([(m_, t) for m_ in lk[t]]) != by_hour[t]["live_md5"] or len(lk[t]) != by_hour[t]["live_tokens"]
                   or sum(1 for n_ in lk[t].values() if n_ >= rc.MIN_PRINTS_DECISION) != by_hour[t]["decision_rows"]]
        keys_ok = not notsub and not md5_bad
        # the book
        bkr = bk.xs_book(T, mid, score)
        t1 = np.where(bkr["reason"] == "end", e, bkr["T1"])
        row = bkr["row"]
        tr_mid, tr_T0 = bkr["mid"], bkr["T0"]
        Qx, Bx = mark_lookup(R.marks, tr_mid, t1)
        traded = [mint_of[int(m)] for m in tr_mid]
        missing = sorted({m for m in traded if m not in plan.strand})  # R11: a mint absent from the record is strand-class, and counted
        strand_class = np.array([plan.strand.get(m, True) for m in traded], bool)
        rent_t22 = np.array([plan.rent_t22.get(m, True) for m in traded], bool)  # unknown program: the higher rent
        tr = {"ent": {lat: [concat([p[i] for p in R.ent[lat]])[row] for i in range(4)] for lat in R.ent}, "spot": concat(R.spot)[row], "Qx": Qx, "Bx": Bx, "strand_class": strand_class, "rent_t22": rent_t22}
        send = int(fee["send"])
        e1p = float(plan.e1["penalty_lamports"])
        dec_kw = dict(send=send, rent=FZ.RENT_DECIDING, auto=True)
        cells_full, cell_pnl = {}, {}
        for lat in LATS:
            pv = price_variant(tr, lat, e1_penalty=e1p, **dec_kw)
            cell_pnl[lat] = pv
            for leg in ("flat", "press"):
                cells_full[f"{leg}_{lat}"] = cell_stats(pv[leg], tr_T0, tr_mid, w0, pv)
        cells, _ = split_cells(cells_full)
        press_gap = any(not np.isfinite(cell_pnl[lat]["press"]).all() for lat in LATS)
        blocks_with = int(len(np.unique((tr_T0 - w0) // BLOCK_S))) if len(tr_T0) else 0
        A = int(len(tr_T0))
        alpha = ALPHA_FAMILY / plan.k
        dec = decide(cells, blocks_with, A, keys_ok, press_gap, alpha)
        if deps.now_fn() >= plan.deadline:  # R3: the clock is checked again when the verdict is formed
            dec = {**dec, "verdict": "NOT_DECIDABLE", "reasons": [f"deadline: the verdict was formed at or after E + {DEADLINE_HOURS} h ({iso(plan.deadline)})"] + list(dec["reasons"])}
        stranded = {f"{leg}_{lat}": int(cell_pnl[lat]["stranded"].sum()) for lat in LATS for leg in ("flat",)}
        res = {
            "schema": SCHEMA, "verdict": dec["verdict"], "reasons": dec["reasons"], "gate": dec["gate"], "N_blocks_with_attempts": blocks_with, "A_attempts": A,
            "decision_p": dec["gate"]["decision_p"], "alpha": alpha, "k": plan.k, "w0": ef.hour_key(plan.w0), "e": ef.hour_key(plan.e), "cells": cells,
            "fee_arm": dict(fee), "e1": plan.e1, "stack": {"send": send, "rent": FZ.RENT_DECIDING, "auto_stranded": True, "latencies": list(LATS), "k_rounding": FZ.K_ROUNDING},
            "sell_builder": plan.sell_builder, "walk_dir": str(Path(paths.walk_dir).resolve()),
            "keys": {"marks": len(marks), "not_subset_marks": len(notsub), "live_md5_mismatch_marks": len(md5_bad), "ok": keys_ok},
            "d3": {"dropped_rows": rep["d3_dropped"], "kept_pre_filter": rep["d3_kept_pre_filter"]}, "stranded_counts": stranded,
            "strand_class_trades": int(strand_class.sum()), "mints": rep["mints"], "mints_with_rows": rep["mints_with_rows"],
            "mint_accounts": {**plan.mint_info, "traded_mints": len(set(traded)), "traded_mints_missing_from_record": len(missing), "traded_trades_on_missing_mints": int(sum(1 for m in traded if m in set(missing)))},
            "report_only": {"files": "report-<step>.json and report-only.json in the out dir, written after this file; an error in a step is recorded there and never changes this file",
                            "steps": [n for n, _ in REPORT_STEP_NAMES]},
            "claim": "forward simulated paper; not money made; leads only to the DEC-018 path and the owner's yes",
        }
        ctx = ReadCtx(scratch, plan, fee, deps, con, rep, tr, tr_mid, tr_T0, cell_pnl, cells_full, slot_s, e1p, send, lk, dkeys, mid_of, list(plan.hours), mid, T, score)
        return res, ctx
    except CountBuildDivergence as exc:
        if con is not None:
            con.close()
        if not deps.keep_scratch:
            shutil.rmtree(scratch, ignore_errors=True)
        return diverged_result(plan, fee, exc.reasons), None
    except BaseException:
        if con is not None:
            con.close()
        if not deps.keep_scratch:
            shutil.rmtree(scratch, ignore_errors=True)
        raise


# ---- report-only steps: each runs alone, in its own try/except, and writes its own file --------------------------------------------------------------
REPORT_STEP_NAMES = (("k_ratio_near_tolerance", None), ("null_txi_ties", None), ("rent", None), ("no_auto_stranded", None), ("send_55k", None), ("lat4.0", None), ("block_sums", None), ("lp_discontinuities", None),
                     ("null_block_time", None), ("gap_table", None), ("asof_vs_frozen", None))


def report_steps(c: ReadCtx) -> list[tuple[str, Callable[[], Any]]]:
    w0 = int(c.plan.w0.timestamp())

    def variant(kw: dict, lats: Sequence[float] = LATS) -> Callable[[], Any]:
        def go() -> Any:
            out = {}
            for lat in lats:
                pv = price_variant(c.tr, lat, e1_penalty=c.e1p, **kw)
                for leg in ("flat", "press"):
                    out[f"{leg}_{lat}"] = cell_stats(pv[leg], c.tr_T0, c.tr_mid, w0, pv)
            return out
        return go

    def block_sums() -> Any:
        return split_cells(c.cells_full)[1]

    def lp() -> Any:
        if "lp_error" in c.rep:
            raise RuntimeError(c.rep["lp_error"])
        return c.rep["lp"]

    def null_bt() -> Any:
        out = {}
        for h in c.plan_hours:
            f = c.scratch / "ut" / f"{h}.parquet"
            if f.exists():
                out[h] = int(c.con.sql(f"select count(*) from read_parquet('{f}') where bt is null").fetchone()[0])
        return out

    def gap_table() -> Any:
        e = int(c.plan.e.timestamp())
        gap = {}
        for t in sorted(c.lk):
            if w0 <= t <= e - MARK_S:
                g = gap_by_cause(c.lk[t], c.dkeys.get(t, set()), c.rep["d3_keys"], c.mid_of, t)
                gap[ef.hour_key(datetime.fromtimestamp(t, tz=timezone.utc))] = {"live": len(c.lk[t]), "decision_rows": len(c.dkeys.get(t, ())), "gap": len(c.lk[t]) - len(c.dkeys.get(t, ())), **g}
        return gap

    def k_ratio() -> Any:
        out = {}
        for lat in LATS:
            pv = c.cell_pnl[lat]
            Qe, Be = np.asarray(c.tr["ent"][lat][0], float), np.asarray(c.tr["ent"][lat][1], float)
            with np.errstate(all="ignore"):
                r = (np.asarray(c.tr["Qx"], float) * np.asarray(c.tr["Bx"], float)) / (Qe * Be)
            bought = pv["filled"] & ~pv["entry_invalid"] & np.isfinite(r)
            near = bought & (r >= K_RATIO_NEAR_LO) & (r < K_RATIO_NEAR_HI)
            out[str(lat)] = {"exits_in_window": int(near.sum()), "bought_valid_entry_exits": int(bought.sum()), "window": [K_RATIO_NEAR_LO, K_RATIO_NEAR_HI],
                             "c8_flagged": int((pv["exit_cause"] == 8).sum())}
        return out

    def null_txi_ties() -> Any:
        return null_txi_tie_report(c)

    def dual() -> Any:
        ao, fo = dual_rows(c.scratch, c.plan, c.deps, rc.UNIVERSE_MIN_MBT)
        return dual_report(ao, fo, c.tr_mid, c.tr_T0, c.cell_pnl)

    return [("k_ratio_near_tolerance", k_ratio), ("null_txi_ties", null_txi_ties), ("rent", variant(dict(send=c.send, rent=FZ.RENT_REPORT, auto=True))),
            ("no_auto_stranded", variant(dict(send=c.send, rent=FZ.RENT_DECIDING, auto=False))),
            ("send_55k", variant(dict(send=FZ.SEND_FEE_REPORT, rent=FZ.RENT_DECIDING, auto=True))),
            ("lat4.0", variant(dict(send=c.send, rent=FZ.RENT_DECIDING, auto=True), (LAT_REPORT_LONG,))),
            ("block_sums", block_sums), ("lp_discontinuities", lp), ("null_block_time", null_bt), ("gap_table", gap_table),
            ("asof_vs_frozen", dual)]  # the doubled feature pass is last


def null_txi_tie_report(c: ReadCtx) -> dict[str, Any]:
    """QP #681 (report-only, never removes a mint or a trade), per UTC date of [W0, E]: prints with a null tx_index (txi < 0) on member pools; content tie groups (same mid, slot,
    txi, evi with more than one distinct (isbuy, sol, tok, q, b, th)) and the mints that have one; and, on the mints with a tie group anywhere in the window, the decision rows,
    the top-10 picks (per mark: the 10 best scores, ties to the lower mint id) and the book attempts, by entry date. A print's date is its slot's clock time."""
    w0, e = int(c.plan.w0.timestamp()), int(c.plan.e.timestamp())
    work = c.scratch / "work"
    ckb, cks = np.load(work / "clock_bt.npy"), np.load(work / "clock_slot.npy")
    members = sorted(set(c.mid_of.values()))
    con = c.con
    con.sql("create or replace temp table memb(mid bigint)")
    con.executemany("insert into memb values (?)", [(m,) for m in members])
    ut = f"read_parquet('{c.scratch}/ut/*.parquet')"

    def day_of(slots: np.ndarray):
        t = ckb[np.minimum(np.searchsorted(cks, slots, "left"), len(cks) - 1)]
        keep = (t >= w0) & (t <= e)
        return keep, np.array([str(d) for d in bk.utc_dates(t)]) if len(t) else np.array([], str)

    out: dict[str, dict[str, int]] = {}

    def cell(d: str) -> dict[str, int]:
        return out.setdefault(d, {"null_txi_prints": 0, "tie_groups": 0, "tie_mints": 0, "decision_rows_on_tie_mints": 0, "top10_picks_on_tie_mints": 0, "attempts_on_tie_mints": 0})

    nul = con.sql(f"select slot, count(*) as n from {ut} where txi < 0 and mid in (select mid from memb) group by slot").fetchnumpy()
    if len(nul["slot"]):
        keep, days = day_of(np.asarray(nul["slot"], np.int64))
        for d, n in zip(days[keep], np.asarray(nul["n"])[keep]):
            cell(str(d))["null_txi_prints"] += int(n)
    ties = con.sql(f"""select mid, slot from (select mid, slot, txi, evi from {ut} where mid in (select mid from memb) group by mid, slot, txi, evi
                       having count(*) > 1 and count(distinct (isbuy, sol, tok, q, b, th)) > 1)""").fetchnumpy()
    tie_mints: set[int] = set()
    if len(ties["mid"]):
        slots, mids = np.asarray(ties["slot"], np.int64), np.asarray(ties["mid"], np.int64)
        keep, days = day_of(slots)
        per_day: dict[str, set] = {}
        for d, m in zip(days[keep], mids[keep]):
            cell(str(d))["tie_groups"] += 1
            per_day.setdefault(str(d), set()).add(int(m))
            tie_mints.add(int(m))
        for d, ms in per_day.items():
            cell(d)["tie_mints"] = len(ms)
    tm = np.array(sorted(tie_mints), np.int64)
    rm, rT, rs_ = np.asarray(c.rows_mid, np.int64), np.asarray(c.rows_T, np.int64), np.asarray(c.rows_score, float)
    if len(rm):
        o = np.lexsort((rm, -rs_, rT))
        Ts = rT[o]
        first = np.r_[0, np.flatnonzero(np.diff(Ts)) + 1]
        rank = np.arange(len(o)) - np.repeat(first, np.diff(np.r_[first, len(o)]))
        top = np.zeros(len(rm), bool)
        top[o[rank < 10]] = True
        on_tie = np.isin(rm, tm)
        rdays = np.array([str(x) for x in bk.utc_dates(rT)])
        for d in np.unique(rdays):
            sel = rdays == d
            cell(str(d))["decision_rows_on_tie_mints"] += int((sel & on_tie).sum())
            cell(str(d))["top10_picks_on_tie_mints"] += int((sel & on_tie & top).sum())
    if len(c.tr_mid):
        on_tie_t = np.isin(np.asarray(c.tr_mid, np.int64), tm)
        dts = np.array([str(x) for x in bk.utc_dates(c.tr_T0)])
        for d in np.unique(dts):
            cell(str(d))["attempts_on_tie_mints"] += int(((dts == d) & on_tie_t).sum())
    return {"by_date": out, "tie_mints_total": int(len(tm)), "note": "report only; no mint or trade is removed; top-10 picks are recomputed per mark from the scores (ties to the lower mint id)"}


def run_report_only(out_dir: Path, c: ReadCtx, token: str, steps: Sequence[tuple[str, Callable[[], Any]]] | None = None) -> dict[str, Any]:
    """After `completed`. Every step is its own try/except. A step writes report-<name>.json (O_EXCL, atomic) with its data or its error, a `report_completed` /
    `report_failed` ledger line, and an entry in report-only.json. Nothing here can change the deciding result or its ledger line."""
    index: dict[str, Any] = {}
    for name, fn in (steps if steps is not None else report_steps(c)):
        fname = "report-" + name.replace(".", "_") + ".json"
        try:
            data = fn()
            write_once(out_dir / fname, {"schema": SCHEMA + "_report", "step": name, "token": token, "status": "ok", "data": data})
            index[name] = {"status": "ok", "file": fname}
            ledger_event(out_dir, "report_completed", token=token, step=name, file=fname)
        except Exception as exc:  # noqa: BLE001 - report-only: recorded, never raised
            err = f"{type(exc).__name__}: {exc}"
            index[name] = {"status": "failed", "error": err, "file": None}
            try:
                write_once(out_dir / fname, {"schema": SCHEMA + "_report", "step": name, "token": token, "status": "failed", "error": err})
                index[name]["file"] = fname
            except Exception:  # noqa: BLE001
                pass
            try:
                ledger_event(out_dir, "report_failed", token=token, step=name, error=err)
            except Exception:  # noqa: BLE001
                pass
    try:
        write_once(out_dir / "report-only.json", {"schema": SCHEMA + "_report_index", "token": token, "report_only": index})
    except Exception:  # noqa: BLE001
        pass
    return index


# ---- orchestration ------------------------------------------------------------------------------------------------------------------------
def emit_verdict(res: Mapping[str, Any], deps: Deps) -> None:
    c = res.get("cells", {})
    lines = [f"VERDICT: {res['verdict']}  (decision p {res.get('decision_p')} vs alpha {res.get('alpha')}; N blocks {res.get('N_blocks_with_attempts')}, A {res.get('A_attempts')})"]
    lines += [f"  reason: {r}" for r in res.get("reasons", [])]
    for name, s in c.items():
        lines.append(f"  {name}: n {s.get('n')} mean_sol {s.get('mean_sol')} ci90_lo {s.get('ci90_lo_sol')} ex_top3 {s.get('ex_top3_sol')} p_block {s.get('p_block')}")
    lines.append("  " + res["claim"])
    deps.stderr("\n".join(lines))


def write_once(path: Path, obj: Any) -> Path:
    """Atomic, no-overwrite write (Rev1). Serialize FIRST (an unserializable value raises before any file exists), write a temp file in the same dir, fsync it, then os.link it to
    the final name (fails with FileExistsError if the name exists: O_EXCL semantics), drop the temp name and fsync the dir. The final name is never half-written."""
    path = Path(path)
    text = json.dumps(obj, sort_keys=True, indent=1, default=_json_default) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o444)
        os.link(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    return path


def write_result(out_dir: Path, res: Mapping[str, Any]) -> Path:
    return write_once(Path(out_dir) / RESULT, res)


def read(paths: Paths, deps: Deps | None = None) -> dict[str, Any]:
    """The whole read. Raises ReadRefused (nothing written) on a preflight or structure-precheck refusal. After the lock, an exception before `completed` writes an `aborted` line
    and re-raises. Order after the lock: verdict to stderr -> deciding result file -> `completed` -> report-only steps (each isolated; they cannot undo the above)."""
    deps = deps or Deps()
    out_dir = Path(paths.out_dir)
    started = deps.now_fn()
    plan, fee, reasons = preflight(paths, deps, started)
    if reasons or plan is None:
        raise ReadRefused(reasons or ["preflight failed"])
    structure_precheck(paths, plan, deps)  # R9: refuses (nothing spent) on any structure refusal over [H0, E + 3h)
    token = take_lock(out_dir)
    ledger_event(out_dir, "started", token=token, head=deps.head_fn(), job=os.environ.get("MISCUSI_JOB_ID", ""), w0=ef.hour_key(plan.w0), e=ef.hour_key(plan.e), k=plan.k,
                 send=fee["send"], fee_arm=fee.get("source"), code_sha256=plan.v and {m: plan.v[f"EXP027_SHA256_{m.upper()}"] for m in CODE_MODULES}, inputs=plan.inputs,
                 sell_builder=plan.sell_builder, walk_dir=str(Path(paths.walk_dir).resolve()))
    ctx: ReadCtx | None = None
    try:
        early = []
        if started >= plan.deadline:
            early.append(f"deadline: the read started at {iso(started)}, at or after E + {DEADLINE_HOURS} h ({iso(plan.deadline)}); no outcome row was opened")
        if plan.a3_halts:
            early.append(f"a3_halt: {len(plan.a3_halts)} structure-monitor halt(s) inside [W0, E] (EXP 7.1.6): {plan.a3_halts[:3]}; no outcome row was opened")
        if early:
            res = nd_result(plan, fee, early)
        else:
            res, ctx = run_deciding(paths, plan, fee, deps)
        res["token"] = token
        emit_verdict(res, deps)  # the verdict is printed BEFORE any result file
        rp = write_result(out_dir, res)
        ledger_event(out_dir, "completed", token=token, verdict=res["verdict"], result=str(rp), result_sha256=sha256_file(rp), N=res["N_blocks_with_attempts"], A=res["A_attempts"])
    except BaseException as exc:  # noqa: BLE001 - the read is spent whatever happened
        if ctx is not None:
            ctx.close()
        reasons = exc.reasons if isinstance(exc, ReadRefused) else [f"{type(exc).__name__}: {exc}"]
        ledger_event(out_dir, "aborted", token=token, reasons=reasons)
        raise
    try:
        if ctx is not None:
            res["report_only_index"] = run_report_only(out_dir, ctx, token)  # in memory only; the file on disk is already final
    finally:
        if ctx is not None:
            ctx.close()
    return res


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--walk-dir", default=PINNED_WALK_DIR, help=f"Forward walk 2 dataset dir; anything but {PINNED_WALK_DIR} is refused")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT), help="READS.jsonl, READ.lock, result file")
    ap.add_argument("--counts-dir", default=str(DEFAULT_OUT), help="the count job's dir (counts.jsonl, config.json)")
    ap.add_argument("--memory-limit", default="6GB")
    ap.add_argument("--threads", type=int, default=4)
    a = ap.parse_args(argv)  # no flag changes a pinned value; the EXP file and the models dir are repo constants
    paths = Paths(Path(a.walk_dir), Path(a.out_dir), Path(a.counts_dir))
    deps = Deps(memory_limit=a.memory_limit, threads=a.threads)
    try:
        res = read(paths, deps)
    except ReadRefused as exc:
        print("REFUSED: " + " | ".join(exc.reasons), file=sys.stderr)
        return 2
    except rl.LayoutRefused as exc:
        print("ABORTED (layout): " + " | ".join(exc.reasons), file=sys.stderr)
        return 3
    return 0 if res["verdict"] in ("PASS", "FAIL", "NOT_DECIDABLE") else 1


if __name__ == "__main__":
    raise SystemExit(main())
