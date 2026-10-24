#!/usr/bin/env python3
"""dbc_price: Meteora Dynamic Bonding Curve (DBC) pricer for SCREENS. PAPER ONLY. No key, no RPC, no wallet.

Curve (P12, tools/dbc_decode.py; re-measured here by `validate`): constant liquidity L in sqrt-price space (Q64.64 sqrt price S, raw integers; L in the program's units):
    quote leg  dQ = L * |dS| / 2^128                 (lamports)
    base leg   dB = L * |dS| / (S0 * S1)             (base atoms)
    BUY  (quote in): S1 = S0 + dQ * 2^128 / L ;  SELL (base in): 1/S1 = 1/S0 + dB / L
Spot (quote atoms per base atom) = S^2 / 2^128; only RATIOS of spot are used (base decimals are not on the tape).
L is NOT on the tape (the config curve points are not in the swap tx). It is implied by each trade's own quote leg and carried forward per pool from the last trade whose
quote leg is >= MIN_L_QUOTE lamports (rounding < 1e-6 there). A trade whose implied L differs from the carried L by > L_JUMP is a segment crossing (counted, flagged); our small
stake normally does not cross; a fill that would pass the pool's migration threshold (quote_reserve_post + stake > migration_threshold) is REFUSED (counted).

FEES are per trade, from the decoded event (trading + protocol + referral). The fee token depends on the config (collect_fee_mode): 'output' = the fee is taken from what comes out
(buy: base, sell: quote); 'input' = from what goes in (buy: quote, sell: base). The mode is inferred per trade (the one that makes the quote-implied L and the base-implied L agree), then
per config by majority; a config with no evidence defaults to 'output' (flagged). Rate = fee / gross on the fee side. A fill is priced with the pool's LAST OBSERVED rate on that side
(causal; young pools decay from ~3% to 0.25%, so this is an approximation, said in the report); a pool with no earlier same-side print uses FALLBACK_FEE (1%).

round_trip: our own buy is re-injected into the exit state; pnl = proceeds - stake - 2 x send - rent. Guard exec/spot > 1.15 -> not filled, one send paid. Stranded (the pool
completed its curve before the sell lands, or the sell would not pay for itself): pnl = -stake - send - rent_stranded. No haircut (none measured here): optimistic by an unknown amount.

DATA GUARD: only <backfill>/meteora_dbc/tape-<hour>.jsonl.zst with the hour in the EXPLORATION window [2026-10-14T00, 2026-10-17T00). Hours [2026-10-17T00, 2026-10-24T00) are a
RESERVED holdout and are refused (raw and resolved path); `count_hour_files` reads only directory listings.
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

import numpy as np

BACKFILL_ROOT = "/data2/mal/venue-tape-backfill/meteora_dbc"
EXPLORE_START = datetime(2026, 10, 14, 0, tzinfo=timezone.utc)
EXPLORE_END = datetime(2026, 10, 17, 0, tzinfo=timezone.utc)  # exclusive; [10-17T00, 10-24T00) is the reserved holdout
HOLDOUT_END = datetime(2026, 10, 24, 0, tzinfo=timezone.utc)
HOLDOUT_ROOT = "/data2/mal/venue-tape-backfill/meteora_dbc_holdout"
# P0c: the holdout-window switch. DEFAULT = REFUSE the holdout. Only tools/dbc_holdout_read.py (the ONE-LOOK run) flips it, through enable_holdout_read(), which prints that it did.
# While it is on, the exploration hours and every root other than HOLDOUT_ROOT are refused instead.
HOLDOUT_READ_ENABLED = False
WSOL = "So11111111111111111111111111111111111111112"

SEND_LAMPORTS = 160_000  # DEC-031 Am.8
RENT_DECIDING = 0  # assumption: the sell closes the token ATA and the rent is refunded (Am.2 logic); stated, not measured on DBC
RENT_REPORT = 2_039_280  # classic SPL token ATA rent; Token-2022 base mints would be higher (not separated)
RENT_STRANDED = 2_039_280
GUARD = 1.15
LAT_S = 1.9
EXIT_LAT_SENS_S = 3.4
STAKE_DECIDING = 0.25
STAKE_BIG = 1.0
SLOT_S_FALLBACK = 0.4
FALLBACK_FEE = 0.01
Q128 = float(2 ** 128)
MIN_L_QUOTE = 10_000_000  # lamports: a quote leg this large gives L to < 1e-6 relative rounding
L_JUMP = 0.01
MODE_TOL = 1e-3
IMPACT_MAX = 0.02  # capacity bar: our 0.25 SOL buy may move the spot by at most 2% (owner/manager request)


class Refused(Exception):
    """A guard refused: nothing was read."""


def enable_holdout_read() -> None:
    """Switch the data guard to the holdout window [2026-10-17T00, 2026-10-24T00) under HOLDOUT_ROOT. Prints that it did. Nothing else changes."""
    global HOLDOUT_READ_ENABLED
    HOLDOUT_READ_ENABLED = True
    print("HOLDOUT READ ENABLED: data guard now accepts only [2026-10-17T00, 2026-10-24T00) under " + HOLDOUT_ROOT, flush=True)


SPENT_ROOT: str | None = None
SPENT_MARKER = "/data/mal/hunt-1008/push-1016/hunt29/dbc/HOLDOUT_READ_DONE_v2"


def enable_spent_exploration(root: str) -> None:
    """P17 (2026-10-24): the m10 one-look holdout [10-17T00, 10-24T00) was READ ONCE (marker SPENT_MARKER) and is now only disclosed exploration data. This switch accepts the whole
    [10-14T00, 10-24T00) range from ONE directory `root` (hardlinks of both tape dirs). Refused unless the marker exists. Not reachable from the default path; sets no holdout flag."""
    global SPENT_ROOT
    if not os.path.exists(SPENT_MARKER):
        raise Refused("holdout_not_spent_marker_missing")
    SPENT_ROOT = root
    print("SPENT-HOLDOUT EXPLORATION ENABLED (disclosed exploration only): " + root, flush=True)


SWAP3_IS_HOOK = True  # legacy (P14): any EvtSwap3 row marks its pool a hook pool
FOB_EVIDENCE = False  # legacy: fee mode only from the L-agreement inference


def enable_swap3_pricing() -> None:
    """P17: EvtSwap3 is the program's NEW generic swap event (emitted for every swap from 2026-10-16T04Z), not a transfer hook. Only real transfer-hook rows (event or ix name contains
    'TransferHook') mark a hook pool, and a Swap3 BUY's `fee_on_base_token` flag is used as exact fee-mode evidence."""
    global SWAP3_IS_HOOK, FOB_EVIDENCE
    SWAP3_IS_HOOK, FOB_EVIDENCE = False, True


