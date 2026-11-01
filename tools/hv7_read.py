"""hv7_read: the pre-declared READ tool of Haiku trader v7 (HV7). PAPER ONLY. No API call, no key, no network, no tape.

docs/research/hv7/DESIGN.md sections 3, 4 and 9 (chunk C5) are the binding rules. This file implements them and invents none; where DESIGN is silent the choice is
named in AMBIGUITIES below.

Two reads, each ONCE per (bot, kind):

  gate <arm L|S>   at B: the first 00:00Z boundary with >= 100 counted attempts on >= 5 UTC dates (outcome-blind, arena_score.blind_read_instant over the arm's
                   entry decision stamps), and not before B + 61 min. The verdict is arena_score._score_bot itself (the DEC-031 scorer, not a copy): the deciding
                   cell is the 1.9 s leg at the registered deciding stake (0.25) in all four sub-cells (flat 15% and pressure scale 1, each under strand rules sc and
                   total); the scorer applies the 5% excluded + data-refused cap, incidents before B, restart orphans (PENDING under 24 h -> refused, older ->
                   NOT_DECIDABLE), the stored-vs-recomputed flat-leg check, and REPORT_ONLY when a strand rule is missing. HV7 adds, on top: E1 (A_L < 20 in
                   [T0, T0 + 2 d) -> NOT_DECIDABLE; fallback share > 10% in that window -> L NOT EVALUABLE), the L fallback share over [T0, B), the 10%
                   excluded-period cap (NOT_DECIDABLE), and REPORT-ONLY: day-block bootstrap, ex-best-day, per-trade p, other stakes and the 1.3 s leg, and the
                   DEC-031 Amendment 3 item 3 multiplicity block (N, 1 - 0.95^N, Holm rank).
  contrast         at T0 + H, and not before T0 + H + 61 min: L - U (primary, one-sided exact sign-flip on daily sums of per-period SOL differences at the deciding
                   stake, p <= 0.025 in each of the four sub-cells, >= 8 non-zero days else NOT_DECIDABLE `contrast_days`), the report-only secondaries L - R,
                   L - S, S - U, R - U (fault check, two-tailed) and the rank IC of L's order against U-priced returns, the Holm block, the fallback checks and the MDE.

Timing comes from the REGISTRATION (the bots.json lines written by hv7_register: clock_start_ms of the four books, the `hv7_halt` line = window end, the
deciding stake). Horizon, window end and record directory are not CLI flags. `--now-ms` and `--record-dir` exist only with MAL_ARENA_TEST=1, and a now later than
the wall clock is refused always.

A skill pass is not a promotion. Nothing here says a book makes money: no book has cleared the promotion gate. A gate PASS here is a SCREEN PASS; confirmation
(DEC-031 section 2 step 4) is owed and is not available in this window.

AMBIGUITIES resolved here (DESIGN is silent; QP rulings of PR #1021 applied):
  A1. Pressure intercept. The gate uses each arm's own refit (the scorer). The contrast uses ONE intercept per strand rule, refit on U's deciding-stake trades and
      applied to every arm (QP: ACCEPT, condition: deciding stake only).
  A2. Horizon. H is the registered window: (hv7_halt - clock_start) / 1 day. Since the 2026-10-30 pin revision (hv7_register: clocks 11-03..11-08, H = 14 days,
      the Am.4 formula at P0's rate) it is 14; the earlier 11-01/11-02 pins gave 10/9.
  A3. B per arm, from that arm's own entry decision stamps.
  A4. Contrast periods are keyed by the bot's own entry stamp (`stamp_ms.entry` = t_e), not the engine write instant. A period enters a pair only if both arms
      have a priced trade in it; U's value is the mean over its priced trades in the period. Dropped periods are reported per pair.
  A5. Report-only p of a book = max over the four sub-cells. Holm family = the secondaries; NOT_DECIDABLE secondaries are flagged in it. R - U fault check is
      two-tailed (either tail <= 0.025 in a decidable sub-cell).
  A6. 61 min after the last included decision.
  A7. (changed per QP) An attempt before B / T0 + H with no closing row: younger than the 24 h settle window -> Refused (PENDING); older -> restart orphan ->
      NOT_DECIDABLE (arena_score semantics).
  A8. The fallback denominator is L's non-excluded rank rows (hv7_count); L's entry count (DESIGN's A_L) is printed beside it and any difference is flagged.
      The 10% excluded-period cap is DEC-031's NOT_DECIDABLE (final), named so here.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from tools import arena_h5_read as H5
from tools import arena_score as AS
from tools import arena_stack as ST
from tools import hv4_read as H4
from tools import hv7_count as C
from tools import hv7_runner as R
from tools.arena_declog import utc_day
from tools.arena_paper import LEDGER_ROOT, REGISTRY_PATH, TEST_ENV, Ledger, load_registry

MIN_N, MIN_DAYS = AS.MIN_N, AS.MIN_DAYS
DAY_MS = AS.DAY_MS
EXIT_WINDOW_MS = 61 * 60_000
BOOT_DRAWS, BOOT_SEED, BOOT_PCT = 1000, 1, 5
CONTRAST_ALPHA = 0.025
CONTRAST_MIN_NONZERO_DAYS = 8
FALLBACK_LIMIT = 0.10
E1_WINDOW_MS = 2 * DAY_MS
T_E_MINUS_T_D_MS = R.T_E_OFFSET_MS - R.T_D_OFFSET_MS  # rank rows are stamped t_d, entries t_e (hv7_runner)
LAT = "1p9"
RULES = ("sc", "total")
CELLS = tuple(f"{m}_{LAT}_{r}" for m in ("flat", "press") for r in RULES)
ARMS = ("L", "R", "S", "U")
PROMOTABLE = ("L", "S")
MDE = {"D": 10, "lift_pct": {"s=8%": 1.9, "s=12%": 3.0}, "ic": 0.17, "power_null_at_D5": 0.0,
       "note": "QP simulation, DESIGN section 4 (arithmetic, not a measurement); E[max of 8 N(0,1)] = 1.4264"}
E_MAX_8 = 1.4264
SCREEN_PASS = "screen pass; confirmation owed, not available in this window"
NO_P_LIST = "Holm rank: not computed, no p list given"


class Refused(Exception):
    pass


# ------------------------------------------------------------------------------------------------------------------------------ registration
def load_registration(registry: str | os.PathLike) -> dict[str, Any]:
    """Timing and stake from the registry written by hv7_register: the four books must exist, share one clock and one deciding stake (R.STAKE_SOL), and each must
    carry an `hv7_halt` line (the window end) with the same value. Nothing here comes from a CLI flag."""
    reg = load_registry(registry)
    bots = [R.bot_id(a) for a in R.ARM_ORDER]
    missing = [b for b in bots if b not in reg]
    if missing:
        raise Refused(f"registry lacks HV7 books: {missing}")
    clocks = {reg[b]["clock_start_ms"] for b in bots}
    stakes = {float(reg[b]["deciding_stake"]) for b in bots}
    if len(clocks) != 1 or stakes != {float(R.STAKE_SOL)}:
        raise Refused(f"HV7 books disagree on clock_start_ms {sorted(clocks)} or deciding stake {sorted(stakes)} (pinned {R.STAKE_SOL})")
    halts: dict[str, set] = {b: set() for b in bots}
    for line in Path(registry).read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            if r.get("kind") == "hv7_halt" and r.get("bot_id") in halts:
                halts[r["bot_id"]].add(int(r["entries_halt_ms"]))
    allh = set().union(*halts.values())
    if len(allh) != 1 or any(len(v) != 1 for v in halts.values()):
        raise Refused(f"registry has no single registered hv7_halt for the four books ({ {b: sorted(v) for b, v in halts.items()} })")
    clock, halt = next(iter(clocks)), next(iter(allh))
    d = (halt - clock) // DAY_MS
    if d <= 0 or (halt - clock) % DAY_MS:
        raise Refused(f"registered window [{clock}, {halt}) is not a positive whole number of days")
    return {"clock_start_ms": clock, "halt_ms": halt, "D": int(d), "stake": float(R.STAKE_SOL), "registry": reg}


# ------------------------------------------------------------------------------------------------------------------------------ pure statistics
def sign_flip_min_p(d: int) -> float:
    """The smallest p an exact one-sided sign-flip can reach on d non-zero daily differences: 2^-d (d = 5: 0.03125 > 0.025, so power at B is exactly 0)."""
    return 2.0 ** -d


def sign_flip_p(diffs: Sequence[float]) -> float | None:
    """hv4_read.signflip_p (A1's exact test, imported not edited): exact for <= 20 non-zero days, ties count against the book."""
    return H4.signflip_p(diffs)


def day_block_boot_lo(x_sol: Sequence[float], days: Sequence[str], draws: int = BOOT_DRAWS, seed: int = BOOT_SEED, pct: float = BOOT_PCT) -> float | None:
    """REPORT-ONLY. Resample whole UTC days with replacement (as many as there are days); the draw's statistic is mean SOL per trade = sum of the resampled day sums /
    sum of the resampled day counts. Returns the 5th percentile over `draws` draws (default_rng(seed), one rng.integers call per draw)."""
    x = np.asarray(x_sol, float)
    if len(x) == 0:
        return None
    ud, inv = np.unique(np.asarray(days), return_inverse=True)
    sums = np.bincount(inv, weights=x)
    cnt = np.bincount(inv).astype(float)
    rng = np.random.default_rng(seed)
    means = np.empty(draws)
    for i in range(draws):
        idx = rng.integers(0, len(ud), len(ud))
        means[i] = sums[idx].sum() / cnt[idx].sum()
    return float(np.percentile(means, pct))


def mde_ic(lift_pct: float, s_pct: float) -> float:
    return lift_pct / (s_pct * E_MAX_8)


def _mde_vs_tab(d_days: int) -> str:
    """Direction of the untabulated MDE against the D = 10 figure: fewer days, less power (larger MDE); more days, more power (smaller MDE)."""
    return "larger than at D = 10" if d_days < MDE["D"] else "smaller than at D = 10"


def mde_line(d_days: int) -> str:
    if d_days == MDE["D"]:
        base = ("MDE (80% power at D = 10, QP simulation): per-trade lift about 1.9% (s = 8%) or about 3.0% (s = 12%), rank IC about 0.17; "
                f"this read has D = {d_days}, exact sign-flip minimum p = {sign_flip_min_p(d_days)}.")
        return base + " A non-pass is reported as 'no skill detected; MDE IC about 0.17', never as evidence of no skill below the MDE."
    base = (f"MDE: tabulated at D = 10 only (per-trade lift about 1.9% at s = 8%, about 3.0% at s = 12%, rank IC about 0.17). This read has D = {d_days}: the MDE is "
            f"not tabulated at D = {d_days} and is {_mde_vs_tab(d_days)}; exact sign-flip minimum p = {sign_flip_min_p(d_days)}.")
    return base + " A non-pass is 'no skill detected' and is never evidence of no skill below the MDE."


# ------------------------------------------------------------------------------------------------------------------------------ ledger rows -> vectors
def _stamp(r: Mapping[str, Any]) -> int:
    """The bot's own entry stamp (t_e). A trade row without stamp_ms.entry is refused: the write instant is not a period key (QP A4)."""
    s = (r.get("stamp_ms") or {}).get("entry")
    if not isinstance(s, int) or isinstance(s, bool):
        raise Refused(f"trade {r.get('trade_id')}: no stamp_ms.entry; contrast periods are keyed by the bot's own stamp")
    return s


def priced_trades(rows: Sequence[Mapping[str, Any]], start_ms: int, end_ms: int | None, stake: float) -> tuple[list[dict], dict[str, Any]]:
    """Priced `trade` rows AT THE DECIDING STAKE with entry decision in [start_ms, end_ms), after arena_score's exclusions (late_print gap overlap, missing V).
    The engine writes one trade row per stake per position; every other stake is dropped here. Also counts attempts, data refusals, exclusions and the unclosed
    opens (their entry instants, for the A7 pending / orphan split)."""
    def inside(r):
        e = AS._entry(r)
        return e >= start_ms and (end_ms is None or e < end_ms)

    def at_stake(r):
        return r.get("stake_sol") is not None and abs(float(r["stake_sol"]) - float(stake)) < 1e-12

    sel = [r for r in rows if "decision_ms" in r and inside(r)]
    opens = {r["position_id"]: r for r in sel if r["kind"] == "open"}
    closed = {r["position_id"] for r in sel if r["kind"] in ("trade", "excluded")}
    refused = [r for r in sel if r["kind"] == "refused"]
    late_gap = {r["position_id"] for r in sel if r["kind"] == "late_print" and r.get("gap_overlap")}
    trades, excl = [], 0
    for r in sel:
        if r["kind"] == "excluded" and at_stake(r):
            excl += 1
        elif r["kind"] == "trade" and at_stake(r):
            vs = r.get("v_src") or {}
            if r["position_id"] in late_gap or "none" in (vs.get("entry"), vs.get("exit")) or "none" in (vs.get("span") or {}):
                excl += 1
            else:
                trades.append(r)
    unclosed = sorted(AS._entry(opens[p]) for p in set(opens) - closed)
    data_refused = sum(1 for r in refused if r.get("reason") in AS.DATA_REFUSALS)
    info = {"attempts": len(opens) + len(refused), "refused": len(refused), "data_refused": data_refused, "excluded": excl, "unclosed": len(unclosed),
            "unclosed_entries": unclosed, "priced": len(trades)}
    return trades, info


def vectors(trades: Sequence[Mapping[str, Any]], intercepts: Mapping[str, float] | None = None) -> tuple[dict[str, np.ndarray], dict[str, float]]:
    """Per-trade lamport vectors of the four deciding sub-cells (1.9 s leg), arena_score._legs semantics. intercepts = a fixed pressure intercept per rule (A1) else
    each rule's own refit. Returns (cells, intercept used per rule)."""
    out: dict[str, np.ndarray] = {}
    used: dict[str, float] = {}
    if not trades:
        return {c: np.zeros(0) for c in CELLS}, used
    send = float(trades[0]["stack"]["send"])
    for rule in RULES:
        by = [r["calc"][LAT]["by_rule"][rule] for r in trades]
        pnl = np.array([b["pnl_nofail"] for b in by], float)
        fil = np.array([b["filled"] for b in by], bool)
        strand = np.array([b["strand"] for b in by], bool)
        ssb = np.array([r["calc"][LAT]["ssb"] for r in trades], float)
        near = np.array([r["calc"][LAT]["nearby_lamports"] for r in trades], float)
        ic = None if intercepts is None else intercepts[rule]
        flat, press, c, _ = AS._legs(rule, pnl, fil, strand, ssb, near, send, intercept=ic)
        out[f"flat_{LAT}_{rule}"], out[f"press_{LAT}_{rule}"], used[rule] = flat, press, float(c)
    return out, used


# ------------------------------------------------------------------------------------------------------------------------------ outcome-blind helpers
def entry_ms(root: str | Path, arm: str) -> list[int]:
    out = []
    for row, bad in C._rows(Path(root), arm):
        if not bad and row.get("kind") == "entry" and isinstance(row.get("ms"), int):
            out.append(row["ms"])
    return out


def entry_orders(root: str | Path, arm: str) -> list[tuple[int, str]]:
    """(ms, order_id) of every decision-log entry row. The runner logs the order id (= the engine's position id, asserted in hv7_runner) BEFORE the order is sent.
    A row without a string order_id is kept under a key no ledger position can carry, so it counts as lost, never as matched."""
    out = []
    for row, bad in C._rows(Path(root), arm):
        if not bad and row.get("kind") == "entry" and isinstance(row.get("ms"), int):
            oid = row.get("order_id")
            out.append((row["ms"], oid if isinstance(oid, str) and oid else f"?no-order-id@{row['ms']}"))
    return out


def _ledger_stamp(r: Mapping[str, Any]) -> int:
    """The bot's stamp of an entry row (the engine's position id hashes the stamp, and the decision log's ms is the stamp). No fallback to the write instant: a row
    without stamp_ms.entry is refused (a gate read turns that into a final NOT_DECIDABLE at T0 + H + 61 min)."""
    st = (r.get("stamp_ms") or {}).get("entry")
    if not isinstance(st, int) or isinstance(st, bool):
        raise Refused(f"ledger row {r.get('trade_id')} has no stamp_ms.entry: the decision-log reconciliation cannot place it")
    return st


def reconcile_ids(orders: Sequence[tuple[int, str]], rows: Sequence[Mapping[str, Any]], start_ms: int, end_ms: int, stake: float) -> dict[str, Any]:
    """The shared decision-log / ledger reconciliation of one arm over [start_ms, end_ms), by IDENTITY not count. Log set = order_ids of the entry rows; ledger set =
    position_ids of the open and refused rows (the engine writes `position_id`). lost = in the log, not the ledger (counts toward the 5% cap); foreign = in the
    ledger, never ordered by the log (final NOT_DECIDABLE). The attempt counts are the set sizes."""
    log_ids = {oid for ms, oid in orders if start_ms <= ms < end_ms}
    led_ids = {r["position_id"] for r in rows if r.get("kind") in ("open", "refused") and "decision_ms" in r and start_ms <= _ledger_stamp(r) < end_ms}
    # QP #1044: a position closed at another stake only (the scorer treats a trade row at ANY stake as closing it) is dropped from the deciding book by priced_trades, so its
    # id matching proves nothing. An open position with a trade/excluded row at some stake but none at the DECIDING stake is lost. (No close row at all stays the pending /
    # restart-orphan path.)
    def at_stake(r):
        return r.get("stake_sol") is not None and abs(float(r["stake_sol"]) - float(stake)) < 1e-12

    closed_any = {r["position_id"] for r in rows if r.get("kind") in ("trade", "excluded")}
    closed_dec = {r["position_id"] for r in rows if r.get("kind") in ("trade", "excluded") and at_stake(r)}
    open_ids = {r["position_id"] for r in rows if r.get("kind") == "open" and "decision_ms" in r and start_ms <= _ledger_stamp(r) < end_ms}
    no_dec = sorted((open_ids & log_ids) & (closed_any - closed_dec))
    lost, foreign = sorted((log_ids - led_ids) | set(no_dec)), sorted(led_ids - log_ids)
    return {"no_deciding_stake_row": len(no_dec), "no_deciding_stake_row_sample": no_dec[:3], "decision_log_attempts": len(log_ids), "ledger_attempts": len(led_ids), "lost_attempts": len(lost), "foreign_ledger_positions": len(foreign),
            "extra_ledger_attempts": len(foreign), "lost_ids_sample": lost[:3], "foreign_ids_sample": foreign[:3]}


NO_DEC_WHY = "closing rows missing at the deciding stake"
FOREIGN_WHY = "ledger holds positions the decision log never ordered"


def _need_orders(orders: Sequence[tuple[int, str]] | None, ms: Sequence[int], what: str) -> Sequence[tuple[int, str]]:
    if orders is None:
        raise Refused(f"{what}: no decision-log order ids given: the decision log / ledger reconciliation matches ids, not counts")
    if sorted(m for m, _ in orders) != sorted(ms):
        raise Refused(f"{what}: the order ids and the entry instants are not the same decision-log rows")
    return orders


def fallback_check(root: str | Path, start_ms: int, end_ms: int) -> dict[str, Any]:
    """hv7_count over [start_ms, end_ms): the L fallback rule (> 10% L-evaluable no), the 10% excluded-period cap (NOT_DECIDABLE, all arms) and the A8 comparison
    of L's entry count (A_L) with its non-excluded rank rows."""
    c = C.count(root, clock_start_ms=start_ms, clock_end_ms=end_ms)
    l_ne = bool(c["fallback_share_gt_10pct"])
    cap = bool(c["not_evaluable"])
    return {"window_ms": [start_ms, end_ms], "attempts_L": c["a_l"], "rank_rows": c["rank_rows"], "fallbacks": c["fallbacks"], "fallback_share": c["fallback_share"],
            "L_not_evaluable": l_ne, "excluded_share": c["excluded_share"], "excluded_cap_not_decidable": cap,
            "entry_rows_minus_rank_rows": c["a_l"] - c["rank_rows"], "entry_vs_rank_row_difference": c["a_l"] != c["rank_rows"],
            "reason": ("E_fallback" if l_ne else None) or ("excluded_periods_cap_10pct (NOT_DECIDABLE)" if cap else None)}


def e1_check(root: str | Path, clock_start_ms: int) -> dict[str, Any]:
    """DESIGN section 4 E1, recomputed with hv7_count over [T0, T0 + 2 d): A_L < 20 -> every read NOT_DECIDABLE; fallback share > 10% -> L's reads NOT EVALUABLE.
    count_sha256 = sha256 of the sorted-key JSON of the hv7_count output, to match the notebook copy."""
    c = C.count(root, clock_start_ms=clock_start_ms, clock_end_ms=clock_start_ms + E1_WINDOW_MS)
    sha = hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()
    return {"window_ms": [clock_start_ms, clock_start_ms + E1_WINDOW_MS], "a_l": c["a_l"], "a_l_lt_20": bool(c["a_l_lt_20"]), "rank_rows": c["rank_rows"],
            "fallbacks": c["fallbacks"], "fallback_share": c["fallback_share"], "fallback_gt_10pct": bool(c["fallback_share_gt_10pct"]), "count_sha256": sha}


def coverage_check(root: str | Path, start_ms: int, end_ms: int) -> dict[str, Any]:
    """REPORT-ONLY, outcome-blind. Separates OUTAGE hours (no row in the SCAN log and none in any arm log: the runner was not writing) from NO-TRADE hours
    (the runner wrote SCAN rows but this arm has no entry row). Whole-hour outages leave no excluded-period row, so the 10% cap does not see them."""
    hours = set(range(start_ms // 3_600_000, (end_ms + 3_599_999) // 3_600_000))

    def seen(name):
        out = set()
        for row, bad in C._rows(Path(root), name):
            m = None if bad else row.get("ms")
            if isinstance(m, int) and not isinstance(m, bool) and start_ms <= m < end_ms:
                out.add(m // 3_600_000)
        return out & hours

    scan = seen("SCAN")
    arms = {a: seen(a) for a in R.ARM_ORDER}
    up = scan.union(*arms.values())
    out: dict[str, Any] = {"report_only": True, "window_hours": len(hours), "outage_hours": len(hours - up), "runner_up_hours": len(up),
                           "note": "outage hours = no SCAN row and no arm row; they fall outside the 10% excluded-period cap"}
    for a in R.ARM_ORDER:
        out[a] = {"hours_with_entry_rows": len(arms[a]), "no_trade_hours": len(up - arms[a])}
    return out


def _need_e1(e1: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if e1 is None:
        raise Refused("E1 not supplied: the read needs hv7_count over [T0, T0 + 2 d)")
    return e1


def holm_block(n_books: int, ps: Mapping[str, float] | None, *, flags: Mapping[str, str] | None = None) -> dict[str, Any]:
    """DEC-031 Am.3 item 3: N = every book with a clock before the read, the plain line 1 - 0.95^N, and the report-only p with its Holm rank. Nothing here decides.
    ps None -> no p list was given: the rank line says so."""
    out: dict[str, Any] = {"N": n_books, "plain_line_1_minus_0.95_pow_N": 1 - 0.95 ** n_books, "report_only": True}
    if ps is None:
        out["holm"] = {}
        out["holm_text"] = NO_P_LIST
    else:
        out["holm"] = H5.holm_rank(dict(ps)) if ps else {}
    if flags:
        out["flags"] = dict(flags)
    return out


# ------------------------------------------------------------------------------------------------------------------------------ integrity of one arm's rows
def arm_integrity(rows: Sequence[Mapping[str, Any]], incidents: Sequence[Mapping[str, Any]], problems: Sequence[str], start_ms: int, end_ms: int, now_ms: int,
                  stake: float, entry_orders: Sequence[tuple[int, str]] = ()) -> dict[str, Any]:
    """The arena_score integrity rules applied to one contrast arm over [start, end) (a copy, because the scorer cannot be forced past a bot's natural instant;
    the bonding and stored flat-leg rules are applied separately in contrast_read, so the two must be kept in step by hand): pending (<24 h, caller refuses), restart orphans, excluded + data-refused share vs 5%,
    incidents before end, damaged lines. `reasons` are NOT_DECIDABLE causes."""
    _, info = priced_trades(rows, start_ms, end_ms, stake)
    ages = [now_ms - e for e in info["unclosed_entries"]]
    orphans = sum(1 for a in ages if a >= AS.SETTLE_MS)
    pending = len(ages) - orphans
    share = (info["excluded"] + info["data_refused"]) / info["attempts"] if info["attempts"] else None
    inc_before = sum(1 for i in incidents if AS.incident_instant(i) < end_ms)
    reasons = []
    rec = reconcile_ids(entry_orders, rows, start_ms, end_ms, stake)
    a_dec, lost = rec["decision_log_attempts"], rec["lost_attempts"]
    if rec["foreign_ledger_positions"]:
        reasons.append(f"{FOREIGN_WHY} ({rec['foreign_ledger_positions']} foreign; decision log {a_dec}, ledger {rec['ledger_attempts']}, lost {lost})")
    if rec["no_deciding_stake_row"]:
        reasons.append(f"{NO_DEC_WHY} ({rec['no_deciding_stake_row']} position(s) closed at another stake only)")
    if lost > 0 and a_dec:
        lshare = (info["excluded"] + info["data_refused"] + lost) / a_dec
        rec["excluded_lost_share"] = lshare
        if lshare > AS.MAX_EXCLUDED_SHARE:
            reasons.append(f"excluded + data-refused + lost share {lshare:.4%} above {AS.MAX_EXCLUDED_SHARE:.0%} ({lost} attempt(s) lost between decision log and ledger)")
    if orphans:
        reasons.append(f"{orphans} restart orphan(s)")
    if share is not None and share > AS.MAX_EXCLUDED_SHARE:
        reasons.append(f"excluded + data-refused share {share:.4%} above {AS.MAX_EXCLUDED_SHARE:.0%}")
    if inc_before:
        reasons.append(f"{inc_before} ledger incident(s) before the window end")
    if problems:
        reasons.append(f"{len(problems)} torn or unparseable ledger line(s)")
    return {**rec, "attempts": info["attempts"], "priced": info["priced"], "excluded": info["excluded"], "data_refused": info["data_refused"], "excluded_share": share,
            "pending": pending, "restart_orphans": orphans, "incidents_before_end": inc_before, "ledger_problems": len(problems), "reasons": reasons}


# ------------------------------------------------------------------------------------------------------------------------------ the gate read
def natural_ledger_instant(rows: Sequence[Mapping[str, Any]], clock_start_ms: int) -> int | None:
    return AS.blind_read_instant([AS._entry(r) for r in rows if r.get("kind") in ("open", "refused") and "decision_ms" in r and AS._entry(r) >= clock_start_ms], clock_start_ms)


def gate_read(arm: str, *, now_ms: int, regn: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], entry_decisions_ms: Sequence[int], e1: Mapping[str, Any] | None,
              entry_orders: Sequence[tuple[int, str]] | None = None, fallback: Mapping[str, Any] | None = None, incidents: Sequence[Mapping[str, Any]] = (), ledger_problems: Sequence[str] = (), n_books: int,
              other_ps: Mapping[str, float] | None = None, coverage: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The L (or S) gate at B. The verdict is arena_score._score_bot. Raises Refused before B + 61 min and while an attempt before B is PENDING (< 24 h)."""
    if arm not in PROMOTABLE:
        raise Refused(f"arm {arm}: only L and S are promotable; R and U have no gate")
    e1 = _need_e1(e1)
    entry_orders = _need_orders(entry_orders, entry_decisions_ms, f"{arm}")
    bot = R.bot_id(arm)
    reg = regn["registry"]
    clock, halt, stake = regn["clock_start_ms"], regn["halt_ms"], regn["stake"]
    b = AS.blind_read_instant(list(entry_decisions_ms), clock)  # outcome-blind: decision stamps only
    base: dict[str, Any] = {"arm": arm, "bot_id": bot, "now_ms": now_ms, "clock_start_ms": clock, "window_end_ms": halt, "D": regn["D"], "deciding_stake": stake,
                            "e1": dict(e1), "no_book_has_cleared_the_promotion_gate": True}
    if b is None:
        if now_ms >= halt + EXIT_WINDOW_MS:
            return dict(base, status="NOT_DECIDABLE", reason="B_not_reached_by_T0_plus_H", read_boundary_ms=None)
        raise Refused(f"{arm}: no read boundary yet (fewer than {MIN_N} counted attempts on {MIN_DAYS} UTC dates); the gate is not read before B")
    if b > halt:
        return dict(base, status="NOT_DECIDABLE", reason="B_not_reached_by_T0_plus_H", read_boundary_ms=b)
    if now_ms < b + EXIT_WINDOW_MS:
        raise Refused(f"{arm}: now {now_ms} is before B + 61 min ({b + EXIT_WINDOW_MS}); the gate is not read before every attempt decided before B has exited")
    natural = natural_ledger_instant(rows, clock)
    try:
        ids = reconcile_ids(entry_orders, rows, clock, b, stake)
    except Refused as e:
        if now_ms < halt + EXIT_WINDOW_MS:
            raise
        base["read_boundary_ms"] = b
        base["multiplicity"] = holm_block(n_books, None if other_ps is None else dict(other_ps))
        return dict(base, status="NOT_DECIDABLE", reasons=[f"{e}; final at T0 + H + 61 min"])
    a_dec, a_led, lost, foreign = ids["decision_log_attempts"], ids["ledger_attempts"], ids["lost_attempts"], ids["foreign_ledger_positions"]
    recon = {"decision_log_attempts_before_b": a_dec, "ledger_attempts_before_b": a_led, "lost_attempts": lost, "foreign_ledger_positions": foreign,
             "extra_ledger_attempts": foreign, "lost_ids_sample": ids["lost_ids_sample"], "no_deciding_stake_row": ids["no_deciding_stake_row"], "foreign_ids_sample": ids["foreign_ids_sample"],
             "lost_share": (lost / a_dec) if a_dec else None, "matched_by": "decision-log order_id == ledger position_id",
             "ledger_natural_boundary_ms": natural, "read_boundary_ms": b}
    base["attempt_reconciliation"] = recon
    base["read_boundary_ms"] = b
    base["coverage"] = dict(coverage) if coverage else None
    own_nd: list[str] = []
    if e1["a_l_lt_20"]:
        own_nd.append("E1_entry_rate (A_L < 20 in [T0, T0 + 2 d))")

    def final_nd(why: str) -> dict[str, Any]:
        base["multiplicity"] = holm_block(n_books, None if other_ps is None else dict(other_ps))
        return dict(base, status="NOT_DECIDABLE", reasons=own_nd + [why])

    if foreign:
        return final_nd(f"{FOREIGN_WHY} ({foreign} foreign position id(s) before B {b}; decision log {a_dec}, ledger {a_led}, lost {lost}); final")
    if ids["no_deciding_stake_row"]:
        return final_nd(f"{NO_DEC_WHY} ({ids['no_deciding_stake_row']} position(s) closed at another stake only, before B {b}; lost {lost} of {a_dec} decision-log attempts); final")
    if natural is not None and natural < b:
        return final_nd(f"decision log and ledger disagree: the ledger reached its own instant {natural} before the decision-log B {b} (decision log {a_dec}, ledger {a_led}); final")
    if natural is None or natural > b:  # the ledger is short: the scorer cannot be run at the ledger's own instant
        if natural is None:
            if a_dec and lost / a_dec > AS.MAX_EXCLUDED_SHARE:
                return final_nd(f"{lost} of {a_dec} decision-log attempts before B are lost to the ledger ({lost / a_dec:.4%} above 5%) and the ledger never reaches its own instant")
            if now_ms >= halt + EXIT_WINDOW_MS:
                return final_nd(f"the ledger never reached its own outcome-blind instant by T0 + H + 61 min (decision log {a_dec}, ledger {a_led} attempts before B, lost {lost})")
            raise Refused(f"{arm}: the ledger has not reached its own instant (decision log {a_dec}, ledger {a_led}, lost {lost} attempts before B); final NOT_DECIDABLE at "
                          f"T0 + H + 61 min = {halt + EXIT_WINDOW_MS} if it does not")
    sc = AS._score_bot(bot, reg, rows, now_ms, None, AS.SETTLE_MS, None if natural == b else b, incidents, None, None)
    if sc.get("pending"):
        raise Refused(f"{arm}: {sc['pending']} attempt(s) before B are PENDING (younger than the 24 h settle window): not read; an older one would be a restart orphan (NOT_DECIDABLE)")
    res = dict(base, ledger_natural_boundary_ms=natural, boundary_difference=natural != b, boundary_source=sc["boundary_source"],
               scorer_status_before_hv7_rules=sc["status"], scorer_reasons=list(sc["reasons"]), scorer_warnings=list(sc["warnings"]), attempts_before_b=sc.get("attempts"),
               restart_orphans=sc.get("restart_orphans"), refused_buys=sc.get("refused_buys"), incidents=sc.get("incidents"), fallback_check=dict(fallback) if fallback else None)
    if natural != b:
        res["boundary_note"] = (f"decision-log B {b} differs from the ledger's natural instant {natural} (e.g. entry_not_in_engine gap rows); the scorer was run at the "
                                "decision-log B")
    nd: list[str] = list(own_nd)
    ne: list[str] = []
    book = (sc.get("books") or {}).get(str(stake))
    if lost > 0 and a_dec:  # DEC-031 1.4: lost attempts join the numerator, the decision log is the denominator (also when the deciding-stake book is absent)
        num = (book["excluded_share"] * sc["attempts"]) if book and book.get("excluded_share") is not None else float(sc.get("refused_data_issues") or 0)
        share = (num + lost) / a_dec
        res["excluded_lost_share"] = share
        if share > AS.MAX_EXCLUDED_SHARE:
            nd.append(f"excluded + data-refused + lost share {share:.4%} above {AS.MAX_EXCLUDED_SHARE:.0%} ({lost} attempt(s) lost between decision log and ledger)")
    if sc["status"] == "NOT_DECIDABLE":
        nd += list(sc["reasons"])
    if ledger_problems:
        nd.append(f"{len(ledger_problems)} torn or unparseable ledger line(s)")
    if fallback and fallback.get("excluded_cap_not_decidable"):
        nd.append("excluded periods above the 10% cap (NOT_DECIDABLE)")
    if arm == "L":
        if e1["fallback_gt_10pct"]:
            ne.append("E1_fallback (fallback share > 10% in [T0, T0 + 2 d))")
        if fallback and fallback.get("L_not_evaluable"):
            ne.append("E_fallback (fallback share > 10% over [T0, B))")
    ps: list[float] = []
    if sc["status"] in ("PASS", "FAIL") and sc.get("deciding"):
        trades, info = priced_trades(rows, clock, b, stake)
        sbook = sc["books"][str(stake)]
        cells_x, used = vectors(trades)
        days = [utc_day(AS._entry(r)) for r in trades]
        cells = {}
        for key in CELLS:
            c = sbook["cells"][key]
            if c["n"] != len(trades):
                raise Refused(f"internal: scorer n {c['n']} != deciding-stake trades {len(trades)} in {key}")
            x = cells_x[key]
            bp = H5.boot_p(x / 1e9)
            ps.append(bp["p"] if bp else 1.0)
            cells[key] = {**{k: c[k] for k in ("n", "days", "days_pos", "mean_sol", "total_sol", "ci90_lo_sol", "ex_top3_sol", "ex_best_day_sol", "pass", "misses")},
                          "daily_sol": {d: v["sol"] for d, v in c["daily"].items()}, "intercept": c.get("intercept"),
                          "day_block_ci90_lo_sol": {"report_only": True, "value": day_block_boot_lo(x / 1e9, days)}, "report_only_boot_p": bp["p"] if bp else None}
        res.update(cells=cells, press_intercepts=used, priced_trades=info["priced"], excluded=info["excluded"], report_only_p=max(ps),
                   not_proposed_for_canary=sc["deciding"]["not_proposed_for_canary"], weakest_cell=sc["deciding"]["weakest_cell"])
        if sc.get("fail_label"):
            res["fail_label"] = sc["fail_label"]
        res["report_only_cells"] = [{"stake": bk["stake"], "cell": k, "n": c["n"], "mean_sol": c["mean_sol"], "total_sol": c["total_sol"], "ci90_lo_sol": c["ci90_lo_sol"]}
                                   for bk in sc["books"].values() for k, c in bk.get("cells", {}).items() if not (bk["stake"] == stake and f"_{LAT}_" in k)]
        if lost > 0:  # REPORT-ONLY: every lost attempt imputed as -stake - round-trip fees (the scorer's legs: 2 x send + deciding rent); days not imputed
            send = float(trades[0]["stack"]["send"])
            imp = -stake * 1e9 - 2 * send - float(ST.F.RENT_DECIDING)
            wc, survives = {}, True
            for key in CELLS:
                x = np.concatenate([cells_x[key], np.full(lost, imp)]) / 1e9
                srt = np.sort(x)[::-1]
                lo, ex3 = AS.boot_lo(x), float(srt[3:].sum())
                wc[key] = {"n": int(len(x)), "mean_sol": float(x.mean()), "total_sol": float(x.sum()), "ci90_lo_sol": lo, "ex_top3_sol": ex3}
                survives = survives and bool(lo is not None and lo > 0 and ex3 > 0 and x.sum() > 0)
            res["lost_attempts_worst_case"] = {"report_only": True, "lost": lost, "imputed_pnl_sol_each": imp / 1e9, "send_lamports": send, "cells": wc,
                                               "per_trade_cells_survive": survives, "day_rule_imputed": False,
                                               "note": "each lost attempt imputed as -stake - round-trip fees (2 x send + deciding rent, the fee legs arena_score uses; no proceeds); for a lost attempt that was sent and stranded this is slightly optimistic (a stranded position loses -stake - send - 2,039,280 rent, about 0.0015 SOL more); per_trade_cells_survive "
                                                       "covers the per-trade cell tests only (CI lower bound, ex-top-3, total); the 5-day and majority-positive-day rules are NOT imputed; "
                                                       "a DEC-032 canary proposal should quote this line"}
    elif sc["status"] == "REPORT_ONLY":
        res["report_only_reasons"] = list(sc["reasons"])
    if nd:
        res.update(status="NOT_DECIDABLE", reasons=nd)
    elif ne:
        res.update(status="NOT_EVALUABLE", reasons=ne)
    else:
        res["status"] = sc["status"]
        if sc["status"] == "PASS":
            res["status_text"] = SCREEN_PASS
            res["note"] = "PASS here is the in-sample gate read only (DEC-031 section 2); canary proposals also need ex-best-day > 0 in every sub-cell (DEC-032 section 1)"
    if res["status"] != sc["status"]:
        res["cells_not_a_verdict"] = f"the cells are the scorer's numbers before the HV7 rules and are not a verdict; the read is {res['status']}, not {sc['status']}"
    own = {bot: max(ps)} if ps and res["status"] in ("PASS", "FAIL") else {}
    res["multiplicity"] = holm_block(n_books, None if other_ps is None else {**other_ps, **own})
    return res


# ------------------------------------------------------------------------------------------------------------------------------ contrasts
def period_values(trades: Sequence[Mapping[str, Any]], x: np.ndarray) -> dict[int, float]:
    """period (the bot's own entry stamp t_e, ms) -> mean lamports over this arm's priced trades in it (one trade for L, R, S; N for U)."""
    acc: dict[int, list[float]] = {}
    for r, v in zip(trades, x):
        acc.setdefault(_stamp(r), []).append(float(v))
    return {k: float(np.mean(v)) for k, v in acc.items()}


def window_days(t0_ms: int, h_end_ms: int) -> list[str]:
    d0 = t0_ms // DAY_MS * DAY_MS
    return [utc_day(d) for d in range(d0, h_end_ms, DAY_MS)]


def daily_diff(pa: Mapping[int, float], pb: Mapping[int, float], days: Sequence[str]) -> tuple[np.ndarray, int]:
    common = sorted(set(pa) & set(pb))
    dsum = {d: 0.0 for d in days}
    for ms in common:
        d = utc_day(ms)
        if d not in dsum:  # unreachable for rows filtered to the window (a stamp can trail its write instant only by the lag); kept as a guard
            raise Refused(f"period {ms} on {d} is outside the read window")
        dsum[d] += (pa[ms] - pb[ms]) / 1e9
    return np.array([dsum[d] for d in days], float), len(common)


def spearman(a: Sequence[float], b: Sequence[float]) -> float | None:
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 3:
        return None
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    for v, r in ((a, ra), (b, rb)):  # average ranks for ties
        for u in np.unique(v):
            m = v == u
            if m.sum() > 1:
                r[m] = r[m].mean()
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def rank_ic_daily(l_rank_rows: Sequence[Mapping[str, Any]], u_trades: Sequence[Mapping[str, Any]], u_x: np.ndarray, days: Sequence[str]) -> tuple[np.ndarray, int]:
    """REPORT-ONLY. Per period with a non-fallback L order: Spearman of L's rank (rank 1 best) against U's per-pool return in that period; daily mean IC (0 on a day
    with none). U-priced by pool at t_e = rank row ms + (hv7_runner t_e - t_d), matched to U's own entry stamp."""
    ret: dict[tuple[int, str], float] = {}
    for r, v in zip(u_trades, u_x):
        ret[(_stamp(r), r["pool"])] = float(v)
    by_day: dict[str, list[float]] = {d: [] for d in days}
    n = 0
    for rr in l_rank_rows:
        if rr.get("fallback") or rr.get("excluded") or not rr.get("order"):
            continue
        t_e = int(rr["ms"]) + T_E_MINUS_T_D_MS
        order = list(rr["order"])
        pairs = [(len(order) - i, ret[(t_e, p)]) for i, p in enumerate(order) if (t_e, p) in ret]  # score N for rank 1 ... 1 for last
        ic = spearman([a for a, _ in pairs], [b for _, b in pairs]) if len(pairs) == len(order) else None
        d = utc_day(t_e)
        if ic is not None and d in by_day:
            by_day[d].append(ic)
            n += 1
    return np.array([float(np.mean(by_day[d])) if by_day[d] else 0.0 for d in days], float), n


def contrast_cell(pa: Mapping[str, Mapping[int, float]], pb: Mapping[str, Mapping[int, float]], days: Sequence[str], *, two_tailed: bool = False) -> dict[str, Any]:
    subs, ps = {}, []
    for key in CELLS:
        d, n = daily_diff(pa[key], pb[key], days)
        nz = int((d != 0).sum())
        p = sign_flip_p(d)
        s = {"p": p, "nonzero_days": nz, "periods": n, "periods_dropped_a": len(pa[key]) - n, "periods_dropped_b": len(pb[key]) - n,
             "mean_daily_diff_sol": float(d.mean()), "total_diff_sol": float(d.sum()), "days_positive": int((d > 0).sum()), "min_p": sign_flip_min_p(nz)}
        if two_tailed:
            s["p_other_tail"] = sign_flip_p(-d)
        subs[key] = s
        ps.append(1.0 if p is None else p)
    nz_min = min(s["nonzero_days"] for s in subs.values())
    dropped = {"a": subs[CELLS[0]]["periods_dropped_a"], "b": subs[CELLS[0]]["periods_dropped_b"]}
    if nz_min < CONTRAST_MIN_NONZERO_DAYS:
        return {"status": "NOT_DECIDABLE", "reason": "contrast_days", "min_nonzero_days": nz_min, "subcells": subs, "p": max(ps), "periods_dropped": dropped}
    ok = all(s["p"] is not None and s["p"] <= CONTRAST_ALPHA for s in subs.values())
    return {"status": "PASS" if ok else "NOT_PASS", "min_nonzero_days": nz_min, "subcells": subs, "p": max(ps), "periods_dropped": dropped}


def contrast_read(*, now_ms: int, regn: Mapping[str, Any], rows_by_arm: Mapping[str, Sequence[Mapping[str, Any]]], l_rank_rows: Sequence[Mapping[str, Any]],
                  fallback: Mapping[str, Any], e1: Mapping[str, Any] | None, n_books: int, incidents_by_arm: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
                  problems_by_arm: Mapping[str, Sequence[str]] | None = None, other_ps: Mapping[str, float] | None = None,
                  coverage: Mapping[str, Any] | None = None, entry_ms_by_arm: Mapping[str, Sequence[int]] | None = None,
                  entry_orders_by_arm: Mapping[str, Sequence[tuple[int, str]]] | None = None) -> dict[str, Any]:
    """L - U and the secondaries, once, at T0 + H. Raises Refused before T0 + H + 61 min and on any PENDING attempt (< 24 h); an older unclosed attempt is a restart
    orphan -> NOT_DECIDABLE."""
    e1 = _need_e1(e1)
    clock, h_end, stake = regn["clock_start_ms"], regn["halt_ms"], regn["stake"]
    if now_ms < h_end + EXIT_WINDOW_MS:
        raise Refused(f"now {now_ms} is before T0 + H + 61 min ({h_end + EXIT_WINDOW_MS}); the contrast is read once, after the last window attempt has exited")
    days = window_days(clock, h_end)
    tr: dict[str, list] = {}
    integ: dict[str, Any] = {}
    for a in ARMS:
        if a not in rows_by_arm:
            raise Refused(f"no ledger rows given for arm {a}")
        if entry_ms_by_arm is None or a not in entry_ms_by_arm:
            raise Refused(f"no decision-log entry instants given for arm {a}: the decision log / ledger reconciliation needs them")
        integ[a] = arm_integrity(rows_by_arm[a], (incidents_by_arm or {}).get(a, ()), (problems_by_arm or {}).get(a, ()), clock, h_end, now_ms, stake,
                                _need_orders((entry_orders_by_arm or {}).get(a), entry_ms_by_arm[a], f"arm {a}"))
        if integ[a]["pending"]:
            raise Refused(f"arm {a}: {integ[a]['pending']} attempt(s) in the window are PENDING (younger than the 24 h settle window): not read")
        tr[a], _ = priced_trades(rows_by_arm[a], clock, h_end, stake)
    res: dict[str, Any] = {"read": "contrast", "now_ms": now_ms, "clock_start_ms": clock, "D": len(days), "days": list(days), "horizon_end_ms": h_end,
                           "deciding_stake": stake, "e1": dict(e1), "fallback_check": dict(fallback), "integrity": integ, "sign_flip_min_p_at_D": sign_flip_min_p(len(days)),
                           "mde": mde_line(len(days)), "no_book_has_cleared_the_promotion_gate": True, "a_skill_pass_is_not_a_promotion": True,
                           "coverage": dict(coverage) if coverage else None}
    nd = []
    if e1["a_l_lt_20"]:
        nd.append("E1_entry_rate (A_L < 20 in [T0, T0 + 2 d))")
    nd += [f"arm {a}: {why}" for a in ARMS for why in integ[a]["reasons"]]
    if fallback.get("excluded_cap_not_decidable"):
        nd.append("excluded periods above the 10% cap (NOT_DECIDABLE)")
    if nd:
        res.update(status="NOT_DECIDABLE", reasons=nd)
        res["multiplicity"] = holm_block(n_books, None if other_ps is None else dict(other_ps))
        return res
    ro = []
    for a in ARMS:  # the scorer's REPORT_ONLY rules, applied per arm (arm_integrity cannot call the scorer past a bot's natural instant)
        if any(rule not in (r["calc"][LAT]["by_rule"] or {}) or rule not in r["legs"] for r in tr[a] for rule in RULES):
            ro.append(f"arm {a}: the deciding cell was not priced under both strand rules")
        if any(r.get("prog") == "pump_bonding" for r in rows_by_arm[a] if r.get("kind") in ("trade", "excluded")):
            ro.append(f"arm {a}: a bonding row is in the book: its costs are unmeasured")
    if ro:
        res.update(status="REPORT_ONLY", reasons=ro, multiplicity=holm_block(n_books, None if other_ps is None else dict(other_ps)))
        return res
    for a in ARMS:  # the stored flat leg must equal the recomputed one (arena_score.book_cells)
        flat_x, _ = vectors(tr[a])
        for rule in RULES:
            for r, v in zip(tr[a], flat_x[f"flat_{LAT}_{rule}"]):
                if r["legs"][rule][f"flat_{LAT}"]["net_lamports"] != float(v):
                    raise Refused(f"trade {r['trade_id']}: stored flat leg differs from the recomputed flat leg ({rule}, {LAT})")
    if not tr["U"]:
        raise Refused("no U priced trades at the deciding stake: the shared pressure intercept cannot be fit")
    _, u_int = vectors(tr["U"])
    vec = {a: vectors(tr[a], intercepts=u_int)[0] for a in ARMS}  # A1: one intercept per rule, U's refit
    per = {a: {k: period_values(tr[a], vec[a][k]) for k in CELLS} for a in ARMS}
    res["press_intercepts_for_contrast"] = u_int
    l_ne = bool(fallback.get("L_not_evaluable")) or bool(e1["fallback_gt_10pct"])
    why_ne = "E1_fallback" if e1["fallback_gt_10pct"] else fallback.get("reason")
    pairs = {"L-U": ("L", "U"), "L-R": ("L", "R"), "L-S": ("L", "S"), "S-U": ("S", "U"), "R-U": ("R", "U")}
    out: dict[str, Any] = {}
    for name, (a, b) in pairs.items():
        if l_ne and "L" in (a, b):
            out[name] = {"status": "NOT_EVALUABLE", "reason": why_ne}
        else:
            out[name] = contrast_cell(per[a], per[b], days, two_tailed=(name == "R-U"))
    res["contrasts"] = out
    ic = None
    if not l_ne:
        icd, n_ic = rank_ic_daily(l_rank_rows, tr["U"], vec["U"][CELLS[0]], days)
        ic = {"report_only": True, "periods": n_ic, "mean_daily_ic": float(icd.mean()), "nonzero_days": int((icd != 0).sum()), "p": sign_flip_p(icd),
              "l_entry_rows": fallback.get("attempts_L"), "l_rank_rows": fallback.get("rank_rows"),
              "entry_vs_rank_row_difference": bool(fallback.get("entry_vs_rank_row_difference")),
              "note": "U-priced flat_1p9_sc returns, periods keyed by U's entry stamp; IC > 0 means L's order follows U-priced returns"}
        if ic["nonzero_days"] < CONTRAST_MIN_NONZERO_DAYS:
            ic["status"] = "NOT_DECIDABLE"
            ic["reason"] = "contrast_days"
    res["rank_ic"] = ic
    fam = {k: float(v["p"]) for k, v in out.items() if k != "L-U" and v.get("p") is not None and v.get("status") != "NOT_EVALUABLE"}
    if ic and ic["p"] is not None and ic["periods"] > 0:
        fam["rank_ic"] = float(ic["p"])
    flags = {k: "NOT_DECIDABLE contrast_days" for k in fam if (out.get(k) or {}).get("status") == "NOT_DECIDABLE"}
    if ic and ic.get("status") == "NOT_DECIDABLE" and "rank_ic" in fam:
        flags["rank_ic"] = "NOT_DECIDABLE contrast_days"
    res["holm"] = holm_block(n_books, fam, flags=flags)
    res["holm"]["family"] = "secondaries only (L-R, L-S, S-U, R-U, rank IC); L-U is the primary at alpha 0.025"
    res["multiplicity"] = holm_block(n_books, None if other_ps is None else dict(other_ps))
    primary = out["L-U"]
    res["status"] = primary["status"]
    if primary.get("reason"):
        res["reason"] = primary["reason"]
    d = len(days)
    if primary["status"] == "NOT_PASS":
        res["verdict_text"] = ("no skill detected; MDE IC about 0.17" if d == MDE["D"]
                               else f"no skill detected; the MDE at D = {d} is not tabulated and is {_mde_vs_tab(d)}")
    elif primary["status"] == "PASS":
        res["verdict_text"] = ("L - U passes: Haiku selects better than random from this screen (" + SCREEN_PASS + "). This is not a promotion and licenses no canary.")
    ru = out.get("R-U", {})
    if ru.get("subcells") and ru.get("status") != "NOT_DECIDABLE":
        tails = {k: min(1.0 if s["p"] is None else s["p"], 1.0 if s["p_other_tail"] is None else s["p_other_tail"]) for k, s in ru["subcells"].items()}
        res["R_minus_U_fault_flag"] = any(t <= CONTRAST_ALPHA for t in tails.values())
        res["R_minus_U_fault_tails_min_p"] = tails
    elif ru.get("subcells"):
        res["R_minus_U_fault_flag"] = None
        res["R_minus_U_fault_note"] = "NOT_DECIDABLE contrast_days: the fault check cannot be read"
    return res


# ------------------------------------------------------------------------------------------------------------------------------ record once
def record_once(record_dir: str | Path, kind: str, res: Mapping[str, Any]) -> Path:
    """kind includes the bot id, so a record is unique per (bot, read kind)."""
    d = Path(record_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"hv7_read_{kind}.json"
    try:
        with open(p, "x") as f:
            f.write(json.dumps(res, sort_keys=True, indent=1, default=str))
    except FileExistsError:
        raise Refused(f"{p} exists: the read is taken once") from None
    return p


def resolve_ledger_root(root: str | Path, ledger_root: str | None) -> str:
    """The HV7 engine ledger is <HV7 root>/engine-ledger. The shared arena ledger and any path outside the HV7 root (symlinks resolved) are refused."""
    base = Path(os.path.realpath(root))
    p = Path(os.path.realpath(ledger_root)) if ledger_root else base / "engine-ledger"
    if p == Path(os.path.realpath(LEDGER_ROOT)) or Path(os.path.realpath(LEDGER_ROOT)) in p.parents:
        raise Refused("the shared arena ledger is refused: the HV7 ledger is <HV7 root>/engine-ledger")
    if base not in p.parents:
        raise Refused("the ledger root must be inside the HV7 root")
    return str(p)


def _load_arm(ledger_root: str, registry: str, arm: str) -> tuple[list[dict], list[dict], list[str]]:
    bot = R.bot_id(arm)
    led = Ledger(ledger_root)
    rows, problems = led.scan_bot(bot)
    incidents, inc_problems = led.read_incidents(bot)
    AS.check_bot_rows(bot, load_registry(registry), rows)
    return rows, incidents, list(problems) + list(inc_problems)


def _beside(bot: str, test_mode: bool = False) -> Any:
    """Any arena_score record for this bot id, printed beside the hv7 record: hv7_read's record is the gate read; arena_score output for hv7.* is non-binding."""
    if test_mode:  # never read the production reads dir from a test run
        return None
    try:
        return AS.load_recorded_read(AS.READS_DIR, bot)
    except Exception:  # noqa: BLE001
        return None


def _load_other_ps(path: str | None) -> dict[str, float] | None:
    if not path:
        return None
    try:
        d = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raise Refused(f"--other-ps {path} is unreadable") from None
    if not (isinstance(d, dict) and all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1 for v in d.values())):
        raise Refused("--other-ps: expected an object {name: p in [0, 1]} from notebook copies")
    return {str(k): float(v) for k, v in d.items()}


def main(argv: Sequence[str] | None = None, *, out=None, _wall_ms: int | None = None) -> int:
    out = out if out is not None else sys.stdout
    ap = argparse.ArgumentParser(prog="hv7_read")
    ap.add_argument("kind", choices=("gate", "contrast"))
    ap.add_argument("--arm", choices=PROMOTABLE, default="L")
    ap.add_argument("--root", default=R.HV7_ROOT, help="HV7 decision-log root (<root>/decisions/<arm>/)")
    ap.add_argument("--ledger-root", help="default <root>/engine-ledger; /data/mal/arena/ledger (shared, holds sealed HV4 output) and any path outside the HV7 root are refused")
    ap.add_argument("--registry", default=REGISTRY_PATH, help="timing (clock, window end, deciding stake) is read from here, never from a flag")
    ap.add_argument("--n-books", type=int, required=True, help="N: every book with a clock before the read, from notebook copies")
    ap.add_argument("--other-ps", help="JSON {name: report-only p} of the other arena reads, from notebook copies (Holm rank)")
    ap.add_argument("--now-ms", type=int, help="TEST ONLY (MAL_ARENA_TEST=1); never later than the wall clock")
    ap.add_argument("--record-dir", help="TEST ONLY (MAL_ARENA_TEST=1); the production dir is fixed")
    a = ap.parse_args(argv)
    test_mode = os.environ.get(TEST_ENV) == "1"
    try:
        wall = int(time.time() * 1000) if _wall_ms is None else int(_wall_ms)
        if (a.now_ms is not None or a.record_dir) and not test_mode:
            raise Refused("--now-ms and --record-dir are test-only (MAL_ARENA_TEST=1); the read uses the wall clock and the fixed record dir")
        now = wall if a.now_ms is None else a.now_ms
        if now > wall:
            raise Refused(f"--now-ms {now} is later than the wall clock {wall}")
        regn = load_registration(a.registry)
        a.ledger_root = resolve_ledger_root(a.root, a.ledger_root)
        other = _load_other_ps(a.other_ps)
        rec_dir = Path(a.record_dir) if a.record_dir else Path(AS.READS_DIR) / "hv7"
        clock, halt = regn["clock_start_ms"], regn["halt_ms"]
        e1 = e1_check(a.root, clock)
        if a.kind == "gate":
            bot = R.bot_id(a.arm)
            tag = f"{bot}_gate"
            if (rec_dir / f"hv7_read_{tag}.json").exists():
                raise Refused("the gate read was already taken")
            ems = entry_ms(a.root, a.arm)
            orders = entry_orders(a.root, a.arm)
            b = AS.blind_read_instant(ems, clock)
            fb = fallback_check(a.root, clock, b) if b is not None and now >= b + EXIT_WINDOW_MS else None
            rows, incs, probs = _load_arm(a.ledger_root, a.registry, a.arm) if fb is not None else ([], [], [])
            cov = coverage_check(a.root, clock, b) if b is not None else None
            res = gate_read(a.arm, now_ms=now, regn=regn, rows=rows, entry_decisions_ms=ems, entry_orders=orders, e1=e1, fallback=fb, incidents=incs, ledger_problems=probs,
                            n_books=a.n_books, other_ps=other, coverage=cov)
            res["arena_score_record_beside"] = _beside(bot, test_mode)
            record_once(rec_dir, tag, res)
        else:
            tag = f"{R.bot_id('L')}_contrast"
            if (rec_dir / f"hv7_read_{tag}.json").exists():
                raise Refused("the contrast was already read")
            if now < halt + EXIT_WINDOW_MS:
                raise Refused(f"now {now} is before T0 + H + 61 min ({halt + EXIT_WINDOW_MS})")
            fb = fallback_check(a.root, clock, halt)
            loaded = {x: _load_arm(a.ledger_root, a.registry, x) for x in ARMS}
            l_rank = [r for r, bad in C._rows(Path(a.root), "L") if not bad and r.get("kind") == "rank" and clock <= r.get("ms", -1) < halt]
            res = contrast_read(now_ms=now, regn=regn, rows_by_arm={x: v[0] for x, v in loaded.items()}, l_rank_rows=l_rank, fallback=fb, e1=e1, n_books=a.n_books,
                                incidents_by_arm={x: v[1] for x, v in loaded.items()}, problems_by_arm={x: v[2] for x, v in loaded.items()}, other_ps=other,
                                coverage=coverage_check(a.root, clock, halt), entry_ms_by_arm={x: entry_ms(a.root, x) for x in ARMS},
                                entry_orders_by_arm={x: entry_orders(a.root, x) for x in ARMS})
            record_once(rec_dir, tag, res)
    except (Refused, AS.Refused) as e:  # these carry no outcome data
        print(f"refused: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 - class name only
        print(f"refused {type(e).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(res, sort_keys=True, default=str), file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
