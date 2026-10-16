#!/usr/bin/env python3
"""r4-b walk-2 OUTCOME-BLIND precount and stop job (PLAN r4b-readtool section 4, builder task 6; DEC-030 draft 1a; QP fourth pass item 5).

What it is. A host-side hourly job. It opens only hours on a pinned allowlist, [H0, E + 1h), each sealed / strict-verified / sha256-matched / row-count-matched
(r4b_layout.check_hours), and appends ONE line per hour to counts.jsonl. It computes no rank, score, entry, landing state, guard, fill, exit, P&L, mean or
day sign. It never reads side, sol_lamports, token_raw, quote_reserve, base_reserve or trader.

Stop (QP ruling 2026-10-16, "QP fourth pass" item 5): CALENDAR. E = W0 + N * 24h = W0 + 480 h (N = 20). The job only records structure counts; the READ
checks >= 20 blocks with attempts and >= 125 attempts (fewer => NOT_DECIDABLE). Cap = earlier of W0 + 21 d and 2026-11-16T01 minus the 1 h exit horizon; a
config whose E is after the cap is refused. (An "attempts" stop mode existed in an earlier draft; it needed ranks of held tokens' pre-mark prints and was removed.)

Columns the job requests (r4b_layout.build(structure_only=True); r4b_layout.TRADE_COLS_STRUCT; a test pins the list)
  trades:      venue, mint, pool, slot, signature, event_index, tx_index, block_time, virtual_quote_reserves.
  creates:     type, mint, creator, is_mayhem_mode, slot, block_time.
  migrations:  type, mint, slot, block_time, tx_index, event_index, pool, quote_mint.
  NEVER requested: side, sol_lamports, token_raw, quote_reserve, base_reserve, trader.
  virtual_quote_reserves: parsed only to take (a) the s0 print's V0 VALUE (band membership, structure: the first print of the canonical pool) and (b) the boolean
  `virtual_quote_reserves IS NULL` for member prints at or after s0 (the null-V refusal). No other V value is selected by any structure-mode output; the
  candidate table's V column is dropped right after s0's value is taken.
  The scratch ut/ files hold (mid, slot, txi, evi, bt) only; the job reads (mid, slot, bt) for the live set and (txi, evi) for the null-tx diagnostics.

Marks. A mark is the start of a UTC hour, T. sd = first clock slot with block_time >= T. A member token is LIVE at T when T >= mbt + 600, T <= mbt + 48h,
and the latest block_time among its canonical-pool prints with slot < sd lies in [T - 600, T) (r4b_features.mint_features `ok`). From print timestamps and
counts only. decision_rows = live tokens with >= 3 prints before sd. live_md5 = md5 of the sorted lines "<mint>\\t<T>\\n" of the live set (mint address and T only).

SUBSET SEMANTICS (QP rules at the read-tool review). The live set is an upper bound of the feature rows: a feature row also needs >= 3 prints over the whole
mint (n < 3 => no row), positive reserves (QX, BX > 0) and the later session's grads rule, which use future prints or prices that this job must not read. So
the READ checks that its decision keys at each mark are a SUBSET of the live keys (not equal), and that the live_md5 it recomputes from the same structure
equals the stored one. The job's decision_rows is a count proxy, not the exact number of feature rows.

Grad-hour fields. The membership statistics of graduations in hour h-1 (PDA match, V0 band, members) are written on hour h's line (their s0 windows
(7300 slots) are then fully inside the loaded data, so a line never changes when later hours arrive). Hour H0's line has none.

Pending tail (EXTRA_HOURS = 2). The last two loaded hours of a run are PENDING: not appended, listed in the status as pending_hours, written by a later run once
two more hours are sealed and loaded (so a token completing near the end of the loaded range never silently loses membership in a counted line). The final two
allowlisted hours are written with extra_hours_loaded < 2 recorded. Create rows: per complete counted UTC date the share of member mints with no create row must
not exceed NO_CREATE_MAX_SHARE (0.06; QP ruling 1: exploration days reach 0.0513).

V is masked at load: the hour table holds only the boolean vq_null (virtual_quote_reserves IS NULL), the candidate table has no V, and V0 is a targeted
re-read of the s0 rows only (matched on the row number). No DuckDB table other than s0t, m0 and m holds a V value (a test pins the schemas), so spill files
cannot hold V. SPH: cap_pick_score.measure_hour_sph reads only slot and block_time, by regex, from the head and tail of the hour file.

THE READ MUST REFUSE if counts.jsonl lacks any hour in [H0, E], or if any line's extra_hours_loaded is not 2 (hours E-1 and E may carry 1 and 0, the final
allowlisted hours; nothing else may differ), or if the config.json pins differ (QP ruling 2).

Idempotent: a re-run rebuilds the adapter over [H0, through] and appends only hours not already in counts.jsonl; an existing line that differs from the rebuilt
one is a REFUSAL (drift), not an overwrite. config.json pins W0, N, A, H0 and the sha256 of this file and r4b_layout.py on first write; a later run with
different values refuses.

Refusals (exit 2, a REFUSED.jsonl line with every reason, nothing appended): an hour outside the allowlist; an hour missing / unsealed / unverified / sha256
or row-count mismatch / bad line (r4b_layout); a null V on a member print (r4b_layout); any day over the V0-missing share (r4b_layout.V0_MISSING_REFUSE_SHARE);
an hour with unmeasurable SPH; any (mint, slot) mixing null and non-null tx_index; and, when the stop is reached (or --final): PDA match < 95% of
non-Mayhem completes; fewer than 100 counted marks with >= 10 live tokens. Every run: any complete counted UTC date with graduations but 0 decisions.

Needs duckdb (/data/mal/audit-1008/venv) and solders (/data/mal/venv, for the PDA).
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import shutil
import sys
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

if __package__ in (None, ""):  # `python tools/r4b_count.py` from the repo root
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import exp012_forward as ef  # noqa: E402
from tools import r4b_frozen as FZ  # noqa: E402
from tools import r4b_layout as rl  # noqa: E402
from tools.latency_curve import _hour_file  # noqa: E402

SCHEMA = "r4b_count_v1"

# ---- pins -------------------------------------------------------------------------------------------------------------------------------
STOP_MODE = "calendar"  # QP fourth pass item 5: calendar. Recorded in config.json; there is no other mode.
N_BLOCKS = 20  # QP 2026-10-16: N=20 24 h blocks
A_ATTEMPTS = 125  # QP 2026-10-16: A=125 attempts (checked inside the read in calendar mode)
CAP_DAYS = 21
WALK_END = "2026-11-16T01"  # end of Forward walk 2
EXIT_HORIZON_H = 1
H0 = "2026-10-16T03"  # first allowlisted hour; 10-16T01/T02 are never opened
UNIVERSE_MIN_MBT = int(datetime(2026, 10, 16, 1, tzinfo=timezone.utc).timestamp())  # only pools created (graduated) at or after 10-16T01Z
MARK_S = 3600
LIVE_LO_S = 600
LIVE_HI_S = 48 * 3600
LIVE_WINDOW_S = 600
TOP_K = 10
MIN_PRINTS_DECISION = 3
PDA_MIN_SHARE = 0.95
# QP ruling 1 (#676): 0.06, because exploration days measure up to 0.0513. The largest share, per complete counted UTC date, of in-universe (member) mints that have no create row. Their create-derived constants are NaN
# downstream; above this the date is refused. A pinned constant, never tuned on results.
NO_CREATE_MAX_SHARE = 0.06
# Hours that must be loaded AFTER an hour before its line is written. A token completing near the end of the loaded range could otherwise lose (or not yet have)
# its membership (s0 print, V0 band) in a counted line. The last EXTRA_HOURS loaded hours of a run are PENDING: not appended, listed in the status, and written by
# a later run once the hours after them are sealed and loaded. Exception: the last allowlisted hour (E) has no later hour to wait for; the lines of the final
# EXTRA_HOURS allowlisted hours are written with `extra_hours_loaded` < EXTRA_HOURS recorded. (Pending, not refusal: an unsealed later hour is just not yet
# there; the layout still refuses anything sealed-but-bad.)
EXTRA_HOURS = 2
MIN_LIVE_TOKENS = 10
MIN_MARKS_WITH_LIVE = 100
LATENCIES_S = FZ.LATENCIES_S  # (1.3, 1.9)
BLOCK_S = 86400
COUNTS = "counts.jsonl"
REFUSED = "REFUSED.jsonl"
CONFIG = "config.json"


class CountRefused(Exception):
    def __init__(self, reasons: Sequence[str] | str):
        self.reasons = [reasons] if isinstance(reasons, str) else list(reasons)
        super().__init__("r4b_count refused: " + "; ".join(self.reasons))


@dataclass(frozen=True)
class Config:
    walk_dir: Path
    out_dir: Path
    w0: str  # UTC hour 'YYYY-MM-DDTHH' (the first counted hour; 00:00Z recommended so the blocks are UTC dates)
    n_blocks: int = N_BLOCKS
    a_attempts: int = A_ATTEMPTS
    h0: str = H0
    cap_days: int = CAP_DAYS
    walk_end: str = WALK_END
    exit_horizon_h: int = EXIT_HORIZON_H

    @property
    def w0_dt(self) -> datetime:
        return ef.hour_dt(self.w0)

    @property
    def w0_epoch(self) -> int:
        return int(self.w0_dt.timestamp())

    @property
    def cap_dt(self) -> datetime:
        """The cap instant: the earlier of W0 + cap_days and WALK_END - exit horizon."""
        return min(self.w0_dt + timedelta(days=self.cap_days), ef.hour_dt(self.walk_end) - timedelta(hours=self.exit_horizon_h))

    @property
    def calendar_stop_dt(self) -> datetime:
        return self.w0_dt + timedelta(days=0, seconds=self.n_blocks * BLOCK_S)

    def validate(self) -> None:
        bad = []
        try:
            w0, h0 = self.w0_dt, ef.hour_dt(self.h0)
        except ValueError as exc:
            raise CountRefused(f"bad hour: {exc}") from exc
        if w0 < h0:
            bad.append(f"W0 {self.w0} is before H0 {self.h0}")
        if self.n_blocks < 1 or self.a_attempts < 1:
            bad.append("N and A must be >= 1")
        if self.calendar_stop_dt > self.cap_dt:
            bad.append(f"calendar stop W0 + {self.n_blocks} d = {ef.hour_key(self.calendar_stop_dt)} is after the cap {ef.hour_key(self.cap_dt)}")
        if self.cap_dt < w0 + timedelta(seconds=self.n_blocks * BLOCK_S):
            bad.append(f"cap {ef.hour_key(self.cap_dt)} is before W0 + N blocks")
        if bad:
            raise CountRefused(bad)

    def allow_end(self) -> datetime:
        """Exclusive end of the allowlist: E + 1h."""
        return self.calendar_stop_dt + timedelta(hours=1)

    def allowlist(self) -> list[str]:
        return rl.hour_range(self.h0, ef.hour_key(self.allow_end()))

    def fingerprint(self) -> dict[str, Any]:
        return {"schema": SCHEMA, "w0": self.w0, "stop_mode": STOP_MODE, "n_blocks": self.n_blocks, "a_attempts": self.a_attempts, "h0": self.h0,
                "cap": ef.hour_key(self.cap_dt), "calendar_stop": ef.hour_key(self.calendar_stop_dt), "walk_end": self.walk_end,
                "exit_horizon_h": self.exit_horizon_h, "code_sha256": code_sha256()}


def code_sha256() -> dict[str, str]:
    here = Path(__file__).resolve()
    return {n: hashlib.sha256((here.parent / n).read_bytes()).hexdigest() for n in ("r4b_count.py", "r4b_layout.py")}


def hour_epoch(h: str) -> int:
    return int(ef.hour_dt(h).timestamp())


def utc_date(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d")


def stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def keys_md5(pairs: Sequence[tuple[str, int]]) -> str:
    """md5 of the sorted lines '<mint>\\t<T>\\n' (mint address + T only)."""
    h = hashlib.md5()
    for mint, t in sorted(pairs):
        h.update(f"{mint}\t{int(t)}\n".encode())
    return h.hexdigest()


# ---- stop rule (pure, on the lines) ------------------------------------------------------------------------------------------------------
def block_index(mark_t: int, w0_epoch: int) -> int:
    return (mark_t - w0_epoch) // BLOCK_S


def stop_status(lines: Sequence[Mapping[str, Any]], cfg: Config) -> dict[str, Any]:
    """The calendar stop: E = W0 + N*24h; STOP_REACHED once the line of hour E (the exit-horizon hour) exists. Only marks in [W0, E) are counted per block.
    The attempts check (>= N blocks with attempts, >= A attempts) is made inside the read, not here."""
    w0 = cfg.w0_epoch
    by_hour = {ln["hour"]: ln for ln in lines}
    e = cfg.calendar_stop_dt
    out: dict[str, Any] = {"stop_mode": STOP_MODE, "w0": cfg.w0, "n_blocks": cfg.n_blocks, "a_attempts": cfg.a_attempts, "cap": ef.hour_key(cfg.cap_dt),
                           "stop": ef.hour_key(e), "status": "STOP_REACHED" if ef.hour_key(e) in by_hour else "RUNNING", "attempts_total": None}
    e_epoch = int(e.timestamp())
    blocks: dict[int, dict[str, int]] = {}
    for ln in lines:
        t = ln["mark_t"]
        if w0 <= t < e_epoch:
            b = blocks.setdefault(block_index(t, w0), {"marks": 0, "marks_live10": 0, "live_tokens": 0, "decision_rows": 0})
            b["marks"] += 1
            b["marks_live10"] += int(ln["live_tokens"] >= MIN_LIVE_TOKENS)
            b["live_tokens"] += ln["live_tokens"]
            b["decision_rows"] += ln["decision_rows"]
    out["blocks"] = {str(k): v for k, v in sorted(blocks.items())}
    return out


def final_ready(status: Mapping[str, Any]) -> bool:
    return status["status"] == "STOP_REACHED"


# ---- aggregate precount refusals (pure, on the lines) -----------------------------------------------------------------------------------
def aggregate_problems(lines: Sequence[Mapping[str, Any]], cfg: Config, e_epoch: int | None) -> list[str]:
    """The refusals that need the whole counted span. `e_epoch` bounds the counted marks (None: no bound)."""
    w0 = cfg.w0_epoch
    probs = []
    nm = sum(ln.get("grad_non_mayhem") or 0 for ln in lines)
    pm = sum(ln.get("grad_pda_match") or 0 for ln in lines)
    if nm and pm / nm < PDA_MIN_SHARE:
        probs.append(f"PDA match {pm}/{nm} = {pm / nm:.4f} of non-Mayhem completes is below {PDA_MIN_SHARE}")
    if nm == 0:
        probs.append("no non-Mayhem completes seen: PDA match share is unmeasurable")
    counted = [ln for ln in lines if ln["mark_t"] >= w0 and (e_epoch is None or ln["mark_t"] < e_epoch)]
    n_live = sum(1 for ln in counted if ln["live_tokens"] >= MIN_LIVE_TOKENS)
    if n_live < MIN_MARKS_WITH_LIVE:
        probs.append(f"only {n_live} counted marks with >= {MIN_LIVE_TOKENS} live tokens (need {MIN_MARKS_WITH_LIVE})")
    probs += date_problems(lines, cfg, e_epoch)
    return probs


def date_problems(lines: Sequence[Mapping[str, Any]], cfg: Config, e_epoch: int | None) -> list[str]:
    """Any COMPLETE counted UTC date with graduations but 0 decision rows. A date d is complete when hour d+1 00 has its line (its last grad-hour 23 is
    reported there). Graduations are the members (grad_members) of grad hours on d; decisions are the hour-mark decision rows of marks on d."""
    by_hour = {ln["hour"]: ln for ln in lines}
    dates = sorted({utc_date(ln["mark_t"]) for ln in lines if ln["mark_t"] >= cfg.w0_epoch})
    probs = []
    for d in dates:
        nxt = (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%dT00")
        if nxt not in by_hour:
            continue
        if e_epoch is not None and int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) >= e_epoch:
            continue
        grads = sum(ln.get("grad_members") or 0 for ln in lines if ln.get("grad_hour") and ln["grad_hour"].startswith(d))
        noc = sum(ln.get("grad_members_no_create") or 0 for ln in lines if ln.get("grad_hour") and ln["grad_hour"].startswith(d))
        if grads and noc / grads > NO_CREATE_MAX_SHARE:
            probs.append(f"UTC date {d}: {noc}/{grads} = {noc / grads:.4f} of in-universe mints have no create row (above {NO_CREATE_MAX_SHARE})")
        dec = sum(ln["decision_rows"] for ln in lines if utc_date(ln["mark_t"]) == d and ln["mark_t"] >= cfg.w0_epoch)
        if grads > 0 and dec == 0:
            probs.append(f"UTC date {d}: {grads} graduations (members) but 0 decision rows")
    return probs


def per_day_report(lines: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per UTC date: hours, mean measured SPH, k at 1.3 / 1.9 s per hour (set), slot count (max slot - min slot over the hours' clocks), live and decisions."""
    days: dict[str, dict[str, Any]] = {}
    for ln in lines:
        d = utc_date(ln["mark_t"])
        r = days.setdefault(d, {"hours": 0, "sph": [], "k13": set(), "k19": set(), "slot_lo": None, "slot_hi": None, "live_tokens": 0, "decision_rows": 0})
        r["hours"] += 1
        r["sph"].append(ln["sph"])
        r["k13"].add(ln["k_1.3"])
        r["k19"].add(ln["k_1.9"])
        lo, hi = ln["slot_lo"], ln["slot_hi"]
        r["slot_lo"] = lo if r["slot_lo"] is None else min(r["slot_lo"], lo)
        r["slot_hi"] = hi if r["slot_hi"] is None else max(r["slot_hi"], hi)
        r["live_tokens"] += ln["live_tokens"]
        r["decision_rows"] += ln["decision_rows"]
    out = {}
    for d, r in sorted(days.items()):
        out[d] = {"hours": r["hours"], "sph_mean": sum(r["sph"]) / len(r["sph"]), "k_1.3": sorted(r["k13"]), "k_1.9": sorted(r["k19"]),
                  "slots": r["slot_hi"] - r["slot_lo"], "live_tokens": r["live_tokens"], "decision_rows": r["decision_rows"]}
    return out


