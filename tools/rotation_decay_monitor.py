"""rotation_decay_monitor: DEC-036 section 9 item 9, the per-book decay monitor for ROTATION-lane live closed trades.

  python -m tools.rotation_decay_monitor --bot BOT_ID --calibration CARD_CALIBRATION.json [--ledger-file PATH] [--since-ms MS] [--json]

Exit code (as tools/floor_decay_monitor.py): 0 ok, 10 a STEP-DOWN flag (section 6 item 5 table), 20 STOP / retire to paper (table row 0.05, the retire
check, or the per-book loss cap), 2 input error (fails closed).

READ-ONLY. It opens the rotation ledger (default ROTATION_LEDGER_PATH of tools.forge.rotation_lane_executor) and the calibration file for reading, prints to stdout and
does nothing else: no file written, no STOP/HALT touched, no RPC, no network, no lock. It never reads FLOOR's ledger. tools/floor_decay_monitor.py and its constants are not
changed or imported. A manager MiScusi job runs it; the manager (not this tool) takes the step as a record (DEC-036 section 7).

What it computes (all DEC-036 section 6):
  1. Data. Per closed live trade of --bot only (a position closed by an exit, a writeoff or a failed buy fill), r = lamports realized after all fees (the sum of every row of
     the position, as the executor's fold_rotation sums it); x = r / s with s the stake of that trade's send_intent. A fee row with no open position is a cost, not a trade.
  2. Population. s = stake_lamports of the book's latest `start` row; the population starts at the first start row of the trailing run of start rows at s (a restart at the same
     stake continues it; a stake change starts a new one and S resets). A close counts in the CUSUM only if its intent stake == s and the intent is at or after the population start.
     The calibration file must be for that rung (bot_id and stake_lamports must match, else error).
  3. CUSUM (one-sided, down). w = clip(x, winsor_lo, winsor_hi); kappa = (mu0 - mu1) / 2; S_0 = 0, S_t = max(0, S_{t-1} + (mu0 - kappa) - w_t). Warning: S >= h_w. Alarm: S >= h_a.
     mu0, mu1, h_w, h_a and the winsor bounds are the CARD's pinned numbers (section 6 items 2-4); this tool does not calibrate and does not choose h. mu1 >= mu0 (or mu1 > 0) is an error.
  4. Ladder action (section 6 item 5): 0.25 -> warning 0.125, alarm 0.05; 0.125 -> 0.05, 0.05; 0.10 -> 0.05, 0.05; 0.05 -> book STOP, retire to paper (both). If the calibration's optional
     `mu0_by_rung` gives mu0 <= 0 for the target rung, the step is a STOP instead (section 6 item 3). The alarm outranks the warning.
  5. Retire check (section 6 item 6), RAW basis: from closed trade 50 of the book (all its trades at every stake, nothing dropped, not winsorized), m = (n * mean(x) + n0 * mu_p) / (n + n0),
     mu_p = - mean over the book's trades of the round-trip cost fraction at each trade's stake (section 8 table, or the calibration's optional `cost_frac_by_stake` override). m < 0 is retire.
     Also retire: the per-book loss cap (section 5 item 2: 0.40 SOL, 0.75 SOL once the book has a 250_000_000 start row) hit, as realized <= -cap, or, with no open position, as the executor's pre-send
     test realized - (s + R) <= -cap.
  --since-ms MS: also evaluate every closed trade with close ts >= MS as the state stood right after it, and print one `fired_at_close` line per close with a flag. The exit code is the worst of the
     latest state and every evaluated close. Per-close CUSUM evaluation covers closes of the current population (the rung the calibration is for); the retire check covers every close of the book.

Calibration file (schema `rotation-decay-calibration.v1`, one rung): {"schema", "bot_id", "stake_lamports", "winsor_lo", "winsor_hi", "mu0", "mu1", "h_w", "h_a", "n0" (default 25),
  "sim_source_sha256" (64 hex: the calibration run's sim source, section 6 item 4), optional "mu0_by_rung" {"<lamports>": mu0}, optional "cost_frac_by_stake" {"<lamports>": fraction}}.
Not a registration, not a gate read, not a profit claim. These are brakes, not tests (section 6 item 8).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

from tools.forge import rotation_lane_executor as RX

SCHEMA = "rotation-decay-calibration.v1"
RETIRE_FROM_N = 50
N0_DEFAULT = 25
FAR = 1 << 62
SHA64 = re.compile(r"^[0-9a-f]{64}$")
RUNGS = RX.ROTATION_STAKE_RUNGS_LAMPORTS
# DEC-036 section 6 item 5. value = (warning target, alarm target); None = book STOP, retire to paper.
LADDER: dict[int, tuple[int | None, int | None]] = {
    250_000_000: (125_000_000, 50_000_000),
    125_000_000: (50_000_000, 50_000_000),
    100_000_000: (50_000_000, 50_000_000),
    50_000_000: (None, None),
}
# DEC-036 section 8 all-in round trip c, in stake units (arithmetic table, not a measurement): 3.594%, 3.825%, 4.035%, 5.268%.
COST_FRAC = {50_000_000: 0.03594, 100_000_000: 0.03825, 125_000_000: 0.04035, 250_000_000: 0.05268}


class MonitorError(ValueError):
    pass


def _num(d: dict[str, Any], k: str) -> float:
    v = d.get(k)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
        raise MonitorError(f"calibration: `{k}` must be a finite number")
    return float(v)


def load_calibration(path: Path, bot: str) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise MonitorError(f"calibration unreadable: {exc}") from None
    if not isinstance(raw, dict):
        raise MonitorError("calibration is not a JSON object")
    if raw.get("schema") != SCHEMA:
        raise MonitorError(f"calibration schema must be {SCHEMA}")
    if raw.get("bot_id") != bot:
        raise MonitorError(f"calibration bot_id {raw.get('bot_id')!r} != --bot {bot!r}")
    st = raw.get("stake_lamports")
    if isinstance(st, bool) or not isinstance(st, int) or st not in RUNGS:
        raise MonitorError("calibration stake_lamports must be one of the rotation rungs")
    cal: dict[str, Any] = {"bot_id": bot, "stake_lamports": st}
    for k in ("winsor_lo", "winsor_hi", "mu0", "mu1", "h_w", "h_a"):
        cal[k] = _num(raw, k)
    if not cal["winsor_lo"] < cal["winsor_hi"]:
        raise MonitorError("calibration: winsor_lo must be < winsor_hi")
    if not cal["mu1"] < cal["mu0"]:
        raise MonitorError("calibration: mu1 must be < mu0 (kappa = (mu0 - mu1) / 2 > 0)")
    if cal["mu1"] > 0:
        raise MonitorError("calibration: mu1 = min(0, mu0 - delta) must be <= 0")
    if not 0 < cal["h_w"] < cal["h_a"]:
        raise MonitorError("calibration: need 0 < h_w < h_a (warning 20% rate is the lower threshold)")
    n0 = raw.get("n0", N0_DEFAULT)
    if isinstance(n0, bool) or not isinstance(n0, int) or n0 < 0:
        raise MonitorError("calibration: n0 must be a non-negative integer")
    cal["n0"] = n0
    sha = raw.get("sim_source_sha256")
    if not (isinstance(sha, str) and SHA64.match(sha)):
        raise MonitorError("calibration: sim_source_sha256 must be 64 lowercase hex")
    cal["sim_source_sha256"] = sha
    for opt in ("mu0_by_rung", "cost_frac_by_stake"):
        m = raw.get(opt)
        cal[opt] = {}
        if m is not None:
            if not isinstance(m, dict):
                raise MonitorError(f"calibration: {opt} must be an object")
            try:
                cal[opt] = {int(k): _num(m, k) for k in m}
            except ValueError:
                raise MonitorError(f"calibration: {opt} keys must be lamports") from None
    return cal


def read_rows(path: Path) -> list[dict[str, Any]]:
    """Complete lines only: the last element of split(b"\\n") is "" (clean end) or a torn tail (a write in progress), dropped either way. A complete line that is not a JSON
    object with a string `ev` is an error (fails closed)."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise MonitorError(f"ledger unreadable: {exc}") from None
    lines = data.split(b"\n")
    lines.pop()
    rows = []
    for n, ln in enumerate(lines, 1):
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except ValueError:
            raise MonitorError(f"{path}:{n}: unreadable line") from None
        if not isinstance(r, dict) or not isinstance(r.get("ev"), str):
            raise MonitorError(f"{path}:{n}: row is not an object with an `ev`")
        rows.append(r)
    return rows


