#!/usr/bin/env python3
"""C1-NF paper shadow: a keyless process that logs picks and paper outcomes from the fast-0 getBlock tip tape.

Rule (EXP-025 section 2, RULE.md, JUDGE-4 3.3). At every whole UTC minute T from graduation + 600 s to + 24 h, for each canonical PumpSwap pool
that printed in the last 60 min, the feature engine (tools/c1nf_features.py) builds the 107 stage-2 features from canonical-pool prints with
slot < SD, SD = the first slot whose block_time >= T. Then: LAYA stage 1, the pinned LightGBM (pred > 0.02), the cap h_top1 <= 0.5 (NaN
dropped), the one-position-per-mint book (re-entry 60 s after the exit). A pick buys 0.25 SOL at SD + 1.3 s (1.9 s binding) and sells 300 s
after the landing.

Outputs (OUT dir, hourly JSONL, strict JSON):
    c1nf-picks-<hour>.jsonl     c1nf_pick       one per pick; the executor parses these (PICK_FIELDS, PICK_EXAMPLE)
    c1nf-outcomes-<hour>.jsonl  c1nf_outcome    paper fills per pick: entries 1.3 / 1.9 / 3.0 / 4.0 s, END and WORST bounds, exit +300 s
    c1nf-events-<hour>.jsonl    c1nf_gap, c1nf_heartbeat, c1nf_start, c1nf_stop
    status.json                 last heartbeat. errors.log: tracebacks (capped).

Pricing. PumpSwap rows are PRE-trade. Price = (vault quote + V) / base with the print's own V (tip rows carry `virtual_quote_reserve`).
Fill arithmetic is RULE.md's: fee tier on (Q, B), own trade applied, guard 1.15 x the decision spot, 55,000 / 505,000 lamports per send,
rent 2,039,280 per fill as report-only variants. Fail legs (flat / pressure) are a book statistic and are not applied here.

Seals.
  * EXP-022 section 9, from 2026-10-16T01Z: a pool whose first print is at or after that instant is followed only when a boolean pick oracle
    answers exactly False for its mint. Missing oracle, an error, a non-bool, a stale oracle: withheld (fail closed). A withheld pool gets NO
    per-pool record of any kind (no pick, no outcome, no reason label): it never enters the book and is counted only in the unlabelled
    aggregate `withheld` of the heartbeat and stop records. The oracle is read as a boolean only. Same approach as tools/h5_shadow.py `_sealed`
    (newest rule: no per-pool record for sealed pools).
  * EXP-025 section 5.1 declared observation: the shadow may log outcomes for decisions inside the counted window. It is not the read.

Paper only. No key, no transaction, no RPC. It reads only the tip tape (live) or one exploration day of /data/mal/audit-1008/tape (replay).
"""

from __future__ import annotations

import argparse
import bisect
import collections
import hashlib
import importlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Optional, Sequence

import numpy as np

from tools.paper_curve_math import pumpswap_sol_fee_ppm

SCHEMA = "c1nf_shadow_v1"
RULE_ID = "C1-NF EXP-025"
WSOL = "So11111111111111111111111111111111111111112"
N_FEATURES = 107
V_LO, V_HI = 17.5e9, 17.7e9
PRED_MIN = 0.02                      # buy when pred > 0.02 (strict)
H_TOP1_MAX = 0.5                     # cap; h_top1 <= 0.5 stays, NaN dropped (ARTIFACTS/exp025/c1nf_cap.py)
REENTRY_S = 60
STAKE_LAMPORTS = 250_000_000
GUARD = 1.15
EXIT_S = 300.0
EXIT_LAG_S = 0.55
ENTRY_LATS = (1.3, 1.9, 3.0, 4.0)    # 1.3 primary, 1.9 binding, 3.0 and 4.0 report-only
PRIMARY_LAT = 1.3
SEND_FEES = {"55k": 55_000, "505k": 505_000}
RENT_LAMPORTS = 2_039_280
LP_FRAC = 0.002                      # post-state estimate only when no later print exists
GRACE_S = 12                         # stream seconds past the last exit before an outcome is closed
DEFAULT_SPS = 0.4
SPS_WINDOW_S = 600
SPS_MIN_SPAN_S = 60
SILENCE_S = 20.0
CLOCK_JUMP_S = 60
HEARTBEAT_S = 60.0
HOLD_MS = 600                        # live: feed rows only once t_recv_ms is this old, so the three kinds of one block arrive together
SEAL_START_MS = int(datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc).timestamp() * 1000)
ORACLE_STALE_S = 60.0
DEFAULT_TIP_DIR = "/var/lib/mal/sealed/fast-trades-tip"
DEFAULT_OUT_DIR = os.path.join(os.path.expanduser("~"), "data", "c1nf-shadow")
TAPE_DIR = "/data/mal/audit-1008/tape"
TOKENS_PARQUET = "/data/mal/hunt-shared/tokens.parquet"
ERRORS_MAX_BYTES = 5_000_000

EXIT_OK, EXIT_USAGE, EXIT_REFUSED = 0, 2, 3

# ---- pick schema (the executor parses these) -----------------------------------------------------------------------------------------------
PICK_FIELDS = ("type", "mint", "pool", "decision_T_ms", "SD_slot", "pred", "h_top1", "stage1", "feature_hash", "model_sha")
PICK_TYPES = {"type": str, "mint": str, "pool": str, "decision_T_ms": int, "SD_slot": int, "pred": float, "h_top1": float, "stage1": bool,
              "feature_hash": str, "model_sha": str}
PICK_ENVELOPE = ("schema", "t_ms")   # added to every record by the sink path; the executor may ignore them
PICK_EXAMPLE = {
    "type": "c1nf_pick", "mint": "DxkqpagQamHXMVhS2GC7fni4qugWp5fcw28chKfUpump", "pool": "5N4CLYwiyx7AWtUzPhErC97bJX62aQbCpBz2CCuFc2Bm",
    "decision_T_ms": 1788523800000, "SD_slot": 444240500, "pred": 0.0431, "h_top1": 0.12, "stage1": True,
    "feature_hash": "0" * 64, "model_sha": "f" * 64,
}
OUTCOME_FIELDS = ("type", "mint", "pool", "decision_T_ms", "SD_slot", "complete", "legs")
EVENT_TYPES = ("c1nf_gap", "c1nf_heartbeat", "c1nf_start", "c1nf_stop")


class Refused(RuntimeError):
    """Bad or unsafe input. Nothing is started. Exit 3."""


def validate_pick(rec: Mapping[str, Any]) -> list[str]:
    """Problems with a pick record (empty list = valid). Types are strict: bool is not an int, NaN is not a float."""
    bad = []
    for k in PICK_FIELDS:
        if k not in rec:
            bad.append(f"missing {k}")
            continue
        v, want = rec[k], PICK_TYPES[k]
        if want is int and (isinstance(v, bool) or not isinstance(v, int)):
            bad.append(f"{k}: not an int")
        elif want is float and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v))):
            bad.append(f"{k}: not a finite number")
        elif want in (str, bool) and not isinstance(v, want):
            bad.append(f"{k}: not a {want.__name__}")
    if rec.get("type") != "c1nf_pick":
        bad.append("type != c1nf_pick")
    for k in ("feature_hash", "model_sha"):
        v = rec.get(k)
        if isinstance(v, str) and (len(v) != 64 or any(c not in "0123456789abcdef" for c in v)):
            bad.append(f"{k}: not 64 hex chars")
    return bad


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def hour_of(t_s: float) -> str:
    return datetime.fromtimestamp(int(t_s), tz=timezone.utc).strftime("%Y-%m-%dT%H")


def day_of(t_s: float) -> str:
    return datetime.fromtimestamp(int(t_s), tz=timezone.utc).strftime("%Y-%m-%d")


