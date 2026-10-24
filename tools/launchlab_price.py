#!/usr/bin/env python3
"""launchlab_price: Raydium LaunchLab bonding-curve pricer for SCREENS. PAPER ONLY. No key, no RPC, no wallet.

Curve (measured on real decoded TradeEvent rows, see tools/test_launchlab_price.py and the P11 report): constant product with virtual reserves.
    in_reserve  = virtual_quote + real_quote          (lamports)
    out_reserve = virtual_base  - real_base           (base atoms; real_base = base sold so far)
    BUY  (quote in): fees are taken from amount_in first; net = amount_in - fees; base_out = out_reserve * net / (in_reserve + net)
                     real_quote += net; real_base += base_out
    SELL (base in) : gross = in_reserve * base_in / (out_reserve + base_in); the fees are taken from gross; amount_out = gross - fees
                     real_quote -= gross; real_base -= base_in
    Fee = protocol + platform + creator + share (the fields of the event). The rate is NOT one number: it depends on the pool's config
    (e.g. 0.25% protocol + 1.25% platform on a buy, 0.25% + 0.75% + 0.05% on a sell on the two fixture pools), so a fill is priced with the pool's LAST OBSERVED
    rate for that side (causal), with a fallback.
Spot price (quote atoms per base atom) = in_reserve / out_reserve. Progress p = real_base / total_base_sell (0..1); the curve ends at p = 1.

Round trip in lamports (`round_trip`): same structure as tools.arena_stack.round_trip but for this curve: our own buy is re-injected into the exit state
(rq' = rq_x + net, rb' = rb_x + tokens), our sell then moves the curve against us. pnl = proceeds - stake - 2 x send - rent. Guard: exec price / decision spot > 1.15 -> not
filled, one send paid. Stranded (no sell lands, or the sell would not pay for itself, or the curve ended): pnl = -stake - send - rent_stranded.
No haircut is applied (none is measured for this venue): that is optimistic by an unknown small amount and is said in the report.

DATA GUARD: `check_hour_path` accepts only <backfill>/launchlab/tape-<hour>.jsonl.zst with the hour in the EXPLORATION window [2026-09-24T00, 2026-10-17T00). Hours
[2026-10-17T00, 2026-10-24T00) are a RESERVED holdout and are refused (raw and resolved path); `count_hour_files` reads only directory listings.
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

BACKFILL_ROOT = "/data2/mal/venue-tape-backfill/launchlab"
EXPLORE_START = datetime(2026, 9, 24, 0, tzinfo=timezone.utc)
EXPLORE_END = datetime(2026, 10, 17, 0, tzinfo=timezone.utc)  # exclusive; [10-17T00, 10-24T00) is the reserved holdout
HOLDOUT_END = datetime(2026, 10, 24, 0, tzinfo=timezone.utc)

SEND_LAMPORTS = 160_000  # DEC-031 Am.8
RENT_DECIDING = 0  # assumption: the sell closes the token ATA and the rent is refunded (Am.2 logic); stated, not measured on LaunchLab
RENT_REPORT = 2_039_280
RENT_STRANDED = 2_039_280
GUARD = 1.15
LAT_S = 1.9
EXIT_LAT_SENS_S = 3.4
FAIL_FLAT = 0.15
STAKE_DECIDING = 0.25
STAKE_BIG = 1.0
SLOT_S_FALLBACK = 0.4
FALLBACK_FEE_BUY = 0.015  # only for a pool with no earlier observed rate on that side
FALLBACK_FEE_SELL = 0.015


class Refused(Exception):
    """A guard refused: nothing was read."""


# ----------------------------------------------------------------------------------------------------------------------- curve
def spot(vq, vb, rq, rb):
    """Quote atoms per base atom."""
    return (np.asarray(vq, float) + rq) / (np.asarray(vb, float) - rb)


def buy_out(vq, vb, rq, rb, amount_in, fee_rate):
    """Base atoms for `amount_in` lamports at state (rq, rb); fee taken from the input first. Returns (base_out, net_in)."""
    net = np.asarray(amount_in, float) * (1.0 - np.asarray(fee_rate, float))
    return (vb - rb) * net / (vq + rq + net), net


def sell_out(vq, vb, rq, rb, base_in, fee_rate):
    """Lamports received for `base_in` atoms at state (rq, rb) after the fee on the gross output. Returns (proceeds, gross)."""
    base_in = np.asarray(base_in, float)
    gross = (vq + rq) * base_in / (vb - rb + base_in)
    return gross * (1.0 - np.asarray(fee_rate, float)), gross


def exact_buy(vq: int, vb: int, rq: int, rb: int, amount_in: int, fees: int) -> int:
    """Integer form of the program's buy output (verified exact on real rows)."""
    net = amount_in - fees
    return (vb - rb) * net // (vq + rq + net)


def exact_sell_gross(vq: int, vb: int, rq: int, rb: int, base_in: int) -> int:
    return (vq + rq) * base_in // (vb - rb + base_in)


def row_fees(r: dict[str, Any]) -> int:
    return int(r["protocol_fee"]) + int(r["platform_fee"]) + int(r["creator_fee"]) + int(r["share_fee"])