def _int(r: dict[str, Any], k: str, required: bool = False) -> int:
    v = r.get(k)
    if v is None and not required:
        return 0
    if isinstance(v, bool) or not isinstance(v, int):
        raise MonitorError(f"row has no integer `{k}`")
    return v


def fold_book(rows: Sequence[dict[str, Any]], bot: str) -> dict[str, Any]:
    """Only `bot`'s rows. trades: per closed position {ts_ms, r, stake, intent_ts_ms, x}; starts: [(ts_ms, stake)]; n_open at the end. Same accounting as the executor's fold_rotation."""
    open_: dict[str, dict[str, Any]] = {}
    trades: list[dict[str, Any]] = []
    starts: list[tuple[int, int]] = []
    seen: set[str] = set()
    for r in rows:
        if r.get("bot_id") != bot:
            continue
        rid = r.get("row_id")
        if rid is not None:
            if rid in seen:
                continue
            seen.add(rid)
        ev = r["ev"]
        if ev == "snap":
            continue
        if ev == "start":
            lane = r.get("lane") if isinstance(r.get("lane"), dict) else {}
            st = r.get("stake_lamports", lane.get("stake_lamports"))
            if isinstance(st, int) and not isinstance(st, bool):
                starts.append((_int(r, "ts_ms", True), st))
            continue
        if not isinstance(r.get("mint"), str):
            raise MonitorError("row has no mint")
        ts, key = _int(r, "ts_ms", True), r["mint"]
        delta = _int(r, "sol_in_lamports") - _int(r, "sol_out_lamports")
        if ev == "send_intent":
            open_[key] = {"stake": _int(r, "stake_lamports", True), "filled": False, "ts_ms": ts, "cum": delta}
        elif key not in open_:
            continue  # a stray fee row: a real cost, not a trade
        elif ev == "fill":
            p = open_[key]
            p["cum"] += delta
            if r.get("ok") is True:
                p["filled"] = True
            else:
                p = open_.pop(key)
                trades.append(_trade(ts, p["cum"], p))
        elif ev in ("exit", "writeoff"):
            p = open_.pop(key)
            trades.append(_trade(ts, p["cum"] + delta, p))
        elif ev == "fee":
            open_[key]["cum"] += delta
    return {"trades": trades, "starts": starts, "n_open": len(open_), "open_stake": sum(p["stake"] for p in open_.values())}


