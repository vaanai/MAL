"""slowbot paper position manager. PAPER only: no wallet, no send, no key.

A position is opened from a candidate dict (buy quote: quote_in_lamports of SOL -> quote_out_lamports of token, units as the
quote client returns them). Every 30 s each open position is re-priced with an injected quote_fn(mint, token_amount) that returns
a dict {"out_lamports": int, "liq_usd": float|None} for SELLING token_amount (a sell quote, never a mid price), or None when the
quote failed (the point is logged as a miss and the position stays open).

Exits (checked in this order each poll):
  1. liquidity collapse: liq_usd <= (1 - LIQ_COLLAPSE) * liq at entry          -> sell all
  2. stop: unit sell value <= (1 - STOP) * entry unit value, before the partial take -> sell all
  3. trail: after the partial take, unit value <= (1 - TRAIL) * high since take   -> sell the remainder
  4. time stop: age >= TIME_STOP_S                                              -> sell all
  5. take: unit value >= (1 + TAKE) * entry unit value, once                    -> sell half
Fills are the sell quote at the polling instant. The 30 s poll lag is NOT modelled beyond that (a stop can fill below -15%),
and the per-tx network fee is an assumption (FEE_LAMPORTS_PER_TX), charged on the entry and on every sell leg.

Persistence: state is one JSON file written tmp + fsync + rename + dir fsync; a restart re-reads it and resumes. Events
(open, price, leg, exit) are appended to JSONL and fsynced BEFORE the state write, so a crash between them can repeat an event
on restart but never lose one; readers dedupe legs by (position id, leg).
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

STAKE_LAMPORTS = 25_000_000  # 0.025 SOL notional
STAKE_TOLERANCE = 0.01
MAX_PER_ARM = 3
POLL_S = 30
STOP = 0.15
TAKE = 0.40
TAKE_FRACTION = 0.5
TRAIL = 0.20
TIME_STOP_S = 4 * 3600
LIQ_COLLAPSE = 0.40
FEE_LAMPORTS_PER_TX = 100_000  # assumption: 0.0001 SOL network + priority per tx
ARMS = ("T", "H", "R")


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(row, sort_keys=True) + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_json(path: Path, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps(obj, sort_keys=True).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


class PositionManager:
    def __init__(self, state_path, log_path, quote_fn, *, now_fn=time.time, max_per_arm: int = MAX_PER_ARM):
        self.state_path = Path(state_path)
        self.log_path = Path(log_path)
        self.quote_fn = quote_fn
        self.now_fn = now_fn
        self.max_per_arm = max_per_arm
        self.state = self._load()

    # -- state
    def _load(self) -> dict:
        try:
            raw = self.state_path.read_bytes()
        except FileNotFoundError:
            return {"open": {}, "closed": [], "seq": 0}
        st = json.loads(raw)
        if not isinstance(st, dict) or not {"open", "closed", "seq"} <= set(st):
            raise ValueError("slowbot position state file is malformed; refusing to start")
        return st

    def _save(self) -> None:
        atomic_write_json(self.state_path, self.state)

    def _log(self, kind: str, now: float, pos: dict, **kw) -> None:
        _append_jsonl(self.log_path, {"kind": kind, "ts_utc": _iso(now), "t": now, "id": pos["id"], "arm": pos["arm"],
                                      "mint": pos["mint"], **kw})

    # -- queries
    def open_positions(self, arm: str | None = None) -> list[dict]:
        return [p for p in self.state["open"].values() if arm is None or p["arm"] == arm]

    # -- open
    def open(self, candidate: dict, arm: str | None = None) -> tuple[dict | None, str]:
        """Open a paper position in `arm` (default candidate['arm']). Returns (position, reason); position is None on refusal."""
        arm = arm or candidate.get("arm")
        if arm not in ARMS:
            return None, "bad_arm"
        mint = candidate.get("mint")
        qin, qout = candidate.get("quote_in_lamports"), candidate.get("quote_out_lamports")
        if not mint or not isinstance(qin, int) or not isinstance(qout, int) or qin <= 0 or qout <= 0:
            return None, "bad_quote"
        if abs(qin - STAKE_LAMPORTS) > STAKE_LAMPORTS * STAKE_TOLERANCE:
            return None, "quote_not_stake_size"
        if len(self.open_positions(arm)) >= self.max_per_arm:
            return None, "arm_full"
        if any(p["mint"] == mint for p in self.open_positions(arm)):
            return None, "already_open"
        now = self.now_fn()
        self.state["seq"] += 1
        pid = f"{arm}-{self.state['seq']:06d}"
        pos = {"id": pid, "arm": arm, "mint": mint, "pool": candidate.get("pool"), "symbol": candidate.get("symbol"),
               "opened_t": now, "cost_lamports": qin, "tokens": qout, "tokens_left": qout,
               "entry_unit": qin / qout, "entry_liq_usd": candidate.get("liq_usd"), "taken": False, "high_ratio": None,
               "proceeds_lamports": 0, "fees_lamports": FEE_LAMPORTS_PER_TX, "legs": 0, "misses": 0}
        self._log("open", now, pos, cost_lamports=qin, tokens=qout, entry_liq_usd=pos["entry_liq_usd"],
                  fee_lamports=FEE_LAMPORTS_PER_TX)
        self.state["open"][pid] = pos
        self._save()
        return pos, "ok"

    # -- poll
    def tick(self) -> list[dict]:
        """Poll every open position once. Returns the list of exit rows produced."""
        now = self.now_fn()
        exits = []
        for pid in list(self.state["open"]):
            pos = self.state["open"][pid]
            q = self.quote_fn(pos["mint"], pos["tokens_left"])
            if not q or not isinstance(q.get("out_lamports"), int) or q["out_lamports"] < 0:
                pos["misses"] += 1
                self._log("price_miss", now, pos, misses=pos["misses"])
                self._save()
                # no quote, including at the time stop: keep trying next poll, never invent a fill
                continue
            ex = self._step(pos, now, q)
            if ex:
                exits.append(ex)
        return exits

    def _step(self, pos: dict, now: float, q: dict) -> dict | None:
        out, liq = q["out_lamports"], q.get("liq_usd")
        unit = out / pos["tokens_left"]
        ratio = unit / pos["entry_unit"]
        if pos["taken"]:
            pos["high_ratio"] = max(pos["high_ratio"], ratio)
        age = now - pos["opened_t"]
        self._log("price", now, pos, ratio=ratio, sell_out_lamports=out, tokens_left=pos["tokens_left"], liq_usd=liq,
                  age_s=age, high_ratio=pos["high_ratio"])
        reason = None
        e_liq = pos["entry_liq_usd"]
        if liq is not None and e_liq and liq <= (1 - LIQ_COLLAPSE) * e_liq:
            reason = "liq_collapse"
        elif not pos["taken"] and ratio <= 1 - STOP:
            reason = "stop"
        elif pos["taken"] and ratio <= (1 - TRAIL) * pos["high_ratio"]:
            reason = "trail"
        elif age >= TIME_STOP_S:
            reason = "time_stop"
        if reason:
            return self._exit(pos, now, out, reason)
        if not pos["taken"] and ratio >= 1 + TAKE:
            self._take_half(pos, now, ratio)
        else:
            self._save()
        return None

    def _take_half(self, pos: dict, now: float, ratio: float) -> None:
        sell = int(pos["tokens_left"] * TAKE_FRACTION)
        q = self.quote_fn(pos["mint"], sell)
        if not q or not isinstance(q.get("out_lamports"), int) or q["out_lamports"] < 0:
            self._log("price_miss", now, pos, misses=pos["misses"] + 1, where="take_half")
            return  # retry next poll; the take is not booked without a fill quote
        pos["proceeds_lamports"] += q["out_lamports"]
        pos["fees_lamports"] += FEE_LAMPORTS_PER_TX
        pos["tokens_left"] -= sell
        pos["taken"] = True
        pos["high_ratio"] = max(ratio, (q["out_lamports"] / sell) / pos["entry_unit"])
        pos["legs"] += 1
        self._log("leg", now, pos, leg="take_half", tokens_sold=sell, out_lamports=q["out_lamports"], ratio=ratio,
                  leg_no=pos["legs"])
        self._save()

    def _exit(self, pos: dict, now: float, out: int, reason: str) -> dict:
        pos["proceeds_lamports"] += out
        pos["fees_lamports"] += FEE_LAMPORTS_PER_TX
        pos["legs"] += 1
        net = pos["proceeds_lamports"] - pos["cost_lamports"] - pos["fees_lamports"]
        row = {"kind": "exit", "ts_utc": _iso(now), "t": now, "id": pos["id"], "arm": pos["arm"], "mint": pos["mint"],
               "reason": reason, "opened_t": pos["opened_t"], "hold_s": now - pos["opened_t"],
               "cost_lamports": pos["cost_lamports"], "proceeds_lamports": pos["proceeds_lamports"],
               "fees_lamports": pos["fees_lamports"], "net_lamports": net, "net_sol": net / 1e9,
               "took_half": pos["taken"], "leg_no": pos["legs"]}
        _append_jsonl(self.log_path, row)
        del self.state["open"][pos["id"]]
        self.state["closed"].append({k: row[k] for k in ("id", "arm", "mint", "reason", "ts_utc", "net_lamports")})
        self._save()
        return row

    def run(self, *, iterations: int | None = None, sleep=time.sleep) -> None:
        n = 0
        while iterations is None or n < iterations:
            self.tick()
            n += 1
            if iterations is None or n < iterations:
                sleep(POLL_S)
