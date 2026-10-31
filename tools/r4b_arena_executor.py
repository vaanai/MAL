"""r4b_arena_executor: the DRY executor of the r4-b book (EXP-027, bot_id `r4b-exp027`). PAPER / DRY ONLY: LIVE_BUILT is False, no key, no wallet, no send.

r4-b is an hourly cross-section (EXP-027 section 2.1): at each UTC hour mark the frozen K = 10 engine (tools/r4b_live.py) sells held tokens that left the top 10
and buys the new ones, 0.25 SOL per position, no stop loss, exits only at hour marks (reason rank | gap | end), plus the host's `restart` exit. This module is a thin, pinned subclass of the
bridge's `Bridge` (the pattern of tools/floor_arena_executor.py). It edits no pinned file (arena_live_bridge.py, floor_arena_executor.py, probe_live.py, any
r4b module). The sell is the bridge's sell-all + close-ATA path (probe_live.LiveExecutor = EXP027_SELL_BUILDER, untouched).

The decisions are the frozen engine's, read from the hash-chained decision log (`entry` and `exit` rows). Nothing is re-decided here. Which decisions go live
is set only by the C0 caps in rank order within a mark; every skipped decision is logged with its reason (QP-RULING-1030 section 1, conditions 1 and 3).
The canary stake S is an execution limit, not the book's operating point; its results are an unpromoted execution measurement, never evidence for r4-b.

WHAT THIS CHANGES ON TOP OF THE BRIDGE (each item is a difference between r4-b and the H5 / probe-profile defaults the bridge was written for)
  1 BOOK PIN       bot_id, frozen_sha (config; the start row must repeat it, else the bridge latches frozen_sha_mismatch), the 49 h max hold and the C0 table
                   are code constants. Config can only lower the caps. The bridge's TIERS["T0"] (0.02 SOL, 10 a day) is not used.
  2 NO STOP LOSS   X6 / N6. The book has none. The bridge's sl_backstop (clamped to (0, 0.60]) would sell positions the paper book keeps, so `backstops` is
                   replaced: the ONLY backstop is a time stop at 48 h + 1 h + 60 s after the ENTRY DECISION (not the landing). A config sl_backstop is refused.
  3 RE-ENTRY       X7. The bridge never re-buys a mint. Here a mint is released as soon as nothing is held for it: its sell landed (the rule), or its buy failed,
                   expired or was refused (nothing was bought). The bridge's order_id idempotency (`<mint>:<T0>`) is untouched.
  4 CAPS IN RANK ORDER  X8. Entry rows of one mark are read in rank order (the engine writes them so). A row whose rank does not rise within its mark is
                   refused `rank_order`. Every skip is `entry_refused` in the bridge log with its reason. An exit row for an order the canary does not hold
                   is ignored and counted (`exits_not_held`), never an error. A reason outside rank | gap | end | restart is not followed (counted).
  5 LATENCY ORIGIN X9 / N5 (DEC-032 Amendment 8 A8 = QP-RULING-1030 Erratum 1). Every order records decision_slot = the paper twin's sd(T). The DECIDING
                   quantity is d_land = landed slot - decision_slot, in SLOTS: "at most 1.9 s" means d_land <= round(1.9 / slot_s) and "above 3.0 s" means
                   d_land > round(3.0 / slot_s), with slot_s the per-UTC-date value the twin uses (config slot_s_by_date; config slot_s is the provisional
                   value when a date is missing) and Python round = np.round (half to even, EXP-027 :258-261). Seconds and wall times (sd_seen_ms when a host
                   writes it) are diagnostics only: they never decide, and a missing wall field is not a P7 condition. NEVER from the declog row or the send.
                   `latency_summary` / `latency_check` are pure; DEC-032 P5 (rolling 20) latches `p5_landing_latency` (new buys stop, exits run).
  6 FEES           505,000 lamports per send (500,000 priority + 5,000 base, the bridge's LiveExecutor profile); reserves use the same fee. Declared, not assumed.
  7 GATES          committed-loss stop (DEC-032 section 3), wallet floor, row pins (paper stake 0.25, order id, t_mark, rank, V0 band, age window, decision_slot),
                   10-minute buy hold after a decision-host restart, canary duration (14 days from the first buy attempt), max concurrent positions 2.
  8 TWIN           N7. `twin_round_trip` prices the r4-b paper twin with tools/r4b_book.round_trip at stake S and the executor's per-send fee (nofail, 1.9 s leg,
                   E1 excluded). Pure; no data loading.
  9 DRY REPLAY     `replay_equivalence` feeds a closed decision log through the bridge with the capacity gates off (orders equal the log's decisions one for one)
                   or on (a rank-ordered capped subset, every skip logged).

  10 RESTART EXIT  A decision-host restart empties the engine's book, so the previous process's positions would get no exit row from it. The host writes one
                   `exit` row, reason `restart` (tools/r4b_live.restart_exits, decision_slot None), per entry with no exit row, right after its second `start` row.
                   It is followed like rank | gap | end: the held position is sold. The trip is marked `gap` (excluded from parity, counted as `exit_restart`).
                   Its latency origin is unknown (no decision slot, and the mark's sd(T) is not borrowed), so it is not a P5 or match-clause sample. The 49 h time
                   stop stays the backstop for a restart row that never comes.
  11 FEED-GAP HOLD A `gap` row in the decision log (tools/r4b_live.log_gap: a feed gap that halts buys, data_loss not False) stops new buys at once and
                   restarts them only after 10 gap-free minutes counted from the END of the gap (max of its instant and to_ms) and, LIVE only, after the manager's
                   review file names the newest gap instant (the bridge's `_gap_cleared`, c0.gap_clear_problem, untouched). An entry decided inside the held period
                   is refused `feed_gap_hold` and logged. Exits keep running. A trip open across the gap is marked `gap`. A gap without data loss is never logged,
                   so it holds nothing. This is the hv4_c0 gap rule's shape for the r4-b profile; the hv4 path in arena_live_bridge.py is not changed.

Parity, the twin writer and the tie-out are NOT wired here: this build records what they need (decision_slot, landed slots and times, `gap` flags).
Nothing here says r4-b makes money. r4-b has zero out-of-sample trades and has not cleared the promotion gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tools import arena_live_bridge as B
from tools import r4b_book as BK
from tools import r4b_frozen as FZ
from tools.arena_declog import DecisionLog
from tools.arena_paper import canon

LIVE_BUILT = False  # this PR builds no live entry point; a live config is refused at start
BOT_ID = "r4b-exp027"  # = tools.r4b_live.BOT_ID (a test asserts it)
BOOK = "r4-b"
PAPER_STAKE_SOL = FZ.STAKE_SOL  # 0.25: the decision rows' stake; the canary stake is lower and the twin is repriced at it
K = FZ.K
MARK_S = FZ.REBALANCE_S
V_LO = FZ.V_LO  # 17.5e9
V_HI = 17_700_000_000  # = tools.r4b_layout.V_HI (a test asserts it)
GRID_LO_S, GRID_HI_S = 600, 48 * 3600  # a member decides from mbt + 600 s to mbt + 48 h (tools/r4b_live.py)
REASON_RESTART = "restart"  # tools.r4b_live.RESTART_REASON (a test asserts it)
FOLLOWED_EXIT_REASONS = ("rank", "gap", "end", REASON_RESTART)

# --- X6: no stop loss; the only backstop is a time stop after the entry decision
MAX_HOLD_MS = (48 + 1) * 3_600_000  # 48 h of eligibility + the 1 h to the next mark
TIME_STOP_MS = MAX_HOLD_MS + B.BACKSTOP_EXTRA_MS  # + 60 s: 48 h + 1 h + 60 s
REASON_TIME_STOP = "time_stop_backstop"

# --- fees: the bridge's LiveExecutor profile (arena_live_bridge.py, the non-hv4 pcfg priority_lamports 500_000) + the 5,000 base fee. DEC-032 section 4: a FILL, never assumed.
PRIORITY_LAMPORTS = 500_000
BASE_FEE_LAMPORTS = 5_000
SEND_FEE_LAMPORTS = PRIORITY_LAMPORTS + BASE_FEE_LAMPORTS  # 505,000 = tools.r4b_frozen.SEND_FEE_DECIDING
SELL_RESERVE_SENDS = 10  # the worst case of one exit: the sell ladder's sends, each at the top priority (the FLOOR / H5 convention)
SELL_RESERVE_LAMPORTS = SELL_RESERVE_SENDS * SEND_FEE_LAMPORTS
RENT_RESERVE_LAMPORTS = B.RENT_RESERVE_LAMPORTS  # 2,100,000, paid on the buy and refunded by the closing sell

# --- the DEC-032 section 3 C0 table, pinned in code; config may only lower it
C0 = {"stake_lamports": 50_000_000, "max_open": 2, "max_trades_per_day": 30, "daily_loss_lamports": 100_000_000, "total_loss_lamports": 200_000_000}
FUNDED_MIN_LAMPORTS, FUNDED_MAX_LAMPORTS = 350_000_000, 1_000_000_000
FLOOR_BELOW_FUNDED_LAMPORTS = 250_000_000
MIN_WALLET_FLOOR_LAMPORTS = 50_000_000
CANARY_MAX_MS = 14 * 86_400_000
GAP_HOLD_MS = 600_000  # DEC-032 section 5 item 2: buys restart only after 10 gap-free minutes (a decision-host restart is a gap)
FEED_GAP_HOLD = "feed_gap_hold"  # the refusal of an entry decided inside the hold that a feed `gap` row starts (counted from the END of the gap)
SLOT_S_MIN, SLOT_S_MAX = 0.1, 1.0

# --- X9: latency origin and the DEC-032 section 5 thresholds
P5_WINDOW = 20
P5_SOFT_HALT_S = 3.0  # landing p50 above this over the first 20 landed buys, then rolling 20: soft halt
MATCH_P50_S = 1.9  # "live fills match paper" needs landing p50 at most this
TWIN_LEG_S = 1.9  # the deciding leg
P5_LATCH = "p5_landing_latency"

ES_SCHEMA = "r4b_executor_state_v1"
SD_MARKS_KEEP = 400
FROZEN_SHA_RE = re.compile(r"[0-9a-f]{40}")
CAP_SKIPS = ("max_open", "max_trades_per_day", "daily_loss_cap", "total_loss_cap", "committed_loss_stop", "wallet_floor")


def wallet_floor_lamports(funded: int | None, configured: int = 0) -> int:
    """DEC-032 section 3: floor = max(0.05 SOL, funded - 0.25 SOL); config may raise it, never lower it."""
    base = MIN_WALLET_FLOOR_LAMPORTS if funded is None else max(MIN_WALLET_FLOOR_LAMPORTS, int(funded) - FLOOR_BELOW_FUNDED_LAMPORTS)
    return max(base, int(configured))


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def _int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


@dataclass(frozen=True)
class R4bConfig(B.Config):
    """The bridge Config plus `slot_s`: seconds per slot of the day, from the decision host's measurement (EXP-027 section 3.3: 3600 / median hourly SPH)."""

    slot_s: float = 0.0