def _trade(ts: int, r: int, p: dict[str, Any]) -> dict[str, Any]:
    if p["stake"] <= 0:
        raise MonitorError("send_intent stake must be positive")
    return {"ts_ms": ts, "r": r, "stake": p["stake"], "intent_ts_ms": p["ts_ms"], "x": r / p["stake"]}


def cusum_path(ws: Sequence[float], mu0: float, mu1: float) -> list[float]:
    """S_0 = 0, S_t = max(0, S_{t-1} + (mu0 - kappa) - w_t), kappa = (mu0 - mu1) / 2 (DEC-036 section 6 item 3). Returns S_1..S_n."""
    kappa = (mu0 - mu1) / 2
    s, out = 0.0, []
    for w in ws:
        s = max(0.0, s + (mu0 - kappa) - w)
        out.append(s)
    return out


def winsorize(x: float, lo: float, hi: float) -> float:
    return min(max(x, lo), hi)


def ladder_action(stake: int, warning: bool, alarm: bool, mu0_by_rung: dict[int, float] | None = None) -> dict[str, Any]:
    """The section 6 item 5 row for `stake`. action: stay | step_down | stop_retire; to_stake for a step_down."""
    if stake not in LADDER:
        raise MonitorError("stake is not a rotation rung")
    if not (warning or alarm):
        return {"action": "stay", "to_stake": None, "reason": "no flag"}
    tgt = LADDER[stake][1 if alarm else 0]
    kind = "alarm" if alarm else "warning"
    if tgt is None:
        return {"action": "stop_retire", "to_stake": None, "reason": f"{kind} at the 0.05 rung: book STOP, retire to paper"}
    if mu0_by_rung and mu0_by_rung.get(tgt, 1.0) <= 0:
        return {"action": "stop_retire", "to_stake": None, "reason": f"{kind}: mu0 at target rung {tgt} <= 0, the step is a book STOP (section 6 item 3)"}
    return {"action": "step_down", "to_stake": tgt, "reason": f"{kind} at stake {stake}"}