CFG_XFER: set[str] = set()  # P17: configs whose base mint carries a Token-2022 transfer fee (transfer_fee_basis_points > 0): not modelled, their pools are excluded like hook pools
CFG_THR: dict[str, int] = {}  # P17: config -> migration_quote_threshold (EvtSwap3 rows do not carry migration_threshold; read from the config account)


def load_config_thresholds(summary_path: str) -> int:
    """Load {config: {thr, ...}} (tools.dbc_config.summary_map output as JSON) into CFG_THR. Used only for rows with no migration_threshold on the event."""
    m = json.loads(open(summary_path).read())
    CFG_THR.clear()
    CFG_THR.update({k: int(v["thr"]) for k, v in m.items()})
    CFG_XFER.clear()
    CFG_XFER.update(k for k, v in m.items() if int(v.get("transfer_fee_bps") or 0) > 0)
    return len(CFG_THR)


def window() -> tuple[datetime, datetime, str]:
    """(start, end exclusive, root) the guard currently accepts."""
    if SPENT_ROOT is not None and not HOLDOUT_READ_ENABLED:
        return (EXPLORE_START, HOLDOUT_END, SPENT_ROOT)
    return (EXPLORE_END, HOLDOUT_END, HOLDOUT_ROOT) if HOLDOUT_READ_ENABLED else (EXPLORE_START, EXPLORE_END, BACKFILL_ROOT)