def check_trade_row(r: dict[str, Any]) -> dict[str, Any]:
    """Re-derive one decoded trade row from its PRE state and amounts. Returns the checks (all True = the row reproduces exactly)."""
    vq, vb = int(r["virtual_quote"]), int(r["virtual_base"])
    rq0, rb0, rq1, rb1 = int(r["real_quote_before"]), int(r["real_base_before"]), int(r["real_quote_after"]), int(r["real_base_after"])
    fees = row_fees(r)
    out: dict[str, Any] = {"side": r["side"], "fees": fees}
    if r["side"] == "buy" and r.get("exact_in", True):
        ain = int(r["amount_in"])
        bo = exact_buy(vq, vb, rq0, rb0, ain, fees)
        out.update(base_out_ok=bo == int(r["amount_out"]) and bo == rb1 - rb0, quote_ok=rq1 - rq0 == ain - fees, rate=fees / ain if ain else None)
    elif r["side"] == "sell" and r.get("exact_in", True):
        bin_ = int(r["amount_in"])
        g = exact_sell_gross(vq, vb, rq0, rb0, bin_)
        out.update(gross_ok=g == rq0 - rq1, out_ok=int(r["amount_out"]) == g - fees, base_ok=rb0 - rb1 == bin_, rate=fees / g if g else None)
    else:
        out["skipped"] = "exact_out_or_other"
    return out


# ----------------------------------------------------------------------------------------------------------------------- round trip
def round_trip(stake, vq, vb, rq_e, rb_e, spot_dec, fee_in, rq_x, rb_x, fee_out, send=SEND_LAMPORTS, rent=RENT_DECIDING, guard_ratio=GUARD,
               rent_stranded=RENT_STRANDED, ended=None, return_stranded=False):
    """Vectorised round trip in lamports. (rq_e, rb_e): state the buy lands on; spot_dec: decision-time spot; (rq_x, rb_x): state the sell lands on (WITHOUT our trade).
    ended: bool per trip, True where the exit lands after the curve ended (no on-curve sell): priced stranded.
    Returns (pnl, filled[, stranded])."""
    stake = np.asarray(stake, float)
    with np.errstate(all="ignore"):
        tok, net = buy_out(vq, vb, rq_e, rb_e, stake, fee_in)
        bad = ~(np.isfinite(tok) & (tok > 0) & np.isfinite(np.asarray(rq_x, float)) & np.isfinite(np.asarray(rb_x, float)))
        guard = ((stake / tok) / spot_dec > guard_ratio) & ~bad
        rq2, rb2 = np.asarray(rq_x, float) + net, np.asarray(rb_x, float) + tok  # our buy re-injected
        sv, gross = sell_out(vq, vb, rq2, rb2, tok, fee_out)
        over = ~(gross <= rq2)  # physical bound: the sell cannot take more quote than the pool holds
        st = bad | over | (sv < send)
        if ended is not None:
            st = st | np.asarray(ended, bool)
        st = st & ~guard
        pnl = np.where(guard, -float(send), np.where(st, -stake - send - rent_stranded, sv - stake - 2 * send - rent))
    return (pnl, ~guard) + ((st,) if return_stranded else ())


# ----------------------------------------------------------------------------------------------------------------------- data guards
HOUR_RE = re.compile(r"tape-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl\.zst$")


def hour_of_name(name: str) -> datetime | None:
    m = HOUR_RE.search(name)
    return datetime.strptime(m.group(1), "%Y-%m-%dT%H").replace(tzinfo=timezone.utc) if m else None


def check_hour_path(path: str | os.PathLike, root: str = BACKFILL_ROOT) -> datetime:
    """Returns the hour of an EXPLORATION file; raises Refused for the holdout, for any hour outside the window, and for any path not directly under `root`.
    Both the given path and its resolved target are checked (a symlink cannot smuggle a holdout hour in)."""
    raw = os.path.abspath(str(path))
    real = os.path.realpath(raw)
    for p, rt in ((raw, os.path.abspath(root)), (real, os.path.realpath(root))):
        h = hour_of_name(os.path.basename(p))
        if h is None:
            raise Refused(f"not_a_tape_hour_file: {path}")
        if h >= EXPLORE_END:
            raise Refused(f"holdout_or_later_hour_refused: {path}")
        if h < EXPLORE_START:
            raise Refused(f"before_exploration_window: {path}")
        if os.path.dirname(p) != rt:
            raise Refused(f"outside_backfill_root: {path}")
    h0 = hour_of_name(os.path.basename(raw))
    assert h0 is not None
    return h0