def make_config(d: Mapping[str, Any]) -> R4bConfig:
    """The bridge Config for r4-b. Fail closed: any pin that does not hold raises ValueError. Caps are min(config, C0); the hold is the book's."""
    if LIVE_BUILT is False and d.get("mode", B.DRY) != B.DRY:
        raise ValueError("live_not_built: LIVE_BUILT is False, only mode 'dry' exists")
    if d.get("bot_id", BOT_ID) != BOT_ID:
        raise ValueError(f"bot_id must be {BOT_ID}")
    sha = d.get("frozen_sha")
    if not isinstance(sha, str) or not FROZEN_SHA_RE.fullmatch(sha):
        raise ValueError("frozen_sha must be the 40-hex install sha (the decision host's start row repeats it)")
    if "max_hold_ms" in d and d["max_hold_ms"] != MAX_HOLD_MS:
        raise ValueError("max_hold_ms is the book's constant")
    if "sl_backstop" in d:
        raise ValueError("r4-b has no stop loss: sl_backstop cannot be configured (X6)")
    for k in ("root", "state_dir"):
        if not d.get(k):
            raise ValueError(f"config {k} is required")
    end = d.get("end_ms")
    if not _num(end) or end <= 0:
        raise ValueError("config end_ms is required (explicit, positive)")
    slot_s = d.get("slot_s")
    if not _num(slot_s) or not SLOT_S_MIN <= slot_s <= SLOT_S_MAX:
        raise ValueError(f"config slot_s is required, in [{SLOT_S_MIN}, {SLOT_S_MAX}] seconds (the landed latency is measured in slots)")
    kw: dict[str, Any] = {}
    for k, cap in C0.items():
        v = d.get(k, cap)
        if not _num(v) or v <= 0:
            raise ValueError(f"config {k} must be a positive number")
        kw[k] = min(int(v), cap)
    funded = d.get("funded_lamports")
    if funded is not None and not (_num(funded) and FUNDED_MIN_LAMPORTS <= funded <= FUNDED_MAX_LAMPORTS):
        raise ValueError("funded_lamports must be 0.35 to 1.00 SOL")
    kw["wallet_floor_lamports"] = wallet_floor_lamports(None if funded is None else int(funded), int(d.get("wallet_floor_lamports", 0) or 0))
    # The bridge's per-trade latch (default 15M = 30 % of S) is an anomaly alarm for a book with a stop. r4-b has none, so a 30 % loss on one position is
    # an ordinary tail and the DEC's stops (daily 0.10, total 0.20, committed loss) are the loss guards. Default: the whole stake; config may only tighten.
    ptl = d.get("per_trade_loss_lamports", kw["stake_lamports"])
    if not _num(ptl) or ptl <= 0:
        raise ValueError("config per_trade_loss_lamports must be a positive number")
    kw["per_trade_loss_lamports"] = min(int(ptl), kw["stake_lamports"])
    for k in ("stop_file", "halt_file"):
        if d.get(k):
            kw[k] = str(d[k])
    if "dry_balance_lamports" in d:
        kw["dry_balance_lamports"] = int(d["dry_balance_lamports"])
    return R4bConfig(bot_id=BOT_ID, frozen_sha=sha, root=str(d["root"]), state_dir=str(d["state_dir"]), end_ms=int(end), max_hold_ms=MAX_HOLD_MS, mode=B.DRY,
                     sl_backstop=B.MAX_SL_BACKSTOP, slot_s=float(slot_s), **kw)  # sl_backstop is a field the overridden backstops() never reads