# ----------------------------------------------------------------------------------------------------------------------- curve
def spot(S):
    """Quote atoms per base atom (up to the unknown power-of-ten decimals factor; ratios are exact)."""
    return (np.asarray(S, float) / 2.0 ** 64) ** 2


def buy_fill(S0, L, stake, rate, mode):
    """Buy with `stake` lamports at (S0, L). Returns (base_out_after_fee, S1, quote_to_curve). mode 0 = fee on output (base), 1 = fee on input (quote)."""
    stake = np.asarray(stake, float)
    rate = np.asarray(rate, float)
    q_in = np.where(np.asarray(mode) == 1, stake * (1.0 - rate), stake)
    with np.errstate(all="ignore"):
        S1 = S0 + q_in * Q128 / L
        b_gross = L * (S1 - S0) / (S0 * S1)
        b_out = np.where(np.asarray(mode) == 1, b_gross, b_gross * (1.0 - rate))
    return b_out, S1, q_in


def sell_fill(S0, L, base_in, rate, mode):
    """Sell `base_in` atoms at (S0, L). Returns (lamports_out_after_fee, S1). The fee is ALWAYS taken from the quote output (measured on 59,747 mode-1 sell rows; `mode` is kept
    in the signature for the callers and applies to buys only)."""
    base_in = np.asarray(base_in, float)
    rate = np.asarray(rate, float)
    with np.errstate(all="ignore"):
        S1 = 1.0 / (1.0 / S0 + base_in / L)
        q_gross = L * (S0 - S1) / Q128
        q_out = q_gross * (1.0 - rate)
    return q_out, S1


def round_trip(stake, S_e, L_e, spot_dec, rate_in, mode, S_x, L_x, rate_out, quote_room=None, send=SEND_LAMPORTS, rent=RENT_DECIDING, guard_ratio=GUARD,
               rent_stranded=RENT_STRANDED, ended=None, return_stranded=False):
    """Vectorised round trip in lamports. (S_e, L_e): state the buy lands on; spot_dec: decision-time spot; (S_x, L_x): state the sell lands on (WITHOUT our trade).
    quote_room: lamports of quote still fillable before the migration threshold at the entry state; a buy that does not fit is a guard refusal (one send paid).
    ended: True where the pool completed its curve before the sell lands (stranded). Returns (pnl, filled[, stranded])."""
    stake = np.asarray(stake, float)
    with np.errstate(all="ignore"):
        tok, S1, q_in = buy_fill(S_e, L_e, stake, rate_in, mode)
        bad = ~(np.isfinite(tok) & (tok > 0) & np.isfinite(np.asarray(S_x, float)) & np.isfinite(np.asarray(L_x, float)))
        guard = (((stake / tok) / spot_dec > guard_ratio) & ~bad)
        if quote_room is not None:
            guard = guard | ((q_in > quote_room) & ~bad)
        S_x2 = np.asarray(S_x, float) + q_in * Q128 / np.asarray(L_x, float)  # our buy re-injected into the exit state
        sv, S_after = sell_fill(S_x2, L_x, tok, rate_out, mode)
        st = bad | ~np.isfinite(sv) | (sv < send)
        if ended is not None:
            st = st | np.asarray(ended, bool)
        st = st & ~guard
        pnl = np.where(guard, -float(send), np.where(st, -stake - send - rent_stranded, sv - stake - 2 * send - rent))
    return (pnl, ~guard) + ((st,) if return_stranded else ())


def buy_impact(S0, L, stake, rate, mode):
    """Spot move caused by our own buy: spot(S1)/spot(S0) - 1."""
    _, S1, _ = buy_fill(S0, L, stake, rate, mode)
    return (S1 / S0) ** 2 - 1.0