def clean(obj: Any) -> Any:
    """JSON-safe copy: NaN / inf become None, numpy scalars become Python numbers."""
    if isinstance(obj, (np.floating, np.integer, np.bool_)):
        obj = obj.item()
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    return obj


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def feature_hash(vec: Any) -> str:
    return hashlib.sha256(np.asarray(vec, dtype="<f4").tobytes()).hexdigest()


# ---- pinned model ----------------------------------------------------------------------------------------------------------------------------
class ModelSet:
    """The pinned stage-2 model(s). EXP-025 retrains daily (decision day D trains on rows before D 00:00Z - 3600 s), so a manifest may hold
    several files: entries {from_day, file, sha256}; the model for decision day D is the latest entry with from_day <= D. Every file's sha256 is
    checked at construction; a mismatch refuses to start. `loader(path) -> object with predict(X)` is injectable (tests use a stub)."""

    def __init__(self, entries: Sequence[Mapping[str, str]], loader: Optional[Callable[[str], Any]] = None, base: Optional[Path] = None) -> None:
        if not entries:
            raise Refused("no model entries")
        self.entries: list[dict] = []
        loader = loader or _lgb_loader
        for e in sorted(entries, key=lambda e: e.get("from_day", "")):
            path = Path(e["file"])
            if base is not None and not path.is_absolute():
                path = base / path
            want = str(e["sha256"]).lower()
            if len(want) != 64:
                raise Refused(f"{path}: sha256 pin is not 64 hex chars")
            if not path.is_file():
                raise Refused(f"{path}: model file missing")
            got = sha256_file(path)
            if got != want:
                raise Refused(f"{path}: sha256 {got} != pinned {want}")
            self.entries.append({"from_day": e.get("from_day", "0000-00-00"), "sha": got, "model": loader(str(path)), "file": str(path)})

    @classmethod
    def single(cls, path: str, sha256: str, loader: Optional[Callable[[str], Any]] = None) -> "ModelSet":
        return cls([{"from_day": "0000-00-00", "file": path, "sha256": sha256}], loader)

    @classmethod
    def from_manifest(cls, path: str | Path, loader: Optional[Callable[[str], Any]] = None) -> "ModelSet":
        p = Path(path)
        try:
            doc = json.loads(p.read_text())
            entries = doc["models"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise Refused(f"{p}: unreadable model manifest ({type(exc).__name__})") from exc
        return cls(entries, loader, base=p.parent)

    def for_day(self, day: str) -> tuple[Any, str]:
        """(model, sha) for the decision day. A day before the first entry has no model: Refused (no pick without a pinned model)."""
        i = bisect.bisect_right([e["from_day"] for e in self.entries], day) - 1
        if i < 0:
            raise Refused(f"no pinned model for {day}")
        e = self.entries[i]
        return e["model"], e["sha"]

    @property
    def shas(self) -> list[str]:
        return [e["sha"] for e in self.entries]


def _lgb_loader(path: str) -> Any:
    import lightgbm as lgb  # lazy: not every venv has it

    return lgb.Booster(model_file=path)


# ---- pool arithmetic ------------------------------------------------------------------------------------------------------------------------
def tier_fee(q: float, b: float) -> float:
    """Canonical PumpSwap total fee on the market cap of a (Q incl. V, B) state, as a fraction."""
    return pumpswap_sol_fee_ppm(q / b * 1e6) / 1e6


class Print:
    """One canonical-pool PumpSwap print: PRE-trade reserves. Q = vault quote + this print's V."""

    __slots__ = ("slot", "bt", "isb", "sol", "tok", "q", "b", "v")

    def __init__(self, slot: int, bt: Optional[int], isb: bool, sol: float, tok: float, q: float, b: float, v: Optional[float]) -> None:
        self.slot, self.bt, self.isb, self.sol, self.tok, self.q, self.b, self.v = slot, bt, isb, sol, tok, q, b, v

    def pre(self) -> tuple[float, float]:
        if self.v is None:
            raise ValueError("print without V")
        return self.q + self.v, self.b

    def post(self) -> tuple[float, float]:
        """State after this print: base moves by token_raw; quote by constant product with the LP share kept in the pool (estimate)."""
        q, b = self.pre()
        b2 = b + (-self.tok if self.isb else self.tok)
        if b2 <= 0:
            return q, b
        q_cp = q * b / b2
        dq = (q_cp - q) * (1 + LP_FRAC) if self.isb else -(q - q_cp) * (1 - LP_FRAC)
        return q + dq, b2


def round_trip(qe: float, be: float, qx: float, bx: float, stake: float = STAKE_LAMPORTS, spot: Optional[float] = None) -> Optional[dict]:
    """Buy at the landing state (qe, be), sell at the exit state (qx, bx), own trade applied (the buy stays in the pool until the sell).
    The guard compares the buy's price to `spot` (the decision-time spot; the landing spot when None). Returns tokens, guarded, proceeds, gross
    return. Same arithmetic as RULE.md / h5_shadow.fill_round_trip."""
    if not all(x > 0 and math.isfinite(x) for x in (qe, be, qx, bx)):
        return None
    f = tier_fee(qe, be)
    net = stake * (1 - f)
    tk = be * net / (qe + net)
    guarded = (stake / tk) > GUARD * (qe / be if spot is None else spot)
    q2, b2 = qx + net, bx - tk
    if b2 <= 0:
        return None
    proceeds = tk * q2 / (b2 + tk) * (1 - tier_fee(q2, b2 + tk))
    return {"tokens": tk, "guarded": guarded, "proceeds": proceeds, "gross_ret": proceeds / stake - 1}


def pnl_variants(rt: dict, stake: float = STAKE_LAMPORTS) -> dict:
    """Lamport P&L under the report variants. A guarded buy reverts and costs one send; nothing else."""
    out = {}
    if rt["guarded"]:
        out["pnl_nofee"] = 0.0
        for k, fee in SEND_FEES.items():
            out[f"pnl_{k}"] = float(-fee)
        out["pnl_505k_rent"] = float(-SEND_FEES["505k"])
        return out
    g = rt["proceeds"] - stake
    out["pnl_nofee"] = g
    for k, fee in SEND_FEES.items():
        out[f"pnl_{k}"] = g - 2 * fee
    out["pnl_505k_rent"] = g - 2 * SEND_FEES["505k"] - 2 * RENT_LAMPORTS
    return out


# ---- slot clock -----------------------------------------------------------------------------------------------------------------------------
class SlotTime:
    """slot <-> block_time from every decoded row. Seconds per slot: a replay override per UTC hour (the batch measures it over the whole hour),
    else a trailing estimate over the last 600 s of the clock."""

    def __init__(self) -> None:
        self.bts: list[int] = []
        self.minslot: dict[int, int] = {}
        self.hour_sps: dict[str, float] = {}

    def add(self, slot: int, bt: int) -> None:
        cur = self.minslot.get(bt)
        if cur is None:
            if not self.bts or bt > self.bts[-1]:
                self.bts.append(bt)
            else:
                bisect.insort(self.bts, bt)
            self.minslot[bt] = slot
        elif slot < cur:
            self.minslot[bt] = slot

    def sps(self, bt: int) -> float:
        o = self.hour_sps.get(hour_of(bt))
        if o:
            return o
        if not self.bts:
            return DEFAULT_SPS
        hi = min(bisect.bisect_right(self.bts, bt), len(self.bts)) - 1
        if hi < 0:
            return DEFAULT_SPS
        lo = bisect.bisect_left(self.bts, self.bts[hi] - SPS_WINDOW_S)
        a, b = self.bts[lo], self.bts[hi]
        ds = self.minslot[b] - self.minslot[a]
        if b - a < SPS_MIN_SPAN_S or ds <= 0:
            return DEFAULT_SPS
        return (b - a) / ds

    def first_slot_at_or_after(self, t: float) -> Optional[int]:
        i = bisect.bisect_left(self.bts, t)
        return self.minslot[self.bts[i]] if i < len(self.bts) else None

    def prune(self, before_bt: int) -> None:
        i = bisect.bisect_left(self.bts, before_bt)
        for bt in self.bts[:i]:
            self.minslot.pop(bt, None)
        del self.bts[:i]


# ---- universe -------------------------------------------------------------------------------------------------------------------------------
def v_of(row: Mapping[str, Any]) -> Optional[float]:
    """Per-print virtual quote reserve (lamports). The tip follower stamps `virtual_quote_reserve`; the walker's event-V rows use
    `virtual_quote_reserves`. Negative values (V0 = 0 pools with pending counters) are real signed readings and are kept."""
    for k in ("virtual_quote_reserve", "virtual_quote_reserves"):
        v = row.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
            return float(v)
    return None


class Universe:
    """Which PumpSwap rows may reach the feature engine: WSOL-quote pools, the canonical pool of the mint, and the event-V band at the pool's
    first print. The engine applies the same rules itself (first print per mint, V band, graduation window); this filter keeps non-universe
    pools out of its memory and counts why they were out. `pda_fn(mint) -> pool or None` is the canonical PDA (tools.pumpswap_tx.canonical_pool)
    and is optional."""

    def __init__(self, pda_fn: Optional[Callable[[str], Optional[str]]] = None, max_rejected: int = 200_000) -> None:
        self.pda_fn = pda_fn
        self.canon: dict[str, str] = {}
        self.pool_mint: dict[str, str] = {}
        self.rejected: "collections.OrderedDict[str, str]" = collections.OrderedDict()
        self.max_rejected = max_rejected
        self.counts: collections.Counter = collections.Counter()

    def canonical_for(self, mint: str) -> Optional[str]:
        c = self.canon.get(mint)
        if c is None and self.pda_fn is not None:
            try:
                c = self.pda_fn(mint)
            except Exception:  # noqa: BLE001 - PDA derivation is a cross-check only
                c = None
            if c:
                self.canon[mint] = c
        return c

    def _reject(self, pool: str, why: str) -> None:
        self.counts[why] += 1
        self.rejected[pool] = why
        while len(self.rejected) > self.max_rejected:
            self.rejected.popitem(last=False)

    def accept(self, row: Mapping[str, Any]) -> bool:
        pool, mint = row.get("pool"), row.get("mint")
        if not pool or not mint or mint == WSOL:
            self.counts["no_pool_or_wsol_base"] += 1
            return False
        if pool in self.pool_mint:
            return True
        if pool in self.rejected:
            return False
        qm = row.get("quote_mint")
        if qm is not None and qm != WSOL:
            self._reject(pool, "quote_not_wsol")
            return False
        canon = self.canonical_for(mint)
        if canon is not None and canon != pool:
            self._reject(pool, "not_canonical")
            return False
        v = v_of(row)
        if v is None:
            self._reject(pool, "no_v_first_print")
            return False
        if not (V_LO <= v <= V_HI):
            self._reject(pool, "v_out_of_band")
            return False
        self.pool_mint[pool] = mint
        self.counts["pools_accepted"] += 1
        return True

    def forget(self, pool: str) -> None:
        self.pool_mint.pop(pool, None)


# ---- seal -----------------------------------------------------------------------------------------------------------------------------------
class SealGuard:
    """EXP-022 section 9 / EXP-025 5.3. `oracle(mint) -> bool` answers "is this mint a CAP-PICK pick" (True = a pick). A pool in scope (first print
    at or after `start_ms`) is priced only when the oracle answers exactly False. Everything else suppresses. `oracle.staleness_s()` (optional)
    over ORACLE_STALE_S suppresses. The oracle is read as a boolean and nothing about a pick is written."""

    def __init__(self, start_ms: Optional[int] = SEAL_START_MS, oracle: Optional[Callable[[str], Any]] = None, stale_s: float = ORACLE_STALE_S) -> None:
        self.start_ms, self.oracle, self.stale_s = start_ms, oracle, stale_s
        self.counters: collections.Counter = collections.Counter()

    def suppress(self, mint: Optional[str], first_print_ms: Optional[int]) -> bool:
        if self.start_ms is None:
            return False
        if first_print_ms is None:
            self.counters["seal_no_time"] += 1
            return True                              # no time to place the pool before the window: fail closed
        if first_print_ms < self.start_ms:
            return False
        if mint is None or self.oracle is None:
            self.counters["seal_no_oracle"] += 1
            return True
        try:
            st = getattr(self.oracle, "staleness_s", None)
            if callable(st):
                age = st()
                if age is None or not isinstance(age, (int, float)) or age > self.stale_s:
                    self.counters["seal_oracle_stale"] += 1
                    return True
            ans = self.oracle(mint)
        except Exception:  # noqa: BLE001 - fail closed
            self.counters["seal_oracle_errors"] += 1
            return True
        if ans is False:
            return False
        self.counters["seal_pick_or_nonbool"] += 1
        return True


# ---- output ---------------------------------------------------------------------------------------------------------------------------------
_FILE_RE = re.compile(r"^(c1nf-[a-z]+)-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl$")


class JsonlSink:
    """Hourly strict-JSON-lines files per stream, flushed per line. The hour is the UTC hour of the record's t_ms. Closed hours of the bulky
    streams are compressed with zstd (the picks stream stays plain: the executor tails it)."""

    PREFIX = {"c1nf_pick": "c1nf-picks", "c1nf_outcome": "c1nf-outcomes"}

    def __init__(self, out_dir: str | Path) -> None:
        self.dir = Path(out_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._fh: dict[str, Any] = {}
        self._name: dict[str, str] = {}
        self.lines: collections.Counter = collections.Counter()

    def prefix_of(self, rec: Mapping[str, Any]) -> str:
        return self.PREFIX.get(rec.get("type", ""), "c1nf-events")

    def path_for(self, rec: Mapping[str, Any]) -> Path:
        return self.dir / f"{self.prefix_of(rec)}-{hour_of(int(rec['t_ms']) // 1000)}.jsonl"

    def write(self, rec: Mapping[str, Any]) -> None:
        path = self.path_for(rec)
        key = self.prefix_of(rec)
        if self._name.get(key) != path.name:
            if key in self._fh:
                self._fh[key].close()
            self._fh[key] = open(path, "a", encoding="utf-8")
            self._name[key] = path.name
        self._fh[key].write(json.dumps(clean(rec), separators=(",", ":"), allow_nan=False) + "\n")
        self._fh[key].flush()
        self.lines[rec.get("type", "?")] += 1

    def compress_closed(self, now_s: float, keep_hours: int = 2) -> int:
        """zstd the closed hours of the outcomes and events streams (not the picks). Returns files compressed; errors are swallowed."""
        n = 0
        cutoff = hour_of(now_s - keep_hours * 3600)
        for p in sorted(self.dir.glob("c1nf-*.jsonl")):
            m = _FILE_RE.match(p.name)
            if m is None or m.group(1) == "c1nf-picks":
                continue
            if m.group(2) < cutoff and p.name != self._name.get(m.group(1)):
                try:
                    subprocess.run(["zstd", "-q", "-3", "--rm", "-f", str(p)], check=True, timeout=120)
                    n += 1
                except (OSError, subprocess.SubprocessError):
                    pass
        return n

    def close(self) -> None:
        for fh in self._fh.values():
            fh.close()
        self._fh.clear()
        self._name.clear()


class MemorySink:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def write(self, rec: Mapping[str, Any]) -> None:
        self.records.append(json.loads(json.dumps(clean(rec), allow_nan=False)))

    def compress_closed(self, now_s: float, keep_hours: int = 2) -> int:
        return 0

    def close(self) -> None:
        pass

    def of(self, typ: str) -> list[dict]:
        return [r for r in self.records if r.get("type") == typ]


class ErrorLog:
    def __init__(self, path: str | Path, max_bytes: int = ERRORS_MAX_BYTES) -> None:
        self.path, self.max_bytes, self.bytes, self.dropped = Path(path), max_bytes, 0, 0

    def log(self, exc: BaseException, ctx: Optional[dict] = None) -> None:
        if self.bytes >= self.max_bytes:
            self.dropped += 1
            return
        head = json.dumps(clean({"t_ms": now_ms(), "exc": type(exc).__name__, "msg": str(exc)[:300], **(ctx or {})}), allow_nan=False)
        text = head + "\n" + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)) + "\n"
        try:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(text)
        except OSError:
            pass
        self.bytes += len(text)