# ---- the hour records --------------------------------------------------------------------------------------------------------------------
SphFn = Callable[[Path], "float | None"]


def default_sph(path: Path) -> float | None:
    from tools.cap_pick_score import measure_hour_sph

    return measure_hour_sph(path)


def k_pair(sph: float) -> tuple[int, int]:
    s = 3600.0 / sph
    return tuple(FZ.k_slots(lat, s) for lat in LATENCIES_S)  # type: ignore[return-value]


def _clock(con: Any, scratch: Path) -> tuple[Any, Any]:
    import numpy as np

    ck = con.sql(f"select bt, min(smin) smin from read_parquet('{scratch}/clock/*.parquet') group by 1 order by 1").fetchnumpy()
    return np.asarray(ck["bt"], np.int64), np.asarray(ck["smin"], np.int64)


def structure_hour_lines(scratch: Path, hours: Sequence[str], verify: Mapping[str, Mapping[str, Any]], sph_fn: SphFn, walk_dir: Path, cfg: Config,
                         stats: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """One record per hour in `hours` from the adapter's structure-only scratch. Returns (lines, problems). Calendar-mode fields; attempts mode adds to them."""
    import duckdb
    import numpy as np

    probs: list[str] = []
    con = duckdb.connect()
    con.sql("SET enable_progress_bar=false")
    ckb, cks = _clock(con, scratch)
    meta = con.sql(f"select mid, mint, mbt from read_parquet('{scratch}/meta.parquet') where mbt >= {UNIVERSE_MIN_MBT}").fetchall()
    meta_mid = {m[0]: m for m in meta}
    con.sql("create table mm(mid bigint, mint varchar, mbt bigint)")
    con.executemany("insert into mm values (?, ?, ?)", [(a, b, c) for a, b, c in meta])
    con.sql(f"create view ut as select mid, slot, txi, evi, bt from read_parquet('{scratch}/ut/*.parquet')")
    con.sql(f"create view m0 as select * from read_parquet('{scratch}/m0.parquet') where mbt >= {UNIVERSE_MIN_MBT}")
    lines = []
    for h in hours:
        t = hour_epoch(h)
        rec = verify[h]
        sph = sph_fn(_hour_file(walk_dir / "trades", "trades", h))
        if sph is None:
            probs.append(f"hour {h}: slots-per-hour is unmeasurable")
            continue
        i = int(np.searchsorted(ckb, t, "left"))
        if i >= len(ckb):
            probs.append(f"hour {h}: no clock entry at or after the mark {t}")
            continue
        sd = int(cks[i])
        live = con.sql(f"""select mm.mint, count(*) n_before from ut join mm using(mid)
                           where ut.slot < {sd} and {t} >= mm.mbt + {LIVE_LO_S} and {t} <= mm.mbt + {LIVE_HI_S}
                           group by mm.mint, mm.mid having max(ut.bt) >= {t - LIVE_WINDOW_S} and max(ut.bt) < {t} order by mm.mint""").fetchall()
        # slot lo / hi of the hour's clock seconds
        sl = con.sql(f"select min(smin), max(smax) from read_parquet('{scratch}/clock/{h}.parquet')").fetchone()
        tx = con.sql(f"""select count(*), coalesce(sum((txi < 0)::int), 0) from read_parquet('{scratch}/ut/{h}.parquet')""").fetchone()
        tie = con.sql(f"""select count(*) from (select 1 from read_parquet('{scratch}/ut/{h}.parquet') where txi < 0 group by mid, slot, evi having count(*) > 1)""").fetchone()[0]
        mixed = con.sql(f"""select count(*) from (select 1 from read_parquet('{scratch}/ut/{h}.parquet') group by mid, slot
                            having bool_or(txi < 0) and bool_or(txi >= 0))""").fetchone()[0]
        if mixed:
            probs.append(f"hour {h}: {mixed} (mint, slot) pair(s) mix null and non-null tx_index")
        prev = ef.hour_key(ef.hour_dt(h) - timedelta(hours=1))
        g = None
        if prev >= cfg.h0:
            p0 = hour_epoch(prev)
            g = con.sql(f"""select count(*), sum((not mayhem)::int), sum((not mayhem and pda_match)::int),
                            sum((not mayhem and pda_match and s0 is not null)::int),
                            sum((not mayhem and pda_match and s0 is not null and v0 between {rl.V_LO} and {rl.V_HI})::int)
                            from m0 where mbt >= {p0} and mbt < {p0 + 3600}""").fetchone()
            gm = con.sql(f"select count(*) from mm where mbt >= {p0} and mbt < {p0 + 3600}").fetchone()[0]
            gnc = con.sql(f"""select count(*) from mm join m0 using(mint) where mm.mbt >= {p0} and mm.mbt < {p0 + 3600} and not m0.has_create""").fetchone()[0]
        line = {
            "schema": SCHEMA, "hour": h, "mark_t": t, "sd": sd, "sha256": {k: rec["sha256"][k] for k in rl.SUBS}, "rows": {k: int(rec["content"][k]["rows"]) for k in rl.SUBS},
            "live_tokens": len(live), "decision_rows": sum(1 for _, n in live if n >= MIN_PRINTS_DECISION),
            "live_md5": keys_md5([(m, t) for m, _ in live]),
            "sph": sph, "k_1.3": k_pair(sph)[0], "k_1.9": k_pair(sph)[1], "slot_lo": int(sl[0]), "slot_hi": int(sl[1]),
            "ut_rows": int(tx[0]), "null_txi_rows": int(tx[1]), "null_txi_tie_groups": int(tie), "mixed_null_txi_slots": int(mixed),
            "grad_hour": prev if g is not None else None,
            "grad_members_no_create": None if g is None else int(gnc),
            "grad_completes": None if g is None else int(g[0]), "grad_non_mayhem": None if g is None else int(g[1] or 0),
            "grad_pda_match": None if g is None else int(g[2] or 0), "grad_with_s0": None if g is None else int(g[3] or 0),
            "grad_v0_in_band": None if g is None else int(g[4] or 0), "grad_members": None if g is None else int(gm),
        }
        line["grad_pda_match_share"] = None if not line["grad_non_mayhem"] else line["grad_pda_match"] / line["grad_non_mayhem"]
        line["grad_v0_band_share"] = None if not line["grad_with_s0"] else line["grad_v0_in_band"] / line["grad_with_s0"]
        lines.append(line)
    con.close()
    if stats.get("v0_missing_days_over"):
        probs.append(f"day(s) {stats['v0_missing_days_over']} have a share of candidate PDA pools with a missing V0 above {rl.V0_MISSING_REFUSE_SHARE}")
    if stats.get("mixed_null_txi_slots"):
        probs.append(f"{stats['mixed_null_txi_slots']} (mint, slot) pair(s) mix null and non-null tx_index over the read hours")
    return lines, probs


# ---- run ---------------------------------------------------------------------------------------------------------------------------------
def read_counts(out_dir: Path) -> list[dict[str, Any]]:
    p = out_dir / COUNTS
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def log_refused(out_dir: Path, reasons: Sequence[str], through: str | None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / REFUSED).open("a", encoding="utf-8") as f:
        f.write(stable_json({"refused": True, "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "through": through, "reasons": list(reasons)}) + "\n")


def run(cfg: Config, through: str | None = None, *, final: bool = False, sph_fn: SphFn | None = None, canonical_fn: rl.CanonFn | None = None,
        problems_fn: rl.ProblemsFn | None = None, verify: Mapping[str, Mapping[str, Any]] | None = None, keep_scratch: bool = False, memory_limit: str = "6GB", threads: int = 4) -> dict[str, Any]:
    """Process hours [H0, through] (through: inclusive hour; default the last allowlisted hour), append the new hours, return the status. Raises CountRefused
    (after writing a REFUSED line and appending nothing) on any refusal."""
    out_dir = Path(cfg.out_dir)
    try:
        return _run(cfg, through, final, sph_fn, canonical_fn, problems_fn, verify, keep_scratch, memory_limit, threads)
    except (CountRefused, rl.LayoutRefused) as exc:
        reasons = list(exc.reasons)
        log_refused(out_dir, reasons, through)
        raise CountRefused(reasons) from exc


def _run(cfg, through, final, sph_fn, canonical_fn, problems_fn, verify, keep_scratch, memory_limit, threads) -> dict[str, Any]:
    cfg.validate()
    out_dir = Path(cfg.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    allow = cfg.allowlist()
    through = through or allow[-1]
    probs = []
    if through not in allow:
        raise CountRefused(f"hour {through} is outside the allowlist [{allow[0]}, {allow[-1]}]")
    if through < cfg.h0:
        raise CountRefused(f"through {through} is before H0")
    hours = rl.hour_range(cfg.h0, ef.hour_key(ef.hour_dt(through) + timedelta(hours=1)))
    with (out_dir / ".lock").open("w") as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise CountRefused("another r4b_count run holds the lock") from exc
        # config pin
        fp = cfg.fingerprint()
        cp = out_dir / CONFIG
        if cp.exists():
            old = json.loads(cp.read_text(encoding="utf-8"))
            diff = sorted(k for k in set(old) | set(fp) if old.get(k) != fp.get(k))
            if diff:
                raise CountRefused(f"config.json differs from this run in {diff}; pins never change after the first write")
        existing = read_counts(out_dir)
        have = {ln["hour"] for ln in existing}
        scratch = out_dir / "scratch"
        shutil.rmtree(scratch, ignore_errors=True)
        try:
            stats = rl.build(Path(cfg.walk_dir), hours[0], ef.hour_key(ef.hour_dt(hours[-1]) + timedelta(hours=1)), scratch, v_source="event",
                             canonical_fn=canonical_fn, problems_fn=problems_fn, verify=verify, memory_limit=memory_limit, threads=threads,
                             tmp_dir=scratch / "tmp", structure_only=True)
            table = verify if verify is not None else ef.verified_hours(Path(cfg.walk_dir), strict_bad_lines=True)
            new_hours = [h for h in hours]
            lines, p2 = structure_hour_lines(scratch, new_hours, table, sph_fn or default_sph, Path(cfg.walk_dir), cfg, stats)
            probs += p2
        finally:
            if not keep_scratch:
                shutil.rmtree(scratch, ignore_errors=True)
        if probs:
            raise CountRefused(probs)
        by_hour_old = {ln["hour"]: ln for ln in existing}
        pending = hours[len(hours) - EXTRA_HOURS:] if EXTRA_HOURS and hours[-1] != allow[-1] else []
        if EXTRA_HOURS and hours[-1] == allow[-1]:
            for ln in lines:
                ln["extra_hours_loaded"] = min(EXTRA_HOURS, len(hours) - 1 - hours.index(ln["hour"]))
        elif EXTRA_HOURS:
            for ln in lines:
                ln["extra_hours_loaded"] = EXTRA_HOURS
        lines = [ln for ln in lines if ln["hour"] not in pending]
        drift = [ln["hour"] for ln in lines if ln["hour"] in by_hour_old and stable_json(by_hour_old[ln["hour"]]) != stable_json(ln)]
        if drift:
            raise CountRefused(f"line(s) for hour(s) {drift[:5]} differ from the rebuilt ones (drift); counts.jsonl is append-only and is never rewritten")
        fresh = [ln for ln in lines if ln["hour"] not in have]
        merged = sorted(existing + fresh, key=lambda x: x["hour"])
        status = stop_status(merged, cfg)
        if final or final_ready(status):
            e_epoch = int(ef.hour_dt(status["stop"]).timestamp()) if status.get("stop") else None
            agg = aggregate_problems(merged, cfg, e_epoch)
            if agg:
                raise CountRefused(agg)
            status["aggregate_ok"] = True
        else:
            part = date_problems(merged, cfg, None)
            if part:
                raise CountRefused(part)
        if not cp.exists():
            cp.write_text(json.dumps(fp, sort_keys=True, indent=1) + "\n", encoding="utf-8")
        with (out_dir / COUNTS).open("a", encoding="utf-8") as f:
            for ln in fresh:
                f.write(stable_json(ln) + "\n")
            f.flush()
        status["appended"] = [ln["hour"] for ln in fresh]
        status["pending_hours"] = pending
        status["hours_total"] = len(merged)
        status["per_day"] = per_day_report([ln for ln in merged if ln["mark_t"] >= cfg.w0_epoch])
        return status


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--walk-dir", required=True, help="dataset dir in walker layout (D/)")
    ap.add_argument("--out-dir", required=True, help="counts dir (counts.jsonl, config.json, REFUSED.jsonl); append-only")
    ap.add_argument("--w0", required=True, help="W0, YYYY-MM-DDTHH (first counted hour)")
    ap.add_argument("--through-hour", default=None, help="last hour to process, inclusive (default: the last allowlisted hour)")
    ap.add_argument("--final", action="store_true", help="also apply the aggregate refusals now")
    ap.add_argument("--memory-limit", default="6GB")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--keep-scratch", action="store_true")
    a = ap.parse_args(argv)
    cfg = Config(Path(a.walk_dir), Path(a.out_dir), a.w0)
    try:
        status = run(cfg, a.through_hour, final=a.final, keep_scratch=a.keep_scratch, memory_limit=a.memory_limit, threads=a.threads)
    except CountRefused as exc:
        print("REFUSED: " + "; ".join(exc.reasons), file=sys.stderr)
        return 2
    print(json.dumps(status, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