# ----------------------------------------------------------------------------------------------------------------------- data guards
HOUR_RE = re.compile(r"tape-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl\.zst$")


def hour_of_name(name: str) -> datetime | None:
    m = HOUR_RE.search(name)
    return datetime.strptime(m.group(1), "%Y-%m-%dT%H").replace(tzinfo=timezone.utc) if m else None


def check_hour_path(path: str | os.PathLike, root: str | None = None) -> datetime:
    """Returns the hour of an accepted file; raises Refused otherwise. Default (HOLDOUT_READ_ENABLED False): only exploration hours [10-14T00, 10-17T00) directly under BACKFILL_ROOT;
    the holdout is refused. Enabled: only holdout hours directly under HOLDOUT_ROOT. The given path and its resolved target are both checked (a symlink cannot smuggle a hour in)."""
    w0, w1, wroot = window()
    root = wroot if root is None else root
    if os.path.realpath(root) != os.path.realpath(wroot):
        raise Refused(f"root_not_allowed_in_this_mode: {root}")
    raw = os.path.abspath(str(path))
    real = os.path.realpath(raw)
    for p, rt in ((raw, os.path.abspath(root)), (real, os.path.realpath(root))):
        h = hour_of_name(os.path.basename(p))
        if h is None:
            raise Refused(f"not_a_tape_hour_file: {path}")
        if not (w0 <= h < w1):
            raise Refused(("holdout_or_later_hour_refused" if not HOLDOUT_READ_ENABLED else "outside_holdout_window") + f": {path}")
        if os.path.dirname(p) != rt:
            raise Refused(f"outside_backfill_root: {path}")
    h0 = hour_of_name(os.path.basename(raw))
    assert h0 is not None
    return h0