# ---- feature engine adapter -----------------------------------------------------------------------------------------------------------------
class Feat:
    __slots__ = ("vec", "stage1", "h_top1", "sd", "mint")

    def __init__(self, vec: np.ndarray, stage1: bool, h_top1: float, sd: Optional[int], mint: Optional[str]) -> None:
        self.vec, self.stage1, self.h_top1, self.sd, self.mint = vec, stage1, h_top1, sd, mint


def unpack_features(res: Any) -> Optional[Feat]:
    """features_at returns either the interface tuple (vec107, stage1_pass, h_top1[, sd]) or tools.c1nf_features.Features. None = not decidable."""
    if res is None:
        return None
    if isinstance(res, tuple):
        vec, s1, h = res[0], res[1], res[2]
        sd = res[3] if len(res) > 3 else None
        mint = None
    else:
        vec, s1, h = res.vec, res.stage1, res.h_top1
        sd, mint = getattr(res, "sd", None), getattr(res, "mint", None)
    vec = np.asarray(vec, dtype=np.float64)
    if vec.shape != (N_FEATURES,):
        raise ValueError(f"feature vector has shape {vec.shape}, want ({N_FEATURES},)")
    return Feat(vec, bool(s1), float(h) if h is not None else float("nan"), sd, mint)


def cap_ok(h_top1: float) -> bool:
    """The added C1-NF line: keep when h_top1 is a number and <= 0.5 (compared as float32, as the export is)."""
    h = float(h_top1)
    return bool(math.isfinite(h) and float(np.float32(h)) <= H_TOP1_MAX)