# --- X9: landed latency from sd(T). Pure functions; none of them reads the declog row's time, the send time or the decision time ---------------------


def slot_latency_s(decision_slot: Any, landed_slot: Any, slot_s: float) -> float | None:
    """(landed_slot - decision_slot) x slot_s, decision_slot = sd(T) of the paper twin. None when either slot is missing or the order is negative."""
    if not (_int(decision_slot) and _int(landed_slot)) or not _num(slot_s) or slot_s <= 0:
        return None
    d = landed_slot - decision_slot
    return None if d < 0 else d * float(slot_s)


def wall_latency_s(sd_seen_ms: Any, landed_ms: Any) -> float | None:
    """Wall time from the first-seen time of slot sd(T) to the landing the bridge observed. None when the decision host did not supply sd_seen_ms."""
    if not (_int(sd_seen_ms) and _int(landed_ms)):
        return None
    d = landed_ms - sd_seen_ms
    return None if d < 0 else d / 1000.0


def _med(xs: Sequence[float]) -> float | None:
    return float(statistics.median(xs)) if xs else None


def k_slots(seconds: float, slot_s: float) -> int:
    """round(seconds / slot_s), Python round = np.round (half to even): the twin's k for a latency in seconds (EXP-027 :258-261)."""
    return int(round(seconds / slot_s))