def count_hour_files(root: str | None = None) -> dict[str, int]:
    """Directory listing only (never opens a file)."""
    root = window()[2] if root is None else root
    names = os.listdir(root) if os.path.isdir(root) else []  # names only, never a file open
    ex = ho = 0
    for n in names:
        h = hour_of_name(n)
        if h is None:
            continue
        if EXPLORE_START <= h < EXPLORE_END:
            ex += 1
        elif EXPLORE_END <= h < HOLDOUT_END:
            ho += 1
    return {"exploration_hours": ex, "holdout_hours_present": ho, "exploration_hours_needed": int((EXPLORE_END - EXPLORE_START).total_seconds() // 3600)}


def exploration_hours(start: datetime | None = None, end: datetime | None = None) -> list[datetime]:
    w0, w1, _ = window()
    out, t = [], max(start or w0, w0)
    end = min(end or w1, w1)
    while t < end:
        out.append(t)
        t += timedelta(hours=1)
    return out


def hour_file(h: datetime, root: str | None = None) -> str:
    root = window()[2] if root is None else root
    return f"{root}/tape-{h.strftime('%Y-%m-%dT%H')}.jsonl.zst"


# ----------------------------------------------------------------------------------------------------------------------- loading
def _zstd_lines(path: str | os.PathLike, root: str | None) -> Iterable[bytes]:
    check_hour_path(path, root)
    p = subprocess.Popen(["zstd", "-dc", "-q", str(path)], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
    assert p.stdout is not None
    try:
        for line in p.stdout:
            yield line
    finally:
        p.stdout.close()
        rc = p.wait()
    if rc != 0:
        raise RuntimeError(f"zstd -dc exited {rc} for {path}")


def read_hour(path: str | os.PathLike, root: str | None = None) -> dict[str, Any]:
    """One exploration hour (guarded): SOL-quoted trade rows in tape order, create rows, curve_complete rows, and `incomplete` (gap row present or no end row)."""
    trades, creates, completes = [], [], []
    gap, ended = False, False
    for line in _zstd_lines(path, root):
        if line.startswith(b'{"v":1,"venue":"meteora_dbc","type":"backfill_hour_end"'):
            ended = True
        elif b"backfill_incomplete" in line and not line.startswith(b'{"v":1,"venue":"meteora_dbc","slot"'):
            gap = True
        elif b'"type":"trade"' in line or b'"type": "trade"' in line:
            d = json.loads(line)
            if d.get("type") == "trade":
                trades.append(d)
        elif b'"curve_complete"' in line:
            d = json.loads(line)
            if d.get("type") == "curve_complete":
                completes.append(d)
        elif b'"type":"create"' in line or b'"type": "create"' in line:
            d = json.loads(line)
            if d.get("type") == "create":
                creates.append(d)
    return {"trades": trades, "creates": creates, "completes": completes, "incomplete": gap or not ended}


def block_time_s(r: dict[str, Any]) -> float:
    return float(r.get("block_time") or (r["t_recv_ms"] / 1000.0))


def row_fee(r: dict[str, Any]) -> int:
    return int(r.get("trading_fee") or 0) + int(r.get("protocol_fee") or 0) + int(r.get("referral_fee") or 0)


def usable_row(r: dict[str, Any]) -> bool:
    """Row-level filter: SOL-quoted swaps. ALL swap modes stay in the state chain (partial-fill rows from routers are 4% of rows and move the price like any swap).
    Pools with a transfer-hook or EvtSwap3 row are excluded from signals at pool level (`hook_pool`), see tools/dbc_screen.py."""
    return r.get("quote_mint") == WSOL


def hook_row(r: dict[str, Any]) -> bool:
    ev = str(r.get("event") or "")
    if SWAP3_IS_HOOK:
        return "TransferHook" in ev or ev == "EvtSwap3"
    return "TransferHook" in ev or "TransferHook" in str(r.get("ix") or "")


def rows_to_arrays(rows: Sequence[dict[str, Any]]) -> dict[str, np.ndarray]:
    """Columns for numpy (rows already filtered by usable_row). side 1 buy / 0 sell. S1 = next_sqrt_price (post), float64."""
    n = len(rows)
    a = {k: np.empty(n, np.float64) for k in ("bt", "S1", "ain", "aout", "fee", "qres", "thr")}
    a["slot"] = np.empty(n, np.int64)
    a["side"] = np.empty(n, np.int8)
    a["fob"] = np.full(n, -1, np.int8)  # Swap3 BUY: fee_on_base_token (1/0); -1 = not available
    for i, r in enumerate(rows):
        if r["side"] == "buy" and r.get("fee_on_base_token") is not None and str(r.get("event")).startswith("EvtSwap3"):
            a["fob"][i] = 1 if r["fee_on_base_token"] else 0
        a["slot"][i] = r["slot"]
        a["bt"][i] = block_time_s(r)
        a["side"][i] = 1 if r["side"] == "buy" else 0
        a["S1"][i] = float(int(r["next_sqrt_price"]))
        a["ain"][i], a["aout"][i], a["fee"][i] = int(r["amount_in"]), int(r["amount_out"]), row_fee(r)
        a["qres"][i], a["thr"][i] = int(r.get("quote_reserve_post") or 0), int(r.get("migration_threshold") or CFG_THR.get(r.get("config"), 0))
    return a


def derive(a: dict[str, np.ndarray], pid: np.ndarray, cfg: np.ndarray, iso: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """Per-trade derived columns. Requires rows sorted by (pid, tape order). Adds S0 (prev post sqrt price in the pool), mode evidence, config mode, rate, L (implied), Lhat
    (carried, causal = from earlier trades of the pool), q_leg. cfg = config id per row (int)."""
    n = len(pid)
    S1 = a["S1"]
    S0 = np.full(n, np.nan)
    same = np.zeros(n, bool)
    same[1:] = pid[1:] == pid[:-1]
    S0[1:] = np.where(same[1:], S1[:-1], np.nan)
    buy = a["side"] == 1
    ain, aout, fee = a["ain"], a["aout"], a["fee"]
    with np.errstate(all="ignore"):
        dS = np.abs(S1 - S0)
        # candidate L from quote and base legs under each fee mode
        # buy: quote in = ain; output mode: quote_curve = ain, base_gross = aout+fee. input mode: quote_curve = ain-fee, base_gross = aout
        # sell: base in = ain; output mode: base_curve = ain, quote_gross = aout+fee. input mode: base_curve = ain-fee, quote_gross = aout
        q0 = np.where(buy, ain, aout + fee)  # mode 0 quote leg
        q1 = np.where(buy, ain - fee, aout + fee)  # mode 1 (A2 fix: a SELL always pays its fee in QUOTE on the OUTPUT, in both modes)
        b0 = np.where(buy, aout + fee, ain)
        b1 = np.where(buy, aout, ain)
        Lq0, Lq1 = q0 * Q128 / dS, q1 * Q128 / dS
        Lb0, Lb1 = b0 * S0 * S1 / dS, b1 * S0 * S1 / dS
        # Mode LABELS are kept exactly as in 5ea8bf17 (QP A2: "fix the sell fee only"): evidence for sells still uses the legacy mode-1 sell legs (quote = amount_out, base = amount_in - fee).
        # Only the PRICING of sells (and the L implied by sells) uses the fee-on-quote-output legs below.
        q1_ev = np.where(buy, ain - fee, aout)
        b1_ev = np.where(buy, aout, ain - fee)
        Lq1_ev, Lb1_ev = q1_ev * Q128 / dS, b1_ev * S0 * S1 / dS
        e0, e1 = np.abs(Lq0 / Lb0 - 1.0), np.abs(Lq1_ev / Lb1_ev - 1.0)
        big = (np.minimum(q0, q1_ev) >= MIN_L_QUOTE) & (fee > 0)
        if iso is not None:
            big = big & iso  # evidence only from rows whose pre-state is certain (see order_fix)
        ev = np.where(big & (e0 < MODE_TOL) & (e1 >= MODE_TOL), 0, np.where(big & (e1 < MODE_TOL) & (e0 >= MODE_TOL), 1, -1))
        if FOB_EVIDENCE and "fob" in a:  # exact: fee on the base (output) token = pricer mode 0; fee on the quote (input) = mode 1
            ev = np.where(a["fob"] >= 0, 1 - a["fob"].astype(np.int64), ev)
    # config mode by majority of evidence
    mode = np.zeros(n, np.int8)
    known = np.zeros(n, bool)
    uc, inv = np.unique(cfg, return_inverse=True)
    c0 = np.bincount(inv, weights=(ev == 0).astype(float), minlength=len(uc))
    c1 = np.bincount(inv, weights=(ev == 1).astype(float), minlength=len(uc))
    cm = (c1 > c0).astype(np.int8)
    ck = (c0 + c1) > 0
    mode, known = cm[inv], ck[inv]
    with np.errstate(all="ignore"):
        qm = np.where(mode == 1, q1, q0)
        bm = np.where(mode == 1, b1, b0)
        L = qm * Q128 / dS
        gross_fee_side = np.where(mode == 1, np.where(buy, ain, ain), np.where(buy, aout + fee, aout + fee))
        # rate = fee / gross amount on the fee side: output mode buy: aout+fee (base), sell: aout+fee (quote); input mode buy: ain (quote), sell: ain (base)
        rate = np.where(fee > 0, fee / np.where((mode == 1) & buy, ain, aout + fee), 0.0)  # sells: fee / (amount_out + fee) in both modes
    qleg = qm
    good = same & np.isfinite(L) & (qleg >= MIN_L_QUOTE) & (L > 0)
    if iso is not None:
        good = good & iso
    # carried L: last good L strictly BEFORE the row (causal), reset at pool change
    idx = np.where(good, np.arange(n), -1)
    np.maximum.accumulate(idx, out=idx)
    prev_idx = np.concatenate([[-1], idx[:-1]])
    pool_start = np.zeros(n, np.int64)
    ps = np.flatnonzero(np.concatenate([[True], pid[1:] != pid[:-1]]))
    pool_start[ps] = ps
    np.maximum.accumulate(pool_start, out=pool_start)
    okc = (prev_idx >= pool_start) & (prev_idx >= 0)
    Lhat = np.where(okc, L[np.maximum(prev_idx, 0)], np.nan)
    # Lpost: carried L INCLUDING this row (state after the row), for entry/exit states
    okp = (idx >= pool_start) & (idx >= 0)
    Lpost = np.where(okp, L[np.maximum(idx, 0)], np.nan)
    return {"S0": S0, "mode": mode, "mode_known": known, "mode_ev": ev, "rate": rate, "L": L, "Lhat": Lhat, "Lpost": Lpost, "qleg": qleg, "bleg": bm, "same": same}


def ffill(x: np.ndarray, pid: np.ndarray, fallback: float) -> np.ndarray:
    """Forward fill NaNs within a pool (rows sorted by pool); fallback before the first value."""
    n = len(x)
    idx = np.where(np.isfinite(x), np.arange(n), -1)
    np.maximum.accumulate(idx, out=idx)
    ps = np.flatnonzero(np.concatenate([[True], pid[1:] != pid[:-1]]))
    start = np.zeros(n, np.int64)
    start[ps] = ps
    np.maximum.accumulate(start, out=start)
    ok = (idx >= start) & (idx >= 0)
    return np.where(ok, x[np.maximum(idx, 0)], fallback)


def validate(a: dict[str, np.ndarray], d: dict[str, np.ndarray], exclude: np.ndarray | None = None) -> dict[str, Any]:
    """Predict each trade's amount_out from its PRE state (S0, carried Lhat) and compare with the event. Only rows with a carried L and a known config mode."""
    buy = a["side"] == 1
    ok = np.isfinite(d["Lhat"]) & np.isfinite(d["S0"]) & d["mode_known"]
    if exclude is not None:
        ok = ok & ~exclude
    out: dict[str, Any] = {"rows": int(len(buy)), "rows_with_state": int(ok.sum()), "config_mode_known_share": float(d["mode_known"].mean())}
    with np.errstate(all="ignore"):
        mode = d["mode"]
        rate = d["rate"]
        # buy: the stake argument is ain (full); predicted base out after fee vs aout
        bo, S1p, _ = buy_fill(d["S0"], d["Lhat"], a["ain"], rate, mode)
        so, S1s = sell_fill(d["S0"], d["Lhat"], a["ain"], rate, mode)
        pred = np.where(buy, bo, so)
        err = pred / a["aout"] - 1.0
        s1err = np.where(buy, S1p, S1s) / a["S1"] - 1.0
    for nm, m in (("buy", buy), ("sell", ~buy)):
        mm = ok & m & (a["aout"] > 0)
        e = np.abs(err[mm])
        big = mm & (np.where(buy, a["ain"], a["aout"]) >= MIN_L_QUOTE)
        eb = np.abs(err[big])
        out[nm] = {
            "n": int(mm.sum()), "within_1bp": float((e <= 1e-4).mean()) if mm.any() else None, "within_10bp": float((e <= 1e-3).mean()) if mm.any() else None,
            "within_1pct": float((e <= 1e-2).mean()) if mm.any() else None, "median_abs_err": float(np.median(e)) if mm.any() else None,
            "n_ge_0p01sol": int(big.sum()), "ge_0p01sol_within_10bp": float((eb <= 1e-3).mean()) if big.any() else None,
            "ge_0p01sol_within_1pct": float((eb <= 1e-2).mean()) if big.any() else None,
            "post_sqrt_price_rel_err_median": float(np.median(np.abs(s1err[mm]))) if mm.any() else None,
        }
    return out


# ----------------------------------------------------------------------------------------------------------------------- within-slot order
def isolated(pid: np.ndarray, slot: np.ndarray) -> np.ndarray:
    """Rows whose (pool, slot) group has one row AND whose previous row's group has one row: their pre-state (the previous row's post price) is certain.
    The tape order inside a slot is NOT the execution order (measured: P14 REPORT), so a row after a multi-row slot has an uncertain pre-state."""
    n = len(pid)
    key_new = np.ones(n, bool)
    key_new[1:] = (pid[1:] != pid[:-1]) | (slot[1:] != slot[:-1])
    gid = np.cumsum(key_new) - 1
    gsz = np.bincount(gid)
    single = gsz[gid] == 1
    prev_single = np.zeros(n, bool)
    prev_single[1:] = single[:-1] & (pid[1:] == pid[:-1])
    return single & prev_single


def order_fix(a: dict[str, np.ndarray], pid: np.ndarray, d: dict[str, np.ndarray], tol: float = 0.05) -> tuple[np.ndarray, dict[str, int], np.ndarray]:
    """Permutation of rows (within each (pool, slot) group) so that each row's pre-state equals the previous row's post price. S moves linearly in the signed quote leg:
    S1 = S0 +/- q * 2^128 / L (exact on both sides), so with a reference L (carried from isolated rows) every row's S0 = S1 -/+ q*2^128/L is known, and the chain is
    the sequence S1_i -> S0_j. Groups that do not chain uniquely keep the tape order and are counted."""
    n = len(pid)
    buy = a["side"] == 1
    S1 = a["S1"]
    Lref = ffill(np.where(d["same"] & np.isfinite(d["L"]) & (d["qleg"] >= MIN_L_QUOTE) & d["iso"], d["L"], np.nan), pid, np.nan)
    # back fill rows before the pool's first isolated L
    rev = ffill(np.where(d["same"] & np.isfinite(d["L"]) & (d["qleg"] >= MIN_L_QUOTE) & d["iso"], d["L"], np.nan)[::-1], pid[::-1], np.nan)[::-1]
    Lref = np.where(np.isfinite(Lref), Lref, rev)
    with np.errstate(all="ignore"):
        dd = np.where(buy, 1.0, -1.0) * d["qleg"] * Q128 / Lref  # S1 - S0
    S0d = S1 - dd
    key_new = np.ones(n, bool)
    key_new[1:] = (pid[1:] != pid[:-1]) | (a["slot"][1:] != a["slot"][:-1])
    starts = np.flatnonzero(key_new)
    ends = np.concatenate([starts[1:], [n]])
    perm = np.arange(n)
    bad = np.zeros(n, bool)  # rows of groups that did not chain, and the row right after them (pre-state uncertain)
    st = {"groups_multi": 0, "reordered": 0, "chain_failed": 0}
    for s0, e0 in zip(starts, ends):
        k = e0 - s0
        if k < 2:
            continue
        st["groups_multi"] += 1
        idx = np.arange(s0, e0)
        if not np.all(np.isfinite(S0d[idx])):
            st["chain_failed"] += 1
            bad[s0:e0] = True
            continue
        succ = {}
        has_pred = set()
        ok = True
        for j in idx:
            diff = np.abs(S1[idx] - S0d[j])
            diff[j - s0] = np.inf
            i = int(np.argmin(diff))
            if diff[i] <= tol * abs(dd[j]) and (idx[i] not in succ):
                succ[idx[i]] = j
                has_pred.add(j)
        starts_ = [j for j in idx if j not in has_pred]
        if len(starts_) != 1:
            st["chain_failed"] += 1
            bad[s0:e0] = True
            continue
        order, cur = [], starts_[0]
        while cur is not None and len(order) <= k:
            order.append(cur)
            cur = succ.get(cur)
        if len(order) != k:
            st["chain_failed"] += 1
            bad[s0:e0] = True
            continue
        if order != list(idx):
            st["reordered"] += 1
        perm[s0:e0] = order
    bad[1:] |= bad[:-1] & (pid[1:] == pid[:-1])
    return perm, st, bad