# ---- the shadow -----------------------------------------------------------------------------------------------------------------------------
class Pending:
    __slots__ = ("pool", "mint", "T", "sd", "sd_bt", "pred", "pick_ms", "first_print_ms", "last_before", "prints", "slots", "sps", "gap", "done")

    def __init__(self, pool, mint, T, sd, sd_bt, pred, pick_ms, first_print_ms, last_before, sps):
        self.pool, self.mint, self.T, self.sd, self.sd_bt, self.pred = pool, mint, T, sd, sd_bt, pred
        self.pick_ms, self.first_print_ms, self.last_before, self.sps = pick_ms, first_print_ms, last_before, sps
        self.prints: list[Print] = []
        self.slots: list[int] = []
        self.gap = False
        self.done = False


class Shadow:
    def __init__(self, engine: Any, models: ModelSet, sink: Any, *, oracle: Optional[Callable[[str], Any]] = None, seal_start_ms: Optional[int] = SEAL_START_MS,
                 universe: Optional[Universe] = None, wall: Callable[[], int] = now_ms, replay: bool = False, errors: Optional[ErrorLog] = None,
                 decide_from: Optional[int] = None, decide_to: Optional[int] = None) -> None:
        self.engine, self.models, self.sink = engine, models, sink
        self.universe = universe or Universe()
        self.seal = SealGuard(seal_start_ms, oracle)
        self.clock = SlotTime()
        self.replay, self._wall, self.errors = replay, wall, errors
        self.decide_from, self.decide_to = decide_from, decide_to
        self.decide_enabled = True
        self.hw_slot: Optional[int] = None
        self.hw_bt: Optional[int] = None
        self.last_T: Optional[int] = None
        self.last_row_wall: Optional[int] = None
        self.first_ms: dict[str, int] = {}                # pool -> first print time (ms)
        self.last_print: dict[str, Print] = {}
        self.pending: dict[str, list[Pending]] = collections.defaultdict(list)
        self.book: dict[str, float] = {}                   # mint -> estimated exit (stream seconds)
        self.gaps: list[tuple[int, int, str]] = []        # (lo_slot, hi_slot, kind)
        self.c: collections.Counter = collections.Counter()
        self.last_hb = 0.0
        self.last_expire = 0
        self.started_ms = wall()
        self.run_info: dict[str, Any] = {}

    # ---- time ----
    def clock_ms(self) -> int:
        if self.replay and self.hw_bt is not None:
            return self.hw_bt * 1000
        return self._wall()

    def emit(self, rec: dict) -> None:
        rec = {"schema": SCHEMA, "t_ms": self.clock_ms(), **rec}
        self.sink.write(rec)

    # ---- rows ----
    def feed(self, row: Mapping[str, Any], kind: Optional[str] = None) -> None:
        """One decoded tip / tape row. `kind` in trades / creates / migrations; inferred from the row when absent."""
        kind = kind or row.get("_k") or ("migrations" if row.get("type") in ("complete", "migration") else "creates" if row.get("type") == "create" or "symbol" in row else "trades")
        slot, bt = row.get("slot"), row.get("block_time")
        if not isinstance(slot, int) or not isinstance(bt, int):
            self.c["rows_bad"] += 1
            return
        self.c["rows"] += 1
        self.last_row_wall = self._wall()
        self._on_clock(slot, bt)
        try:
            if kind == "trades":
                self._on_trade(row, slot, bt)
            elif kind == "creates":
                self.engine.on_create(row["mint"], row.get("creator"), row.get("name"), row.get("symbol"), bt, row.get("is_mayhem_mode"))
            elif kind == "migrations":
                if row.get("type") == "complete" or row.get("type") is None:
                    self.engine.on_graduation(row["mint"], bt)
                elif row.get("pool") and row.get("quote_mint", WSOL) == WSOL:
                    self.universe.canon[row["mint"]] = row["pool"]
        except Exception as exc:  # noqa: BLE001 - one bad row never stops the shadow
            self.c["row_errors"] += 1
            if self.errors:
                self.errors.log(exc, {"kind": kind, "slot": slot})

    def _on_clock(self, slot: int, bt: int) -> None:
        if self.hw_slot is not None and slot > self.hw_slot and self.hw_bt is not None and bt - self.hw_bt > CLOCK_JUMP_S:
            self._gap("clock_jump", self.hw_slot, slot, bt_from=self.hw_bt, bt_to=bt)
        self.clock.add(slot, bt)
        self.engine.on_block(slot, bt)
        if self.hw_slot is None or slot > self.hw_slot:
            self.hw_slot = slot
        newbt = self.hw_bt is None or bt > self.hw_bt
        if newbt:
            self.hw_bt = bt
            Tc = bt // 60 * 60
            if self.last_T is None:
                self.last_T = Tc
            elif Tc > self.last_T:
                skipped = (Tc - self.last_T) // 60 - 1
                if skipped > 0:
                    self.c["minutes_skipped"] += skipped
                    self._gap("minutes_skipped", self.hw_slot, slot, minutes=skipped)
                self.last_T = Tc
                if self.decide_enabled and (self.decide_from is None or Tc >= self.decide_from) and (self.decide_to is None or Tc < self.decide_to):
                    self._decide(Tc, slot, bt)
            self._resolve_due()

    def _on_trade(self, row: Mapping[str, Any], slot: int, bt: int) -> None:
        venue = row.get("venue")
        if venue == "pumpswap":
            if not self.universe.accept(row):
                return
            pool, mint = row["pool"], row["mint"]
            v = v_of(row)
            if v is not None:
                self.engine.set_pool_v(pool, v)
            side = row.get("side")
            self.engine.on_trade(venue, mint, row.get("trader"), side == "buy", row.get("sol_lamports"), row.get("token_raw"), row.get("quote_reserve"),
                                 row.get("base_reserve"), pool, slot, bt)
            pr = Print(slot, bt, side == "buy", float(row.get("sol_lamports") or 0), float(row.get("token_raw") or 0), float(row.get("quote_reserve") or 0),
                       float(row.get("base_reserve") or 0), v)
            self.first_ms.setdefault(pool, bt * 1000)
            self.last_print[pool] = pr
            for pend in self.pending.get(pool, ()):
                if not pend.done and slot >= pend.sd:
                    pend.prints.append(pr)
                    pend.slots.append(slot)
        elif venue == "pump_bonding":
            self.engine.on_trade(venue, row["mint"], row.get("trader"), row.get("side") == "buy", row.get("sol_lamports"), row.get("token_raw"),
                                 row.get("quote_reserve"), row.get("base_reserve"), None, slot, bt)

    # ---- decisions ----
    def _decide(self, T: int, sd: int, bt: int) -> None:
        self.c["minutes"] += 1
        feats: list[tuple[str, Feat]] = []
        for pool in self.engine.alive_pools(T):
            try:
                f = unpack_features(self.engine.features_at(pool, T, sd))
            except Exception as exc:  # noqa: BLE001
                self.c["feature_errors"] += 1
                if self.errors:
                    self.errors.log(exc, {"pool": pool, "T": T})
                continue
            if f is None:
                self.c["not_decidable"] += 1
                continue
            self.c["decision_rows"] += 1
            if not f.stage1:
                continue
            self.c["stage1"] += 1
            feats.append((pool, f))
        if not feats:
            return
        try:
            model, sha = self.models.for_day(day_of(T))
            X = np.stack([f.vec for _, f in feats]).astype(np.float32)
            preds = np.asarray(model.predict(X), dtype=np.float64).reshape(-1)
            if preds.shape[0] != len(feats):
                raise ValueError("model returned the wrong number of predictions")
        except Exception as exc:  # noqa: BLE001 - fail closed: no model answer, no pick
            self.c["model_errors"] += 1
            if self.errors:
                self.errors.log(exc, {"T": T})
            return
        for (pool, f), pred in zip(feats, preds):
            if not (pred > PRED_MIN):                       # NaN fails
                continue
            self.c["stage2"] += 1
            if not cap_ok(f.h_top1):
                self.c["cap_dropped"] += 1
                continue
            mint = f.mint or self.universe.pool_mint.get(pool)
            if mint is None:
                self.c["no_mint"] += 1
                continue
            if self.seal.suppress(mint, self.first_ms.get(pool)):
                self.c["withheld"] += 1                      # EXP-022 s9: no pick, no book entry, no record about this pool; aggregate only
                continue
            if T < self.book.get(mint, -1.0) + REENTRY_S:
                self.c["book_blocked"] += 1
                continue
            sps = self.clock.sps(bt)
            self.book[mint] = bt + PRIMARY_LAT + EXIT_S + EXIT_LAG_S
            self.c["picks"] += 1
            self.emit({"type": "c1nf_pick", "mint": mint, "pool": pool, "decision_T_ms": T * 1000, "SD_slot": int(sd), "pred": float(pred),
                       "h_top1": float(f.h_top1), "stage1": True, "feature_hash": feature_hash(f.vec.astype(np.float32)), "model_sha": sha})
            self.pending[pool].append(Pending(pool, mint, T, int(sd), bt, float(pred), self.clock_ms(), self.first_ms.get(pool), self.last_print.get(pool), sps))

    # ---- outcomes ----
    def _resolve_due(self) -> None:
        if self.hw_bt is None:
            return
        for pool in list(self.pending):
            for pend in self.pending[pool]:
                if not pend.done and self.hw_bt >= pend.sd_bt + max(ENTRY_LATS) + EXIT_S + EXIT_LAG_S + GRACE_S:
                    self._resolve(pend, final=False)
            self.pending[pool] = [p for p in self.pending[pool] if not p.done]
            if not self.pending[pool]:
                del self.pending[pool]

    def _states(self, pend: Pending, z: int) -> tuple[tuple[float, float], tuple[float, float], bool]:
        """(END state, START state, END estimated?). END = pre-trade state of the first print with slot > z (the state after the last print
        <= z), else the post-trade estimate of the last print. START = pre-trade state of the first print with slot >= z."""
        ps = pend.prints
        i = bisect.bisect_right(pend.slots, z)
        if i < len(ps):
            end, est = ps[i].pre(), False
        else:
            last = ps[-1] if ps else pend.last_before
            if last is None:
                raise ValueError("no print to price from")
            end, est = last.post(), True
        j = bisect.bisect_left(pend.slots, z)
        start = ps[j].pre() if j < len(ps) else end
        return end, start, est

    def _cands(self, pend: Pending, z: int) -> list[tuple[float, float]]:
        """Pre-trade states of the prints inside slot z, plus the END state."""
        end, _, _ = self._states(pend, z)
        j, i = bisect.bisect_left(pend.slots, z), bisect.bisect_right(pend.slots, z)
        return [pend.prints[k].pre() for k in range(j, i)] + [end]

    def _resolve(self, pend: Pending, final: bool) -> None:
        pend.done = True
        self.c["outcomes"] += 1
        base = {"type": "c1nf_outcome", "mint": pend.mint, "pool": pend.pool, "decision_T_ms": pend.T * 1000, "SD_slot": pend.sd, "SD_bt": pend.sd_bt,
                "pred": pend.pred}
        if self.seal.suppress(pend.mint, pend.first_print_ms):   # the oracle went stale / failed since the pick: price nothing, say nothing
            self.c["withheld"] += 1
            self.c["outcomes"] -= 1
            pend.prints, pend.slots = [], []
            return
        legs: dict[str, Any] = {}
        complete = True
        reasons: list[str] = []
        sps = pend.sps
        spot: Optional[float] = None
        try:
            if pend.prints:
                q0, b0 = pend.prints[0].pre()                 # the first print with slot >= SD, PRE-trade: the batch's decision spot
            else:
                q0, b0 = pend.last_before.post()              # no later print: the state after the last one
            spot = q0 / b0
        except Exception:  # noqa: BLE001
            complete = False
            reasons.append("no_spot")
        for lat in ENTRY_LATS:
            key = f"{lat:g}"
            X = pend.sd + int(math.floor(lat / sps + 0.5))
            t_land = pend.sd_bt + (X - pend.sd) * sps
            deadline = t_land + EXIT_S
            sd_exit = self.clock.first_slot_at_or_after(deadline)
            if sd_exit is None or spot is None:
                legs[key] = {"landing_slot": X, "incomplete": "clock_short" if sd_exit is None else "no_spot"}
                complete = False
                reasons.append(f"{key}:{'clock_short' if sd_exit is None else 'no_spot'}")
                continue
            Y = sd_exit + int(math.ceil(EXIT_LAG_S / sps))
            try:
                end_b, _, est_b = self._states(pend, X)
                end_s, _, est_s = self._states(pend, Y)
                cb, cs = self._cands(pend, X), self._cands(pend, Y)
                worst_b = max(cb, key=lambda s: s[0] / s[1])
                worst_s = min(cs, key=lambda s: s[0] / s[1])
                leg: dict[str, Any] = {"landing_slot": X, "exit_slot": Y, "end_estimated": bool(est_b or est_s), "n_prints_total": len(pend.prints)}
                for name, eb, es in (("end", end_b, end_s), ("worst", worst_b, worst_s)):
                    rt = round_trip(eb[0], eb[1], es[0], es[1], spot=spot)
                    if rt is None:
                        leg[name] = {"incomplete": "bad_state"}
                        complete = False
                        continue
                    leg[name] = {"entry_q": eb[0], "entry_b": eb[1], "exit_q": es[0], "exit_b": es[1], "entry_px": eb[0] / eb[1], "exit_px": es[0] / es[1],
                                 "tokens": rt["tokens"], "guarded": rt["guarded"], "proceeds": rt["proceeds"], "gross_ret": rt["gross_ret"], **pnl_variants(rt)}
                legs[key] = leg
            except Exception as exc:  # noqa: BLE001 - missing V, no print, empty pool
                legs[key] = {"landing_slot": X, "exit_slot": Y, "incomplete": type(exc).__name__}
                complete = False
                reasons.append(f"{key}:{type(exc).__name__}")
        lo, hi = pend.sd, pend.sd + int((max(ENTRY_LATS) + EXIT_S + EXIT_LAG_S + 2) / max(sps, 1e-3))
        gap = pend.gap or any(g_lo <= hi and g_hi >= lo for g_lo, g_hi, _ in self.gaps)
        if gap:
            self.c["outcomes_gap"] += 1
        if not complete:
            self.c["outcomes_incomplete"] += 1
        self.emit({**base, "complete": bool(complete), "primary": f"{PRIMARY_LAT:g}", "sps": sps,
                   "spot": spot, "stake_lamports": STAKE_LAMPORTS, "gap": bool(gap), "reasons": reasons[:6], "legs": legs,
                   "pick_lag_ms": max(0, pend.pick_ms - pend.sd_bt * 1000) if not self.replay else None})
        pend.prints, pend.slots = [], []

    # ---- gaps, heartbeat ----
    def _gap(self, kind: str, lo: Optional[int], hi: Optional[int], **kw: Any) -> None:
        self.c["gaps"] += 1
        if lo is not None and hi is not None:
            self.gaps.append((int(lo), int(hi), kind))
            del self.gaps[:-200]
            for lst in self.pending.values():
                for p in lst:
                    if not p.done:
                        p.gap = True
        self.emit({"type": "c1nf_gap", "kind": kind, "slot_from": lo, "slot_to": hi, **kw})

    def note_follower_gap(self, rec: Mapping[str, Any]) -> None:
        """A line of the tip follower's own gaps.jsonl (backlog_jump, unfetchable slot): copied as a gap record and marks open picks."""
        lo = rec.get("slot_from", rec.get("from_slot", rec.get("slot")))
        hi = rec.get("slot_to", rec.get("to_slot", rec.get("slot")))
        self._gap("follower_gap", lo if isinstance(lo, int) else None, hi if isinstance(hi, int) else None, reason=str(rec.get("reason"))[:60])

    def tick(self, now: Optional[int] = None) -> None:
        now = self._wall() if now is None else now
        if self.last_row_wall is not None and not self.replay and now - self.last_row_wall > SILENCE_S * 1000 and not getattr(self, "_silent", False):
            self._silent = True
            self.c["silence"] += 1
            self.emit({"type": "c1nf_gap", "kind": "silence", "slot_from": self.hw_slot, "slot_to": None, "silent_s": (now - self.last_row_wall) / 1000})
            for lst in self.pending.values():
                for p in lst:
                    p.gap = True
        elif self.last_row_wall is not None and now - self.last_row_wall <= SILENCE_S * 1000:
            self._silent = False
        if self.hw_bt is not None and self.hw_bt - self.last_expire > 600:
            self.last_expire = self.hw_bt
            try:
                self.engine.expire(self.hw_bt)
            except Exception as exc:  # noqa: BLE001
                if self.errors:
                    self.errors.log(exc, {"where": "expire"})
            self.clock.prune(self.hw_bt - 3 * 3600)
            self.sink.compress_closed(now / 1000)
        if now / 1000 - self.last_hb >= HEARTBEAT_S:
            self.last_hb = now / 1000
            self.heartbeat(now)

    def heartbeat(self, now: Optional[int] = None) -> dict:
        now = self._wall() if now is None else now
        hb = {"type": "c1nf_heartbeat", "hw_slot": self.hw_slot, "hw_bt": self.hw_bt, "last_decided_T": self.last_T,
              "stream_lag_s": None if self.hw_bt is None or self.replay else round(now / 1000 - self.hw_bt, 2),
              "pending": sum(len(v) for v in self.pending.values()), "pools_alive": len(self.universe.pool_mint), "counters": dict(self.c),
              "universe": dict(self.universe.counts), "model_shas": self.models.shas, "rule": RULE_ID,
              "uptime_s": round((now - self.started_ms) / 1000, 1)}
        self.emit(hb)
        return hb

    def finish(self, reason: str = "end") -> None:
        for lst in list(self.pending.values()):
            for p in lst:
                if not p.done:
                    self._resolve(p, final=True)
        self.pending.clear()
        self.emit({"type": "c1nf_stop", "reason": reason, "counters": dict(self.c)})
        self.heartbeat()