def order_slot_s(o: Mapping[str, Any], slot_s: float, slot_s_by_date: Mapping[str, float] | None = None) -> tuple[float, str]:
    """(slot_s, source) for one order: the twin's per-UTC-date value of the decision's date if given, else the provisional config value."""
    if slot_s_by_date and _int(o.get("decision_ms")):
        d = datetime.fromtimestamp(o["decision_ms"] / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
        v = slot_s_by_date.get(d)
        if _num(v) and v > 0:
            return float(v), "date"
    return float(slot_s), "provisional"


def landed_buy_latencies(orders: Mapping[str, Mapping[str, Any]], slot_s: float, slot_s_by_date: Mapping[str, float] | None = None) -> list[dict[str, Any]]:
    """One record per landed buy, in landing order: order_id, slots = d_land (landed - decision; DECIDING), k19 / k30 (round(1.9 / slot_s), round(3.0 / slot_s)),
    x19 / x30 = d_land - k (the excess in slots), slot_s_latency and wall_s_latency (diagnostics), gap."""
    rows = []
    for oid, o in orders.items():
        if not _int(o.get("buy_landed_ms")):
            continue
        ds, ls = o.get("decision_slot"), o.get("buy_landed_slot")
        ss, src = order_slot_s(o, slot_s, slot_s_by_date)
        d = (ls - ds) if _int(ds) and _int(ls) and ls >= ds else None
        k19, k30 = k_slots(MATCH_P50_S, ss), k_slots(P5_SOFT_HALT_S, ss)
        rows.append({"order_id": oid, "landed_ms": o["buy_landed_ms"], "slots": d, "k19": k19, "k30": k30, "x19": None if d is None else d - k19,
                     "x30": None if d is None else d - k30, "slot_s_used": ss, "slot_s_source": src,
                     "slot_s": slot_latency_s(ds, ls, ss), "wall_s": wall_latency_s(o.get("sd_seen_ms"), o["buy_landed_ms"]), "gap": bool(o.get("gap"))})
    rows.sort(key=lambda r: (r["landed_ms"], r["order_id"]))
    return rows


def latency_summary(orders: Mapping[str, Mapping[str, Any]], slot_s: float, *, window: int = P5_WINDOW, slot_s_by_date: Mapping[str, float] | None = None) -> dict[str, Any]:
    """The landing-latency facts of DEC-032 section 5, counted from sd(T). `window_p50_s` is the rolling P5 figure (last `window` measured landed buys);
    `all_p50_s` is the aggregate the match clause uses (all measured landed buys since the first send); `non_gap_p50_s` leaves out trips marked `gap`.
    The wall figures use sd_seen_ms and are reported beside the slot figures; a missing sd_seen_ms is counted, never replaced by another time."""
    rows = landed_buy_latencies(orders, slot_s, slot_s_by_date)
    meas = [r for r in rows if r["slots"] is not None]
    wall = [r for r in rows if r["wall_s"] is not None]
    sells = []
    for o in orders.values():
        v = slot_latency_s(o.get("exit_decision_slot"), o.get("sell_landed_slot"), slot_s)
        if v is not None:
            sells.append(v)
    last = meas[-window:]
    last_wall = wall[-window:]
    return {"origin": "sd(T): decision_slot, never the declog row or the send time", "slot_s": slot_s, "landed_buys": len(rows), "measured": len(meas),
            "unmeasured": len(rows) - len(meas), "window": window, "window_n": len(last), "window_p50_s": _med([r["slot_s"] for r in last]),
            "all_p50_s": _med([r["slot_s"] for r in meas]), "non_gap_p50_s": _med([r["slot_s"] for r in meas if not r["gap"]]),
            "window_x30_p50_slots": _med([r["x30"] for r in last]), "all_x19_p50_slots": _med([r["x19"] for r in meas]),
            "all_slots_p50": _med([r["slots"] for r in meas]), "provisional_slot_s": sum(1 for r in meas if r["slot_s_source"] == "provisional"),
            "wall_measured": len(wall), "wall_missing": len(rows) - len(wall), "wall_window_p50_s": _med([r["wall_s"] for r in last_wall]),
            "wall_all_p50_s": _med([r["wall_s"] for r in wall]), "sells_measured": len(sells), "sells_p50_s": _med(sells)}


def latency_check(summary: Mapping[str, Any], *, min_n: int = P5_WINDOW) -> dict[str, Any]:
    """DEC-032 P5 and the match clause on a `latency_summary`, decided IN SLOTS (A8 / Erratum 1): p5_soft_halt when the median over the P5 window of
    (d_land - round(3.0 / slot_s)) is above 0; match_latency_ok when the median over all measured landed buys of (d_land - round(1.9 / slot_s)) is at most 0;
    both need at least `min_n` measured landed buys. The seconds and wall figures are diagnostics (diag_*), never deciding."""
    enough = summary["measured"] >= min_n
    x30, x19 = summary["window_x30_p50_slots"], summary["all_x19_p50_slots"]
    ww, wa = summary["wall_window_p50_s"], summary["wall_all_p50_s"]
    return {"enough": enough, "p5_soft_halt": bool(enough and x30 is not None and x30 > 0),
            "match_latency_ok": bool(enough and x19 is not None and x19 <= 0),
            "diag_wall_p5_over": bool(summary["wall_measured"] >= min_n and ww is not None and ww > P5_SOFT_HALT_S),
            "diag_wall_match_over": bool(summary["wall_measured"] >= min_n and wa is not None and wa > MATCH_P50_S)}


# --- N7: the r4-b paper twin at stake S and the executor's per-send fee ---------------------------------------------------------------------------------


def twin_entry_state(S: Mapping[str, Any], i: int) -> tuple[float, float, float]:
    """(Qe, Be, spot) of decision row i at the 1.9 s leg, from the dict tools.r4b_book.mint_states returns. A states dict without the 1.9 s leg is refused."""
    if TWIN_LEG_S not in S["Qe"] or TWIN_LEG_S not in S["Be"]:
        raise ValueError(f"the twin is priced at the {TWIN_LEG_S} s leg; the states dict has {sorted(S['Qe'])}")
    return float(S["Qe"][TWIN_LEG_S][i]), float(S["Be"][TWIN_LEG_S][i]), float(S["spot"][i])


def twin_round_trip(Qe: float, Be: float, spot: float, Qx: float, Bx: float, *, stake_lamports: int, send_fee_lamports: int = SEND_FEE_LAMPORTS) -> dict[str, Any]:
    """The r4-b paper twin of one decision row: tools/r4b_book.round_trip (the registered pricing engine, DEC-021 haircuts, guard 1.15, strand rules) at the
    canary stake S and the executor's per-send fee. NOFAIL leg (no flat 15 % or pressure blend), E1 EXCLUDED (round_trip has no E1 floor), rent 0 (the sell
    closes the ATA: r4b_frozen.RENT_DECIDING). (Qe, Be, spot) come from the 1.9 s leg (`twin_entry_state`); (Qx, Bx) are the exit state at the exit mark.
    Pure: no data is loaded. pnl in lamports; bps is of the stake."""
    if not _int(stake_lamports) or stake_lamports <= 0 or not _int(send_fee_lamports) or send_fee_lamports < 0:
        raise ValueError("stake_lamports must be a positive int and send_fee_lamports a non-negative int")
    pnl, filled, stranded = BK.round_trip(float(stake_lamports), float(Qe), float(Be), float(spot), float(Qx), float(Bx), send=float(send_fee_lamports),
                                          rent=FZ.RENT_DECIDING, return_stranded=True)
    pnl_f = float(pnl)
    return {"pnl_lamports": pnl_f, "bps_of_stake": pnl_f / stake_lamports * 1e4, "filled": bool(filled), "stranded": bool(stranded), "stake_lamports": stake_lamports,
            "send_fee_lamports": send_fee_lamports, "leg_s": TWIN_LEG_S, "fail_model": "nofail", "e1": "excluded"}


# --- the bridge ---------------------------------------------------------------------------------------------------------------------------------------


class R4bBridge(B.Bridge):
    """Bridge pinned to r4-b. `dry_replay_caps=True` (dry only) switches the capacity gates off so a replay of a whole decision log shows every decision going through."""

    def __init__(self, cfg: B.Config, port: B.OrderPort, *, now_ms: Callable[[], int] | None = None, dry_replay_caps: bool = False,
                 live_ok_path: Path = B.LIVE_OK_PATH, tier_path: Path = B.TIER_PATH, root_checks: bool = True,
                 close_blocked: Callable[[str], bool | None] | None = None, slot_s_by_date: Mapping[str, float] | None = None,
                 gap_clear_dir: Path | str | None = None, require_gap_review: bool = False) -> None:
        self.slot_s_by_date = dict(slot_s_by_date or {})  # the twin's per-UTC-date slot_s (A8); config slot_s is the provisional value
        if any(not _num(v) or not SLOT_S_MIN <= v <= SLOT_S_MAX for v in self.slot_s_by_date.values()):
            raise SystemExit("r4b executor start refused: slot_s_by_date out of range")
        if (not isinstance(cfg, R4bConfig) or cfg.bot_id != BOT_ID or cfg.max_hold_ms != MAX_HOLD_MS or not FROZEN_SHA_RE.fullmatch(str(cfg.frozen_sha))
                or not _num(cfg.slot_s) or not SLOT_S_MIN <= cfg.slot_s <= SLOT_S_MAX):
            raise SystemExit("r4b executor start refused: book pin")
        if cfg.mode != B.DRY or port.live or LIVE_BUILT:
            raise SystemExit("r4b executor start refused: live_not_built")
        self.dry_replay_caps = bool(dry_replay_caps)
        self.require_gap_review = bool(require_gap_review) or cfg.mode == B.LIVE  # live always needs the manager's review; a dry bridge may ask for it too (tests), never be excused
        sd = Path(cfg.state_dir)
        self.es_path = sd / f"r4b-state-{cfg.mode}.json"
        self.es = self._load_es()
        super().__init__(cfg, port, now_ms=now_ms, live_ok_path=live_ok_path, tier_path=tier_path, root_checks=root_checks, close_blocked=close_blocked,
                         gap_clear_dir=gap_clear_dir)

    # -- sidecar state (the bridge's BridgeState has a fixed field set; r4-b's own facts live beside it, atomic like it)
    def _load_es(self) -> dict[str, Any]:
        if self.es_path.exists():
            raw = json.loads(self.es_path.read_text())
            if raw.get("schema") != ES_SCHEMA:
                raise SystemExit("r4b executor start refused: executor state schema")
            for k, v in (("feed_gap_end_ms", 0), ("feed_gap_newest_ms", 0), ("feed_gaps", [])):  # keys added with the feed-gap hold: a state file from before has none
                raw.setdefault(k, v)
            return raw
        return {"schema": ES_SCHEMA, "first_send_ms": None, "gap_hold_until_ms": 0, "starts_seen": 0, "gaps": [], "exits_not_followed": {}, "exits_not_held": {},
                "sd_by_mark": {}, "last_entry": [None, None], "time_stops": 0, "feed_gap_end_ms": 0, "feed_gap_newest_ms": 0, "feed_gaps": []}

    def _save(self) -> None:
        super()._save()
        B._atomic_write(self.es_path, self.es)

    def _bump(self, table: str, key: str) -> None:
        t = self.es[table]
        t[key] = t.get(key, 0) + 1

    # -- limits: the C0 table, never the bridge's older T0
    def tier_limits(self) -> tuple[str, dict[str, int], str | None]:
        if self.dry_replay_caps:
            big = 10 ** 12
            return "T0", {"stake_lamports": min(self.cfg.stake_lamports, C0["stake_lamports"]), "max_open": big, "max_trades_per_day": big, "daily_loss_lamports": big,
                          "total_loss_lamports": big}, "dry_replay_caps"
        lim = {k: min(getattr(self.cfg, k), v) for k, v in C0.items()}
        return "T0", lim, None

    # -- money: committed loss and the wallet floor, at the executor's actual fee
    def _position_worst_case(self, stake: int) -> int:
        return stake + SEND_FEE_LAMPORTS + RENT_RESERVE_LAMPORTS + SELL_RESERVE_LAMPORTS

    def committed_loss_lamports(self, scope: bool = True) -> int:
        """realized loss since the first send (floored at 0) + the worst case of every open or in-flight position (DEC-032 section 3)."""
        total = max(0, -int(self.state.realized_lamports))
        return total + sum(self._position_worst_case(int(o["stake_lamports"])) for o in self.open_orders(scope).values())

    def floor_problem(self, stake: int, bal: int | None, scope: bool = True) -> str | None:
        if self.dry_replay_caps:
            return None
        if bal is None:
            return "balance_unreadable"
        opens = self.open_orders(scope)
        if self.committed_loss_lamports(scope) + self._position_worst_case(stake) > self.cfg.total_loss_lamports:
            return "committed_loss_stop"
        held = sum(int(o["stake_lamports"]) for o in opens.values())
        need = held + stake + SEND_FEE_LAMPORTS + RENT_RESERVE_LAMPORTS + (len(opens) + 1) * SELL_RESERVE_LAMPORTS + self.cfg.wallet_floor_lamports
        return "wallet_floor" if bal < need else None

    # -- the decision row's own pins (restated here: the canary executes only the frozen engine's rows)
    @staticmethod
    def row_problem(row: Mapping[str, Any]) -> str | None:
        meta = row.get("meta") or {}
        mint, tm = meta.get("mint"), row.get("t_mark")
        if not isinstance(mint, str) or not mint or not isinstance(row.get("pool"), str) or not row.get("pool"):
            return "no_pool_or_mint"
        if not _int(tm) or tm % MARK_S:
            return "t_mark_pin"
        if row.get("order_id") != f"{mint}:{tm}":
            return "order_id_pin"
        if row.get("stake_sol") != PAPER_STAKE_SOL:
            return "paper_stake_pin"
        rk = row.get("rank")
        if not _int(rk) or not 0 <= rk < K:
            return "rank_pin"
        v0, mbt = meta.get("v0"), meta.get("mbt")
        if not _num(v0) or not V_LO <= v0 <= V_HI:
            return "v_band"
        if not _int(mbt) or not mbt + GRID_LO_S <= tm <= mbt + GRID_HI_S:
            return "age_pin"
        if not _int(row.get("decision_slot")):
            return "decision_slot_missing"  # without sd(T) the landed latency cannot be counted (X9)
        return None

    # -- entry
    def entry_refusal(self, row: Mapping[str, Any], now: int, lim: dict[str, int], scope: bool = True) -> str | None:
        tm, rk = row.get("t_mark"), row.get("rank")
        out_of_order = False
        if _int(tm) and _int(rk):
            last_tm, last_rk = self.es["last_entry"]
            out_of_order = last_tm == tm and last_rk is not None and rk <= last_rk
            if not out_of_order:
                self.es["last_entry"] = [tm, rk]
        why = super().entry_refusal(row, now, lim, scope)
        if why:
            return why
        why = self.row_problem(row)
        if why:
            return why
        if out_of_order:
            return "rank_order"  # X8: entries of one mark come in rank order; a row that does not rise within its mark is not taken
        if self.dry_replay_caps:
            return None
        if now < int(self.es["gap_hold_until_ms"]):
            return "gap_hold"
        why = self.feed_gap_refusal(row, now)
        if why:
            return why
        first = self.es["first_send_ms"]
        if first is not None and now > int(first) + CANARY_MAX_MS:
            return "canary_expired"
        return None

    def handle_entry(self, row: dict[str, Any]) -> None:
        before = self.state.attempts
        known = str(row.get("order_id")) in self.state.orders
        super().handle_entry(row)
        if self.state.attempts > before and self.es["first_send_ms"] is None:
            self.es["first_send_ms"] = self.now_ms()  # the 14 days run from the first buy attempt
        o = self.state.orders.get(str(row.get("order_id")))
        if o is not None and not known:  # X9: the order carries the paper twin's decision slot, the mark and the optional first-seen time of that slot
            o.update(t_mark=row.get("t_mark"), rank=row.get("rank"), sd_seen_ms=row.get("sd_seen_ms") if _int(row.get("sd_seen_ms")) else None)
            self._log("r4b_order", order_id=o["order_id"], t_mark=o["t_mark"], rank=o["rank"], decision_slot=o.get("decision_slot"), sd_seen_ms=o["sd_seen_ms"],
                      status=o["status"])
        self._release_free()
        self._save()

    # -- X7: a mint is free to be bought again as soon as nothing is held for it
    def _release_free(self) -> None:
        held = {o["mint"] for o in self.state.orders.values() if o["status"] not in ("closed", "refused")}
        free = [m for m in self.state.bought if m not in held]
        if free:
            self.state.bought = [m for m in self.state.bought if m in held]

    # -- decision rows
    def _gap(self, ms: int, why: str) -> None:
        self.es["gap_hold_until_ms"] = max(int(self.es["gap_hold_until_ms"]), int(ms) + GAP_HOLD_MS)
        self.es["gaps"] = (self.es["gaps"] + [{"ms": int(ms), "why": why}])[-200:]
        marked = self._mark_open_gap()  # a restarted engine starts flat: these positions get no exit row from it unless the host writes the restart exits; excluded from parity
        self._log("gap", why=why, decision_ms=int(ms), hold_until_ms=self.es["gap_hold_until_ms"], open_marked=marked)

    def _mark_open_gap(self) -> int:
        marked = 0
        for o in self.state.orders.values():
            if o["status"] not in ("closed", "refused"):
                o["gap"] = True
                marked += 1
        return marked

    def _feed_gap(self, row: Mapping[str, Any]) -> None:
        """A `gap` row of the decision log (tools.r4b_live.log_gap): the feed lost data. Buys stop at once; the hold ends 10 minutes after the END of the gap
        (the larger of the row's instant and its to_ms); trips open now are marked `gap`. A row without an int instant is held from now (fail closed)."""
        raw_ms, to_ms, frm = row.get("ms"), row.get("to_ms"), row.get("from_ms")
        if not _int(raw_ms):
            self._count("feed_gap_row_malformed")
        ms = int(raw_ms) if _int(raw_ms) else self.now_ms()
        end = max(ms, int(to_ms)) if _int(to_ms) else ms
        kind = row.get("gap_kind") if isinstance(row.get("gap_kind"), str) else "unknown"
        self.es["feed_gap_end_ms"] = max(int(self.es["feed_gap_end_ms"]), end)
        self.es["feed_gap_newest_ms"] = max(int(self.es["feed_gap_newest_ms"]), ms)
        self.es["feed_gaps"] = (self.es["feed_gaps"] + [{"ms": ms, "end_ms": end, "gap_kind": kind}])[-200:]
        marked = self._mark_open_gap()
        self._count("feed_gap")
        self._log("feed_gap", gap_kind=kind, decision_ms=ms, from_ms=int(frm) if _int(frm) else None, to_ms=int(to_ms) if _int(to_ms) else None, end_ms=end,
                  hold_until_ms=end + GAP_HOLD_MS, review_required=self.require_gap_review, open_marked=marked, seq=row.get("seq"))
        self._save()

    def feed_gap_refusal(self, row: Mapping[str, Any], now: int) -> str | None:
        """`feed_gap_hold` for an entry decided before the hold ends (the hold is counted from the END of the newest gap) or read before it ends, and, when a review is
        required (live; a dry bridge built with require_gap_review), until the manager's review file names exactly the newest gap instant (the bridge's
        `_gap_cleared`: 10 gap-free minutes from the end AND the root-owned file). No gap row seen: no refusal."""
        end = int(self.es["feed_gap_end_ms"])
        if not end:
            return None
        hold_end = end + GAP_HOLD_MS
        ms = row.get("ms")
        if now < hold_end or not _int(ms) or ms < hold_end:
            return FEED_GAP_HOLD
        if self.require_gap_review and not self._gap_cleared(int(self.es["feed_gap_newest_ms"]), now, end):
            return FEED_GAP_HOLD + ":review"
        return None

    def read_new_rows(self) -> list[dict[str, Any]]:
        """The bridge's read, plus: each entry row's decision_slot is remembered by its t_mark, so an exit row of the same mark (the engine writes exits first and
        has no decision_slot on them) gets the same sd(T) even when it is read in the same batch ahead of its mark's entries."""
        rows = super().read_new_rows()
        sdm = self.es["sd_by_mark"]
        for r in rows:
            if r.get("kind") == "entry" and _int(r.get("t_mark")) and _int(r.get("decision_slot")):
                sdm[str(r["t_mark"])] = r["decision_slot"]
        if len(sdm) > SD_MARKS_KEEP:
            for k in sorted(sdm, key=int)[:-SD_MARKS_KEEP]:
                del sdm[k]
        return rows

    def handle_row(self, row: dict[str, Any]) -> None:
        k = row.get("kind")
        if k == "start":
            self.es["starts_seen"] += 1
            if self.es["starts_seen"] > 1:
                self._gap(row["ms"], "host_restart")
        elif k == "gap":
            self._feed_gap(row)
            return
        elif k == "exit":
            reason = row.get("reason")
            if reason not in FOLLOWED_EXIT_REASONS:
                self._bump("exits_not_followed", str(reason))
                self._count("exit_not_followed")
                self._log("exit_not_followed", order_id=row.get("order_id"), reason=reason, seq=row.get("seq"))
                self._save()
                return
            o = self.state.orders.get(str(row.get("order_id")))
            if o is None or o["status"] in ("refused", "closed"):  # X8: a position the canary does not hold (capped out, refused, already sold): ignored and counted
                cause = "unknown_order" if o is None else o["status"]
                self._bump("exits_not_held", cause)
                self._count("exit_not_held")
                self._log("exit_not_held", order_id=row.get("order_id"), reason=reason, cause=cause, seq=row.get("seq"))
                self._save()
                return
            if reason == REASON_RESTART:  # the host's own exit, not a mark decision: no sd(T) to borrow, and the trip is not a parity sample
                o["gap"] = True
                o["restart_exit"] = True
                self._count("exit_restart")
            elif row.get("decision_slot") is None:
                sd = self.es["sd_by_mark"].get(str(row.get("t_mark")))
                row = {**row, "decision_slot": sd, "decision_slot_src": "mark_entry" if sd is not None else None}
        super().handle_row(row)

    # -- X6: no stop loss. The only backstop is the time stop, counted from the entry DECISION
    def backstops(self) -> None:
        now = self.now_ms()
        self.reconcile(only_stale=True)
        for oid, o in list(self.state.orders.items()):
            if o["status"] != "open":
                continue
            due = int(o["decision_ms"]) + TIME_STOP_MS
            if now >= due:
                o.setdefault("exit_decision_ms", due)  # the backstop's own instant, not the instant we noticed
                self.es["time_stops"] += 1
                self._try_exit(oid, REASON_TIME_STOP)

    # -- events from the port: landed slots and times (X9), re-entry (X7), P5
    def apply_events(self, events: list[dict[str, Any]]) -> None:
        by_mint = {o["mint"]: k for k, o in self.state.orders.items()}
        landed = [((ev.get("order_id") or by_mint.get(ev.get("mint"))), ev) for ev in events if ev.get("event") in ("buy_landed", "sell_landed")]
        super().apply_events(events)
        now = self.now_ms()
        for oid, ev in landed:
            o = self.state.orders.get(oid) if oid else None
            if o is None:
                continue
            if ev["event"] == "buy_landed" and o["status"] in ("open", "sell_sending", "sell_sent", "closed"):
                o["buy_landed_ms"] = o.get("buy_landed_ms") or o.get("t_open_ms") or now
            elif ev["event"] == "sell_landed" and o["status"] == "closed":
                o["sell_landed_slot"], o["sell_landed_ms"] = ev.get("landed_slot"), now
        self._release_free()
        if landed:
            self.p5_check()
        self._save()

    def latency_report(self) -> dict[str, Any]:
        return {"summary": (s := latency_summary(self.state.orders, self.cfg.slot_s, slot_s_by_date=self.slot_s_by_date)), "check": latency_check(s)}

    def p5_check(self) -> None:
        """DEC-032 P5, evaluated on every landed buy: a soft halt (new buys stop, exits run) until the manager clears it with the bridge stopped."""
        rep = self.latency_report()
        if rep["check"]["p5_soft_halt"]:
            self._latch(P5_LATCH, f"landing median excess {rep['summary']['window_x30_p50_slots']} slots over round(3.0 / slot_s), last {rep['summary']['window_n']} landed buys, counted from sd(T)")


# --- dry replay of a closed decision log --------------------------------------------------------------------------------------------------------------


class _Clock:
    def __init__(self, t: int = 0) -> None:
        self.t = t

    def __call__(self) -> int:
        return self.t


REPLAY_LAG_MS = 50  # the bridge reads each row this long after its decision instant (inside the 5 s age limit)


def log_decisions(root: str, bot_id: str = BOT_ID) -> dict[str, Any]:
    """What the log holds: entries (order id, mark, rank, in log order), the first followed exit row of each entered order, and every followed exit row."""
    entries, first_exit, followed, seen = [], {}, 0, set()
    for r in DecisionLog(root).rows(bot_id):
        if r.get("kind") == "entry" and str(r["order_id"]) not in seen:
            seen.add(str(r["order_id"]))
            entries.append({"order_id": str(r["order_id"]), "t_mark": r.get("t_mark"), "rank": r.get("rank")})
        elif r.get("kind") == "exit" and r.get("reason") in FOLLOWED_EXIT_REASONS:
            followed += 1
            first_exit.setdefault(str(r["order_id"]), r)
    return {"entries": entries, "first_exit": first_exit, "followed_exit_rows": followed}


def _md5(x: Any) -> str:
    return hashlib.md5(canon(x).encode()).hexdigest()


def replay_equivalence(cfg_dict: Mapping[str, Any], *, caps: bool = False) -> dict[str, Any]:
    """Run a closed decision log through a dry R4bBridge and compare the orders it would send with the log's decisions. No price, fill or P&L is read or made.
    caps=False (default): capacity gates OFF; `ok` needs the would-send orders (side, order_id) to equal the log's decisions one for one (a buy per entry row, a
    sell per entered order with a followed exit row), no refusal, no latch. caps=True: the C0 caps ON; `ok` needs the buys to be a subset of the entries, every
    entry that was not bought to have a logged skip, no buy after a capacity skip within the same mark (rank order), a sell for exactly the bought orders that
    have a followed exit row, every other followed exit row ignored and counted, no latch."""
    cfg = make_config(cfg_dict)
    port = B.DryPort(cfg.dry_balance_lamports)
    clk = _Clock(0)
    br = R4bBridge(cfg, port, now_ms=clk, dry_replay_caps=not caps)
    for row in br.read_new_rows():
        clk.t = int(row["ms"]) + REPLAY_LAG_MS
        br.handle_row(row)
        br._poll_ports()
        br.backstops()
        br._poll_ports()
    br._save()
    sends = sorted((w["side"], str(w["order_id"])) for w in port.would_send)
    bought = {oid for side, oid in sends if side == "buy"}
    sold = {oid for side, oid in sends if side == "sell"}
    lg = log_decisions(cfg.root)
    entered = {e["order_id"] for e in lg["entries"]}
    refused = {k: v for k, v in br.state.counts.items() if k.startswith("refused_")}
    out: dict[str, Any] = {"caps": bool(caps), "entries": len(entered), "orders": len(sends), "md5_orders": _md5(sends), "refused": refused, "latches": dict(br.state.latches),
                           "not_followed": dict(br.es["exits_not_followed"]), "exits_not_held": dict(br.es["exits_not_held"])}
    if not caps:
        want = sorted([("buy", oid) for oid in entered] + [("sell", oid) for oid in entered if oid in lg["first_exit"]])
        held_exits = sum(br.es["exits_not_held"].values())
        out.update(decisions=len(want), md5_decisions=_md5(want), exits_not_held_expected=lg["followed_exit_rows"] - len(sold),
                   ok=bool(sends == want and not refused and not br.state.latches and held_exits == lg["followed_exit_rows"] - len(sold)))
        return out
    skips: dict[str, str] = {}
    for line in (Path(br.log_path).read_text().splitlines() if Path(br.log_path).exists() else []):
        r = json.loads(line)
        if r.get("kind") == "entry_refused":
            skips[str(r["order_id"])] = str(r["why"])
    cap_seen: set[Any] = set()
    rank_ok = True
    for e in lg["entries"]:  # rank order within a mark: once a capacity skip happened in a mark, nothing later in that mark is bought
        if e["order_id"] in bought and e["t_mark"] in cap_seen:
            rank_ok = False
        if skips.get(e["order_id"], "").split(":")[0] in CAP_SKIPS:
            cap_seen.add(e["t_mark"])
    want_sold = {oid for oid in bought if oid in lg["first_exit"]}
    n_skip: dict[str, int] = {}
    for why in skips.values():
        n_skip[why] = n_skip.get(why, 0) + 1
    out.update(bought=len(bought), skipped=len(entered - bought), skips=n_skip, skips_logged=set(skips) == entered - bought, rank_ordered=rank_ok, subset=bought <= entered,
               sells_match=sold == want_sold, exits_not_held_expected=lg["followed_exit_rows"] - len(sold))
    out["ok"] = bool(out["subset"] and out["skips_logged"] and rank_ok and out["sells_match"] and not br.state.latches
                     and sum(br.es["exits_not_held"].values()) == lg["followed_exit_rows"] - len(sold))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="r4-b dry executor. DRY ONLY: live is not built.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("replay-check", help="dry replay of a closed decision log; prints the equivalence record")
    r.add_argument("--config", required=True)
    r.add_argument("--caps", action="store_true", help="C0 caps ON (default: capacity gates off)")
    t = sub.add_parser("run", help="tail the decision log and log the orders it would send (dry)")
    t.add_argument("--config", required=True)
    t.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    raw = json.loads(Path(a.config).read_text())
    if a.cmd == "replay-check":
        res = replay_equivalence(raw, caps=a.caps)
        print(canon(res))
        return 0 if res["ok"] else 1
    cfg = make_config(raw)
    br = R4bBridge(cfg, B.DryPort(cfg.dry_balance_lamports))
    print(f"r4b_arena_executor mode=dry bot={cfg.bot_id} stake={cfg.stake_lamports}", flush=True)
    br.run(once=a.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