def count_hour_files(root: str = BACKFILL_ROOT) -> dict[str, int]:
    """Directory listing only (never opens a file): exploration hours present, holdout hours present."""
    names = os.listdir(root) if os.path.isdir(root) else []
    ex = ho = 0
    for n in names:
        h = hour_of_name(n)
        if h is None:
            continue
        if EXPLORE_START <= h < EXPLORE_END:
            ex += 1
        elif EXPLORE_END <= h < HOLDOUT_END:
            ho += 1
    return {"exploration_hours": ex, "holdout_hours": ho, "exploration_hours_needed": int((EXPLORE_END - EXPLORE_START).total_seconds() // 3600)}


def exploration_hours() -> list[datetime]:
    out, t = [], EXPLORE_START
    while t < EXPLORE_END:
        out.append(t)
        t += timedelta(hours=1)
    return out


def hour_file(h: datetime, root: str = BACKFILL_ROOT) -> str:
    return f"{root}/tape-{h.strftime('%Y-%m-%dT%H')}.jsonl.zst"


# ----------------------------------------------------------------------------------------------------------------------- loading
def read_hour_rows(path: str | os.PathLike) -> Iterable[dict[str, Any]]:
    """Trade rows of ONE exploration hour file (guarded). Streams `zstd -dc`; a non-zero exit raises."""
    check_hour_path(path)
    p = subprocess.Popen(["zstd", "-dc", "-q", str(path)], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
    assert p.stdout is not None
    try:
        for line in p.stdout:
            if b'"trade"' not in line:
                continue
            d = json.loads(line)
            if d.get("type") == "trade":
                yield d
    finally:
        p.stdout.close()
        rc = p.wait()
    if rc != 0:
        raise RuntimeError(f"zstd -dc exited {rc} for {path}")


def read_hour(path: str | os.PathLike, root: str = BACKFILL_ROOT) -> tuple[list[dict[str, Any]], bool]:
    """(trade rows, incomplete) of one exploration hour. incomplete = the file carries a `backfill_incomplete` gap row, or has no `backfill_hour_end` row."""
    check_hour_path(path, root)
    p = subprocess.Popen(["zstd", "-dc", "-q", str(path)], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
    assert p.stdout is not None
    rows: list[dict[str, Any]] = []
    gap = False
    ended = False
    try:
        for line in p.stdout:
            if b'"trade"' in line:
                d = json.loads(line)
                if d.get("type") == "trade":
                    rows.append(d)
            elif b"backfill_incomplete" in line:
                gap = True
            elif b"backfill_hour_end" in line:
                ended = True
    finally:
        p.stdout.close()
        rc = p.wait()
    if rc != 0:
        raise RuntimeError(f"zstd -dc exited {rc} for {path}")
    return rows, (gap or not ended)


def count_migrate_ix(path: str | os.PathLike, root: str = BACKFILL_ROOT) -> tuple[int, int]:
    """(migrate instruction rows, create rows) of one exploration hour (guarded). For the I5 fetch estimate only."""
    check_hour_path(path, root)
    p = subprocess.Popen(["zstd", "-dc", "-q", str(path)], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
    assert p.stdout is not None
    mig = cre = 0
    try:
        for line in p.stdout:
            if b'"ix"' in line and b"igrate" in line:
                d = json.loads(line)
                if d.get("type") == "ix" and any("igrate" in str(x) for x in d.get("ix") or []):
                    mig += 1
            elif b'"type": "create"' in line or b'"type":"create"' in line:
                cre += 1
    finally:
        p.stdout.close()
        p.wait()
    return mig, cre


def block_time_s(r: dict[str, Any]) -> float:
    return float(r.get("block_time") or (r["t_recv_ms"] / 1000.0))


def rows_to_arrays(rows: Sequence[dict[str, Any]]) -> dict[str, np.ndarray]:
    """Columns for numpy. side 1 buy / 0 sell; status 0 fund / 1 migrate / 2 trade."""
    n = len(rows)
    a = {k: np.empty(n, np.float64) for k in ("vq", "vb", "tbs", "rq0", "rb0", "rq1", "rb1", "ain", "aout", "fees", "bt")}
    a["slot"] = np.empty(n, np.int64)
    a["side"] = np.empty(n, np.int8)
    a["status"] = np.empty(n, np.int8)
    a["exact_in"] = np.empty(n, np.int8)
    st = {"fund": 0, "migrate": 1, "trade": 2}
    for i, r in enumerate(rows):
        a["slot"][i] = r["slot"]
        a["bt"][i] = block_time_s(r)
        a["side"][i] = 1 if r["side"] == "buy" else 0
        a["vq"][i], a["vb"][i], a["tbs"][i] = r["virtual_quote"], r["virtual_base"], r["total_base_sell"]
        a["rq0"][i], a["rb0"][i], a["rq1"][i], a["rb1"][i] = r["real_quote_before"], r["real_base_before"], r["real_quote_after"], r["real_base_after"]
        a["ain"][i], a["aout"][i], a["fees"][i] = r["amount_in"], r["amount_out"], row_fees(r)
        a["status"][i] = st.get(r.get("pool_status"), 2)
        a["exact_in"][i] = 1 if r.get("exact_in", True) else 0
    return a


def trade_fee_rate(side, ain, aout, fees):
    """Observed fee rate per trade: buy = fees / amount_in; sell = fees / gross, gross = amount_out + fees."""
    side = np.asarray(side)
    ain, aout, fees = (np.asarray(x, float) for x in (ain, aout, fees))
    with np.errstate(all="ignore"):
        return np.where(side == 1, fees / ain, fees / (aout + fees))