# ---- tailing the tip tape -------------------------------------------------------------------------------------------------------------------
KINDS = ("trades", "creates", "migrations")
KIND_RANK = {"creates": 0, "migrations": 1, "trades": 2}


def row_key(row: Mapping[str, Any]) -> tuple:
    return (row.get("slot", 0), row.get("tx_index") or 0, row.get("event_index") or 0, KIND_RANK.get(row.get("_k", "trades"), 2))


class TipTail:
    """Tails <kind>-<hour>.jsonl in the tip dir. Offsets are per (kind, hour); a half-written last line waits for the next poll; a file that shrank
    or was replaced (inode change) restarts at 0; offsets of hours older than the previous hour are dropped. `bootstrap` reads whole hours (plain
    or .zst) from the beginning, so the live offsets continue where the bootstrap stopped."""

    def __init__(self, directory: str | Path) -> None:
        self.dir = Path(directory)
        self.off: dict[tuple[str, str], int] = {}
        self.ino: dict[tuple[str, str], int] = {}
        self.rows_read = 0
        self.bad_lines = 0
        self.resets = 0

    def _read_plain(self, kind: str, hour: str) -> list[dict]:
        path = self.dir / f"{kind}-{hour}.jsonl"
        key = (kind, hour)
        try:
            st = path.stat()
        except OSError:
            return []
        pos = self.off.get(key, 0)
        if key in self.ino and (self.ino[key] != st.st_ino or st.st_size < pos):
            pos = 0
            self.resets += 1
        self.ino[key] = st.st_ino
        out: list[dict] = []
        with open(path, "rb") as fh:
            fh.seek(pos)
            while True:
                line = fh.readline()
                if not line.endswith(b"\n"):
                    break                                    # EOF or a half-written line: wait
                pos += len(line)
                try:
                    r = json.loads(line)
                except ValueError:
                    self.bad_lines += 1
                    continue
                if isinstance(r, dict):
                    r["_k"] = kind
                    out.append(r)
        self.off[key] = pos
        self.rows_read += len(out)
        return out

    def _read_zst(self, kind: str, hour: str, extra_dirs: Sequence[Path]) -> list[dict]:
        for d in (self.dir, *extra_dirs):
            p = d / f"{kind}-{hour}.jsonl.zst"
            if p.is_file():
                res = subprocess.run(["zstd", "-dc", str(p)], capture_output=True, check=True, timeout=300)
                out = []
                for line in res.stdout.splitlines():
                    try:
                        r = json.loads(line)
                    except ValueError:
                        self.bad_lines += 1
                        continue
                    if isinstance(r, dict):
                        r["_k"] = kind
                        out.append(r)
                self.rows_read += len(out)
                return out
        return []

    def bootstrap(self, hours: Sequence[str], extra_dirs: Sequence[str | Path] = ()) -> Iterator[list[dict]]:
        """Yield one sorted batch per hour, oldest first. An hour with a plain file is read (and its offset kept); otherwise its .zst."""
        extra = [Path(d) for d in extra_dirs]
        for h in hours:
            rows: list[dict] = []
            for k in KINDS:
                got = self._read_plain(k, h) if (self.dir / f"{k}-{h}.jsonl").exists() else self._read_zst(k, h, extra)
                rows.extend(got)
            rows.sort(key=row_key)
            yield rows

    def poll(self, now_ms_: int) -> list[dict]:
        cur = hour_of(now_ms_ / 1000)
        prev = hour_of(now_ms_ / 1000 - 3600)
        nxt = hour_of(now_ms_ / 1000 + 3600)
        rows: list[dict] = []
        for h in (prev, cur, nxt):
            for k in KINDS:
                rows.extend(self._read_plain(k, h))
        for key in [k for k in self.off if k[1] not in (prev, cur, nxt)]:
            self.off.pop(key, None)
            self.ino.pop(key, None)
        rows.sort(key=row_key)
        return rows