def retire_check(trades: Sequence[dict[str, Any]], n0: int, cost_frac: dict[int, float]) -> dict[str, Any]:
    """Raw basis: x = r / s for every trade of the book, nothing dropped, not winsorized; mu_p = -mean(cost fraction at each trade's stake)."""
    n = len(trades)
    out: dict[str, Any] = {"n": n, "armed": n >= RETIRE_FROM_N, "n0": n0, "fired": False}
    if not n:
        return out
    mean_x = sum(t["x"] for t in trades) / n
    mu_p = -sum(cost_frac[t["stake"]] for t in trades) / n
    m = (n * mean_x + n0 * mu_p) / (n + n0)
    out.update({"mean_x": mean_x, "mu_p": mu_p, "m": m})
    out["fired"] = bool(out["armed"] and m < 0)
    return out


def loss_cap_lamports(starts: Sequence[tuple[int, int]]) -> int:
    return RX.ROTATION_BOOK_LOSS_CAP_STEPPED_LAMPORTS if any(st == 250_000_000 for _, st in starts) else RX.ROTATION_BOOK_LOSS_CAP_FIRST_LAMPORTS


def state_at(trades: Sequence[dict[str, Any]], pop: Sequence[dict[str, Any]], cal: dict[str, Any], cost_frac: dict[int, float], cap: int, n_open: int, s: int) -> dict[str, Any]:
    """The flags after the last trade of `trades` (all book trades so far) and `pop` (the population trades so far)."""
    ws = [winsorize(t["x"], cal["winsor_lo"], cal["winsor_hi"]) for t in pop]
    path = cusum_path(ws, cal["mu0"], cal["mu1"])
    S = path[-1] if path else 0.0
    warning, alarm = S >= cal["h_w"], S >= cal["h_a"]
    la = ladder_action(s, warning, alarm, cal["mu0_by_rung"])
    ret = retire_check(trades, cal["n0"], cost_frac)
    realized = sum(t["r"] for t in trades)
    cap_hit = realized <= -cap or (n_open == 0 and realized - (s + RX.ROTATION_FEE_RESERVE_LAMPORTS) <= -cap)
    code = 20 if (la["action"] == "stop_retire" or ret["fired"] or cap_hit) else 10 if la["action"] == "step_down" else 0
    flags = [k for k, v in (("warning", warning), ("alarm", alarm), ("retire", ret["fired"]), ("loss_cap", cap_hit)) if v]
    return {"S": S, "h_w": cal["h_w"], "h_a": cal["h_a"], "warning": warning, "alarm": alarm, "n_population": len(pop), "ladder": la, "retire": ret,
            "book_realized_lamports": realized, "loss_cap_lamports": cap, "loss_cap_hit": cap_hit, "flags": flags, "exit_code": code}


def run(ledger: Path, bot: str, cal: dict[str, Any], since_ms: int | None = None) -> dict[str, Any]:
    RX_bad = RX.bot_id_problem(bot)
    if RX_bad:
        raise MonitorError(f"--bot invalid: {RX_bad}")
    book = fold_book(read_rows(ledger), bot)
    starts, trades = book["starts"], book["trades"]
    if not starts:
        raise MonitorError("no start row with a stake for this bot in the ledger")
    s, pop_start = starts[-1][1], starts[-1][0]
    for ts, st in reversed(starts):
        if st != s:
            break
        pop_start = ts
    if s not in LADDER:
        raise MonitorError(f"latest start stake {s} is not a rotation rung")
    if cal["stake_lamports"] != s:
        raise MonitorError(f"calibration is for rung {cal['stake_lamports']}, the book's current stake is {s}: give the card's calibration for the current rung")
    cost_frac = {**COST_FRAC, **cal["cost_frac_by_stake"]}
    for t in trades:
        if t["stake"] not in cost_frac:
            raise MonitorError(f"trade stake {t['stake']} has no cost fraction")
    cap = loss_cap_lamports(starts)

    def in_pop(t: dict[str, Any]) -> bool:
        return t["stake"] == s and t["intent_ts_ms"] >= pop_start

    res = state_at(trades, [t for t in trades if in_pop(t)], cal, cost_frac, cap, book["n_open"], s)
    res.update({"bot_id": bot, "stake_lamports": s, "population_start_ms": pop_start, "n_closed_book": len(trades),
                "n_closed_other_stake_or_before_start": sum(1 for t in trades if not in_pop(t)), "n_open": book["n_open"],
                "calibration": {k: cal[k] for k in ("mu0", "mu1", "h_w", "h_a", "winsor_lo", "winsor_hi", "n0", "sim_source_sha256")}})
    if since_ms is not None:
        fired = []
        for i, t in enumerate(trades):
            if t["ts_ms"] < since_ms:
                continue
            sub = trades[:i + 1]
            # open count after this close is not reconstructed: the no-open pre-send form is used only at the latest state; per close, realized <= -cap only
            st = state_at(sub, [u for u in sub if in_pop(u)], cal, cost_frac, cap, 1, s)
            if st["exit_code"]:
                fired.append({"ts_ms": t["ts_ms"], "flags": st["flags"], "action": st["ladder"]["action"], "to_stake": st["ladder"]["to_stake"], "exit_code": st["exit_code"], "S": st["S"]})
        res["fired_at_close"] = fired
        res["exit_code_latest"] = res["exit_code"]
        res["exit_code"] = max([res["exit_code"]] + [c["exit_code"] for c in fired])
    return res