class GapTail:
    """Tails the follower's gaps.jsonl (appended lines only; same half-line / truncation handling)."""

    def __init__(self, path: str | Path) -> None:
        self.path, self.pos, self.ino = Path(path), 0, None
        self.primed = False

    def poll(self) -> list[dict]:
        try:
            st = self.path.stat()
        except OSError:
            return []
        if not self.primed:                                   # history before this run is not this run's gap
            self.pos, self.ino, self.primed = st.st_size, st.st_ino, True
            return []
        if self.ino != st.st_ino or st.st_size < self.pos:
            self.pos, self.ino = 0, st.st_ino
        out = []
        with open(self.path, "rb") as fh:
            fh.seek(self.pos)
            while True:
                line = fh.readline()
                if not line.endswith(b"\n"):
                    break
                self.pos += len(line)
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    out.append(r)
        return out


# ---- ledger adapter -------------------------------------------------------------------------------------------------------------------------
class _AsofSnapshot:
    """c1nf_features.LedgerSnapshot over a tools.c1nf_wallet_ledger.AsofLedger: get(trader) -> 7 passA values or None. The ledger keys wallets on
    duckdb hash(trader); `hash_fn(trader) -> int` defaults to a duckdb call."""

    def __init__(self, asof: Any, hash_fn: Optional[Callable[[str], int]] = None) -> None:
        self.asof, self._hash, self._cache = asof, hash_fn or _duck_hash(), {}

    def get(self, trader: str) -> Optional[Sequence[float]]:
        if trader in self._cache:
            return self._cache[trader]
        th = np.array([self._hash(trader)], dtype=np.uint64)
        known, m = self.asof.passa_matrix(th)
        v = tuple(float(x) for x in m[0]) if bool(known[0]) else None
        if len(self._cache) > 500_000:
            self._cache.clear()
        self._cache[trader] = v
        return v


def _duck_hash() -> Callable[[str], int]:
    import duckdb  # lazy

    con = duckdb.connect()
    return lambda s: int(con.execute("select hash(?)", [s]).fetchone()[0])


class AsofDirLedger:
    """c1nf_features.LedgerProvider over `<root>/asof/asof-<day>` directories written by tools/c1nf_wallet_ledger.py. None for a day without a snapshot
    (wallet features then stay NaN, as in C1)."""

    def __init__(self, root: str | Path, opener: Optional[Callable[[Path], Any]] = None, hash_fn: Optional[Callable[[str], int]] = None) -> None:
        self.root, self.hash_fn = Path(root), hash_fn
        if opener is None:
            from tools.c1nf_wallet_ledger import AsofLedger

            opener = AsofLedger.open
        self.opener = opener

    def snapshot_for_day(self, day: str) -> Optional[_AsofSnapshot]:
        p = self.root / "asof" / f"asof-{day}"
        if not p.is_dir():
            return None
        return _AsofSnapshot(self.opener(p), self.hash_fn)


def load_oracle(spec: Optional[str]) -> Optional[Callable[[str], Any]]:
    """`pkg.module:callable` -> the pick oracle. None when absent (the seal then fails closed)."""
    if not spec:
        return None
    mod, _, fn = spec.partition(":")
    return getattr(importlib.import_module(mod), fn)


def canonical_pda_fn() -> Optional[Callable[[str], Optional[str]]]:
    """The canonical pool PDA of a mint, when solders is importable (tools.pumpswap_tx.canonical_pool); else None (the engine's first-print rule)."""
    try:
        from solders.pubkey import Pubkey  # noqa: F401

        from tools import pumpswap_tx as tx

        return lambda mint: str(tx.canonical_pool(Pubkey.from_string(mint)))
    except Exception:  # noqa: BLE001
        return None


def build_engine(ledger: Any = None) -> Any:
    from tools.c1nf_features import FeatureEngine  # the parallel builder's module

    return FeatureEngine(ledger=ledger)


# ---- replay (exploration tape, read-only) ---------------------------------------------------------------------------------------------------
FORBIDDEN = ("fresh-0802", "fresh-0808", "fresh-0828", "oracle-live", "forward-paper", "forward-walk", "forward_walk", "forward-1002", "forward-1016",
             "exp012-gate", "runner-status", "/var/lib/mal", ".env", "helius", "keypair", "walk-2", "walk2", "walk_2")
EXPLORATION = (("2026-08-14T12", "2026-08-28T12"), ("2026-09-03T12", "2026-09-15T12"), ("2026-09-18T23", "2026-09-25T07"))


def refuse_replay(paths: Iterable[str], hours: Sequence[str]) -> None:
    for p in paths:
        for bad in FORBIDDEN:
            if bad in str(p):
                raise Refused(f"{p}: outside the exploration tape ({bad!r})")
    for h in hours:
        if not any(lo <= h < hi for lo, hi in EXPLORATION):
            raise Refused(f"{h}: not an exploration-tape hour {EXPLORATION}")