def render(res: dict[str, Any]) -> str:
    cal, ret, la = res["calibration"], res["retire"], res["ladder"]
    L = [f"rotation_decay_monitor (DEC-036 section 9 item 9) bot {res['bot_id']}: stake {res['stake_lamports']} lamports",
         f"closed: book {res['n_closed_book']}, current-rung population {res['n_population']}; open positions {res['n_open']}",
         f"CUSUM S {res['S']:.6f} (mu0 {cal['mu0']}, mu1 {cal['mu1']}, winsor [{cal['winsor_lo']}, {cal['winsor_hi']}]); warning at h_w {cal['h_w']}: {'FIRED' if res['warning'] else 'ok'}; alarm at h_a {cal['h_a']}: {'FIRED' if res['alarm'] else 'ok'}",
         f"ladder action: {la['action']}" + (f" to {la['to_stake']}" if la["to_stake"] else "") + f" ({la['reason']})"]
    if ret["n"]:
        L.append(f"retire check (raw basis): n {ret['n']} ({'armed' if ret['armed'] else 'not armed before 50'}), mean(x) {ret['mean_x']:+.6f}, mu_p {ret['mu_p']:+.6f}, n0 {ret['n0']}, m {ret['m']:+.6f}: {'RETIRE' if ret['fired'] else 'ok'}")
    else:
        L.append("retire check: no closed trades")
    L.append(f"per-book loss cap {res['loss_cap_lamports']} lamports, book realized {res['book_realized_lamports']}: {'HIT' if res['loss_cap_hit'] else 'ok'}")
    L.append("flags: " + (",".join(res["flags"]) or "none"))
    L.append("STOP / retire" if res["exit_code"] == 20 else "STEP-DOWN" if res["exit_code"] == 10 else "no flag")
    L.append(f"population start {res['population_start_ms']} ms; closes not in the population (other stake or before start): {res['n_closed_other_stake_or_before_start']}")
    L.append("not a registration, not a gate read, not a profit claim; brakes, not tests")
    return "\n".join(L)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rotation-lane decay monitor (read-only; DEC-036 section 9 item 9)")
    ap.add_argument("--bot", required=True)
    ap.add_argument("--ledger-file", type=Path, default=Path(RX.ROTATION_LEDGER_PATH))
    ap.add_argument("--calibration", type=Path, required=True)
    ap.add_argument("--since-ms", type=int)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        cal = load_calibration(a.calibration, a.bot)
        res = run(a.ledger_file, a.bot, cal, a.since_ms)
    except MonitorError as exc:
        print(f"rotation_decay_monitor: ERROR {exc}", file=sys.stderr)
        return 2
    print(json.dumps(res, indent=2, sort_keys=True) if a.json else render(res))
    if a.since_ms is not None and not a.json:
        for c in res["fired_at_close"]:
            print(f"fired_at_close ts={c['ts_ms']} flags={','.join(c['flags'])} action={c['action']} exit={c['exit_code']}")
        print(f"since-ms {a.since_ms}: closes with a flag {len(res['fired_at_close'])}; latest exit {res['exit_code_latest']}; worst exit {res['exit_code']}")
    return res["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