def replay_hours(first: str, n: int) -> list[str]:
    t0 = datetime.strptime(first, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    return [(t0 + timedelta(hours=i)).strftime("%Y-%m-%dT%H") for i in range(n)]


def tape_rows(tape_dir: str, hours: Sequence[str], pool_v: Mapping[str, float], pool_of_mint: Mapping[str, str]) -> Iterator[list[dict]]:
    """Per tape hour, the rows of all three kinds sorted by (slot, tx_index, event_index). The tape has no per-print V: each row gets the pool's V
    from the hunt-shared token table (`pool_v`). Only canonical-pool PumpSwap rows (pool == the table's pool for the mint) are passed on."""
    import pandas as pd  # lazy: the audit venv

    for h in hours:
        rows: list[dict] = []
        t = pd.read_parquet(f"{tape_dir}/trades/{h}.parquet")
        bond = t[t.venue == "pump_bonding"]
        ps = t[t.venue == "pumpswap"]
        ps = ps[ps.pool.map(lambda p: p in pool_v)]
        for df, is_ps in ((bond, False), (ps, True)):
            for r in df.to_dict("records"):
                r["_k"] = "trades"
                if is_ps:
                    r["virtual_quote_reserve"] = pool_v[r["pool"]]
                rows.append(r)
        for k in ("creates", "migrations"):
            for r in pd.read_parquet(f"{tape_dir}/{k}/{h}.parquet").to_dict("records"):
                r["_k"] = k
                rows.append(r)
        for r in rows:
            for k, v in list(r.items()):
                if isinstance(v, float) and math.isnan(v):
                    r[k] = None
        rows.sort(key=row_key)
        yield rows


def hour_sps_from_tape(tape_dir: str, hours: Sequence[str]) -> dict[str, float]:
    """Seconds per slot of each UTC hour from the hour's clock endpoints, as the batch measures it."""
    import pandas as pd

    out = {}
    for h in hours:
        t = pd.read_parquet(f"{tape_dir}/trades/{h}.parquet", columns=["slot", "block_time"])
        g = t.groupby("block_time").slot.min()
        if len(g) > 1 and g.index[-1] > g.index[0] and g.iloc[-1] > g.iloc[0]:
            out[h] = float((g.index[-1] - g.index[0]) / (g.iloc[-1] - g.iloc[0]))
    return out


def run_replay(args: argparse.Namespace, models: ModelSet, engine: Any, sink: Any) -> int:
    hours = replay_hours(args.replay_from, args.replay_hours)
    refuse_replay([args.replay_tape, args.out_dir, args.tokens], hours)
    import pandas as pd

    tk = pd.read_parquet(args.tokens, columns=["mint", "pool", "v0_lamports"])
    tk = tk[(tk.v0_lamports >= V_LO) & (tk.v0_lamports <= V_HI)]
    pool_v = dict(zip(tk.pool, tk.v0_lamports.astype(float)))
    pool_of_mint = dict(zip(tk.mint, tk.pool))
    dec_from = int(datetime.strptime(args.decide_from, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp()) if args.decide_from else None
    dec_to = int(datetime.strptime(args.decide_to, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp()) if args.decide_to else None
    sh = Shadow(engine, models, sink, replay=True, seal_start_ms=None, decide_from=dec_from, decide_to=dec_to, errors=ErrorLog(Path(args.out_dir) / "errors.log"))
    sh.clock.hour_sps = hour_sps_from_tape(args.replay_tape, hours)
    sh.run_info = {"mode": "replay", "hours": [hours[0], hours[-1]], "decide_from": args.decide_from, "decide_to": args.decide_to}
    t_first = int(datetime.strptime(hours[0], "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)
    sh.emit({"type": "c1nf_start", "t_ms": t_first, "mode": "replay", "hours": [hours[0], hours[-1]], "model_shas": models.shas, "v_source": "hunt-shared tokens.v0_lamports (tape has no event-V)"})
    for batch in tape_rows(args.replay_tape, hours, pool_v, pool_of_mint):
        for r in batch:
            sh.feed(r, r["_k"])
    sh.finish("replay_end")
    return EXIT_OK


# ---- live loop ------------------------------------------------------------------------------------------------------------------------------
class Stop:
    flag = False


def run_live(args: argparse.Namespace, models: ModelSet, engine: Any, sink: Any) -> int:
    out = Path(args.out_dir)
    errors = ErrorLog(out / "errors.log")
    oracle = load_oracle(args.pick_oracle)
    seal_ms = None if args.no_seal else int(datetime.strptime(args.seal_start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)
    sh = Shadow(engine, models, sink, oracle=oracle, seal_start_ms=seal_ms, universe=Universe(canonical_pda_fn()), errors=errors)
    tail = TipTail(args.tip_dir)
    gaps = GapTail(args.gaps_file) if args.gaps_file else None
    sh.emit({"type": "c1nf_start", "mode": "live", "tip_dir": str(args.tip_dir), "model_shas": models.shas, "seal_start_ms": seal_ms,
             "oracle": bool(oracle), "bootstrap_hours": args.bootstrap_hours, "max_seconds": args.max_seconds})

    def on_sig(*_a: Any) -> None:
        Stop.flag = True

    signal.signal(signal.SIGTERM, on_sig)
    signal.signal(signal.SIGINT, on_sig)
    t0 = time.monotonic()
    now = now_ms()
    # bootstrap: rebuild engine state from the last hours with decisions off, then resume at the next whole minute
    sh.decide_enabled = False
    hrs = [hour_of(now / 1000 - 3600 * i) for i in range(args.bootstrap_hours, -1, -1)]
    for batch in tail.bootstrap(hrs, args.archive_dir):
        for r in batch:
            sh.feed(r, r["_k"])
        if Stop.flag:
            break
    sh.decide_enabled = True
    sh.last_T = (sh.hw_bt // 60 * 60) if sh.hw_bt else None
    sh.c["bootstrap_rows"] = tail.rows_read
    held: list[dict] = []
    try:
        while not Stop.flag and (args.max_seconds is None or time.monotonic() - t0 < args.max_seconds):
            now = now_ms()
            held.extend(tail.poll(now))
            cut = now - HOLD_MS
            ready = [r for r in held if (r.get("t_recv_ms") or 0) <= cut]
            held = [r for r in held if (r.get("t_recv_ms") or 0) > cut]
            ready.sort(key=row_key)
            for r in ready:
                sh.feed(r, r["_k"])
            if gaps:
                for g in gaps.poll():
                    sh.note_follower_gap(g)
            sh.tick(now)
            _write_status(out / "status.json", sh)
            time.sleep(args.poll_s)
    finally:
        for r in sorted(held, key=row_key):
            sh.feed(r, r["_k"])
        sh.finish("signal" if Stop.flag else "max_seconds" if args.max_seconds is not None else "end")
        _write_status(out / "status.json", sh)
        sink.close()
    return EXIT_OK


def _write_status(path: Path, sh: Shadow) -> None:
    tmp = path.with_suffix(".tmp")
    st = {"schema": SCHEMA, "t_ms": now_ms(), "hw_slot": sh.hw_slot, "hw_bt": sh.hw_bt, "counters": dict(sh.c), "universe": dict(sh.universe.counts),
          "pending": sum(len(v) for v in sh.pending.values())}
    try:
        tmp.write_text(json.dumps(clean(st), allow_nan=False))
        os.replace(tmp, path)
    except OSError:
        pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="C1-NF paper shadow (keyless). Live on the fast-0 tip tape, or --replay-from on one exploration day.")
    p.add_argument("--tip-dir", default=DEFAULT_TIP_DIR)
    p.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    p.add_argument("--model", help="LightGBM model file (with --model-sha256)")
    p.add_argument("--model-sha256", help="pinned sha256 of --model; a mismatch refuses to start")
    p.add_argument("--model-manifest", help='JSON {"models":[{"from_day","file","sha256"}]} for the daily retrain')
    p.add_argument("--ledger-root", help="tools/c1nf_wallet_ledger.py output root (asof/asof-<day>)")
    p.add_argument("--archive-dir", action="append", default=[], help="extra dir with <kind>-<hour>.jsonl.zst for the bootstrap")
    p.add_argument("--gaps-file", default=None, help="the tip follower's gaps.jsonl")
    p.add_argument("--bootstrap-hours", type=int, default=26)
    p.add_argument("--poll-s", type=float, default=0.5)
    p.add_argument("--max-seconds", type=float, default=None, help="stop after this many seconds (smoke runs)")
    p.add_argument("--pick-oracle", default=None, help="module:callable, oracle(mint) -> bool (True = CAP-PICK pick); absent = fail closed in the window")
    p.add_argument("--seal-start", default="2026-10-16T01:00:00Z")
    p.add_argument("--no-seal", action="store_true", help="tests only: disables the seal")
    p.add_argument("--replay-from", help="UTC hour YYYY-MM-DDTHH of the first exploration-tape hour (replay mode)")
    p.add_argument("--replay-hours", type=int, default=24)
    p.add_argument("--replay-tape", default=TAPE_DIR)
    p.add_argument("--tokens", default=TOKENS_PARQUET)
    p.add_argument("--decide-from", help="replay: first decision hour YYYY-MM-DDTHH (earlier hours only build state)")
    p.add_argument("--decide-to", help="replay: end (exclusive) decision hour")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.model_manifest:
            models = ModelSet.from_manifest(args.model_manifest)
        elif args.model and args.model_sha256:
            models = ModelSet.single(args.model, args.model_sha256)
        else:
            print("refusing: --model with --model-sha256, or --model-manifest, is required", file=sys.stderr)
            return EXIT_USAGE
        ledger = AsofDirLedger(args.ledger_root) if args.ledger_root else None
        sink = JsonlSink(args.out_dir)
        engine = build_engine(ledger)
        if args.replay_from:
            return run_replay(args, models, engine, sink)
        return run_live(args, models, engine, sink)
    except Refused as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
