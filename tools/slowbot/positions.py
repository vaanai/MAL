"""slowbot paper position manager. PAPER only: no wallet, no send, no key.

Paired design: one candidate can open a position in several arms at once (candidate["arms"] list or candidate["arm"] str); each arm
has its own entry quote, its own position, its own cap (MAX_PER_ARM) and its own results.

Entry: the fill is a buy quote taken AFTER the decision. Preferred: an injected buy_quote_fn(mint, lamports, arm) returning
{"in_lamports", "out_lamports", "liq_usd"} (called once per arm). Fallback: the candidate's own quote, accepted only if
candidate["quote_ts"] is at most MAX_QUOTE_AGE_S old (else refused "stale_quote"). quote_age_s is logged on the open row.

Pricing: quote_fn(mint, token_amount) returns {"out_lamports": int, "liq_usd": float|None} for SELLING token_amount (a sell quote,
never a mid), or None on failure; it may raise (caught per position per poll, counted as a miss).

Exits (checked in order each 30 s poll):
  1. liquidity collapse: liq_usd <= (1 - LIQ_COLLAPSE) * entry liq
  2. liq_unknown_exit: liq_usd unknown (None) on LIQ_UNKNOWN_MAX polls in a row; each one logs a liq_unknown event
  3. stop: unit sell value <= (1 - STOP) * entry unit value, before the partial take
  4. trail: after the partial take, unit value <= (1 - TRAIL) * high since take
  5. time stop: age >= TIME_STOP_S
  6. take: unit value >= (1 + TAKE) * entry unit value, once, sells half
  7. no_quote_writeoff: no good sell quote for NO_QUOTE_WRITEOFF_S -> the remainder is booked at 0 proceeds (a rug)
Exit fills: after a trigger a fresh quote is taken for the fill; if that fails the trigger quote less FALLBACK_HAIRCUT is used.
Both are logged (trigger_out_lamports, fill_out_lamports, fill_basis). The 30 s poll lag is still not modelled.

Costs (assumptions, untested against real fills): FEE_LAMPORTS_PER_TX + PRIORITY_LAMPORTS_PER_TX on the entry and on every sell
leg; ATA_RENT_LAMPORTS charged at entry as a cost (no close refund modelled; ata_closed_assumed false). 0% tx-fail assumed.

Persistence: state is one JSON file (tmp + fsync + rename + dir fsync); a restart resumes. Events are fsynced to JSONL BEFORE the
state write, so a crash can repeat an event but never lose one; readers dedupe exits by position id.
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
LIQ_UNKNOWN_MAX = 3
NO_QUOTE_WRITEOFF_S = 600
FALLBACK_HAIRCUT = 0.01
MAX_QUOTE_AGE_S = 5.0
FEE_LAMPORTS_PER_TX = 100_000
PRIORITY_LAMPORTS_PER_TX = 200_000
TX_COST_LAMPORTS = FEE_LAMPORTS_PER_TX + PRIORITY_LAMPORTS_PER_TX
ATA_RENT_LAMPORTS = 2_039_280
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


def _good(q) -> bool:
    return isinstance(q, dict) and isinstance(q.get("out_lamports"), int) and not isinstance(q["out_lamports"], bool) \
        and q["out_lamports"] >= 0


class PositionManager:
    def __init__(self, state_path, log_path, quote_fn, *, buy_quote_fn=None, now_fn=time.time, max_per_arm: int = MAX_PER_ARM):
        self.state_path = Path(state_path)
        self.log_path = Path(log_path)
        self.quote_fn = quote_fn
        self.buy_quote_fn = buy_quote_fn
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

    def _quote(self, mint, amount):
        """quote_fn that never raises: returns (quote_or_None, error_class_or_None)."""
        try:
            q = self.quote_fn(mint, amount)
        except Exception as e:
            return None, type(e).__name__
        return (q if _good(q) else None), None

    # -- queries
    def open_positions(self, arm: str | None = None) -> list[dict]:
        return [p for p in self.state["open"].values() if arm is None or p["arm"] == arm]

    # -- open
    def _refuse(self, arm, candidate, reason, now, **kw) -> tuple[None, str]:
        _append_jsonl(self.log_path, {"kind": "refused", "ts_utc": _iso(now), "t": now, "arm": arm, "mint": candidate.get("mint"),
                                      "reason": reason, **kw})
        return None, reason

    def open_all(self, candidate: dict) -> dict:
        """Paired design: open the candidate in every arm it lists ("arms" list, else "arm"). Returns {arm: (position, reason)}."""
        arms = candidate.get("arms")
        if arms is None:
            arms = [candidate.get("arm")]
        return {a: self.open(candidate, a) for a in arms}

    def open(self, candidate: dict, arm: str | None = None) -> tuple[dict | None, str]:
        """Open a paper position in `arm`. Returns (position, reason); position is None on refusal (every refusal is logged)."""
        now = self.now_fn()
        arm = arm or candidate.get("arm")
        mint = candidate.get("mint")
        if arm not in ARMS:
            return self._refuse(arm, candidate, "bad_arm", now)
        if not mint:
            return self._refuse(arm, candidate, "no_mint", now)
        if len(self.open_positions(arm)) >= self.max_per_arm:
            return self._refuse(arm, candidate, "arm_full", now)
        if any(p["mint"] == mint for p in self.open_positions(arm)):
            return self._refuse(arm, candidate, "already_open", now)
        quote_age = None
        if self.buy_quote_fn is not None:
            try:
                bq = self.buy_quote_fn(mint, STAKE_LAMPORTS, arm)
            except Exception as e:
                return self._refuse(arm, candidate, "no_entry_quote", now, error_class=type(e).__name__)
            if not isinstance(bq, dict):
                return self._refuse(arm, candidate, "no_entry_quote", now)
            qin, qout, liq = bq.get("in_lamports"), bq.get("out_lamports"), bq.get("liq_usd")
            if liq is None:
                liq = candidate.get("liq_usd")
            quote_age = 0.0
        else:
            qin, qout, liq = candidate.get("quote_in_lamports"), candidate.get("quote_out_lamports"), candidate.get("liq_usd")
            qts = candidate.get("quote_ts")
            if not isinstance(qts, (int, float)):
                return self._refuse(arm, candidate, "stale_quote", now, detail="no_quote_ts")
            quote_age = now - qts
            if quote_age > MAX_QUOTE_AGE_S or quote_age < 0:
                return self._refuse(arm, candidate, "stale_quote", now, quote_age_s=quote_age)
        if not isinstance(qin, int) or not isinstance(qout, int) or isinstance(qin, bool) or qin <= 0 or qout <= 0:
            return self._refuse(arm, candidate, "bad_quote", now)
        if abs(qin - STAKE_LAMPORTS) > STAKE_LAMPORTS * STAKE_TOLERANCE:
            return self._refuse(arm, candidate, "quote_not_stake_size", now)
        if not isinstance(liq, (int, float)) or liq <= 0:
            return self._refuse(arm, candidate, "no_entry_liq", now)
        self.state["seq"] += 1
        pid = f"{arm}-{self.state['seq']:06d}"
        entry_fees = TX_COST_LAMPORTS + ATA_RENT_LAMPORTS
        pos = {"id": pid, "arm": arm, "mint": mint, "pool": candidate.get("pool"), "symbol": candidate.get("symbol"),
               "opened_t": now, "cost_lamports": qin, "tokens": qout, "tokens_left": qout,
               "entry_unit": qin / qout, "entry_liq_usd": liq, "taken": False, "high_ratio": None,
               "proceeds_lamports": 0, "fees_lamports": entry_fees, "legs": 0, "misses": 0, "miss_since_t": None,
               "liq_unknown_n": 0, "last_ratio": None, "last_value_lamports": None}
        self._log("open", now, pos, cost_lamports=qin, tokens=qout, entry_liq_usd=liq, quote_age_s=quote_age,
                  fee_lamports=TX_COST_LAMPORTS, ata_rent_lamports=ATA_RENT_LAMPORTS, ata_closed_assumed=False,
                  fee_assumption={"fee": FEE_LAMPORTS_PER_TX, "priority": PRIORITY_LAMPORTS_PER_TX}, tx_fail_assumed=0.0)
        self.state["open"][pid] = pos
        self._save()
        return pos, "ok"

    # -- poll
    def tick(self) -> list[dict]:
        """Poll every open position once (never raises on a quote error). Returns the exit rows produced."""
        exits = []
        for pid in list(self.state["open"]):
            pos = self.state["open"][pid]
            q, err = self._quote(pos["mint"], pos["tokens_left"])
            now = self.now_fn()  # after the (slow) quote call
            if q is None:
                ex = self._miss(pos, now, err)
            else:
                pos["miss_since_t"] = None  # a good quote ends the miss streak (misses stays a lifetime count)
                ex = self._step(pos, now, q)
            if ex:
                exits.append(ex)
        return exits

    def _miss(self, pos: dict, now: float, err, where: str = "poll") -> dict | None:
        pos["misses"] += 1
        if pos["miss_since_t"] is None:
            pos["miss_since_t"] = now
        self._log("price_miss", now, pos, misses=pos["misses"], error_class=err, where=where,
                  miss_age_s=now - pos["miss_since_t"], proceeds_lamports=pos["proceeds_lamports"],
                  fees_lamports=pos["fees_lamports"])
        if now - pos["miss_since_t"] >= NO_QUOTE_WRITEOFF_S:
            return self._exit(pos, now, 0, "no_quote_writeoff", trigger_out=0, fill_basis="writeoff")
        self._save()
        return None

    def _fresh_fill(self, pos: dict, amount: int, trigger_out: int) -> tuple[int, str]:
        q, _ = self._quote(pos["mint"], amount)
        if q is not None:
            return q["out_lamports"], "requote"
        return int(trigger_out * (1 - FALLBACK_HAIRCUT)), "trigger_less_1pct"

    def _step(self, pos: dict, now: float, q: dict) -> dict | None:
        out, liq = q["out_lamports"], q.get("liq_usd")
        unit = out / pos["tokens_left"]
        ratio = unit / pos["entry_unit"]
        pos["last_ratio"], pos["last_value_lamports"] = ratio, out
        if pos["taken"]:
            pos["high_ratio"] = max(pos["high_ratio"], ratio)
        age = now - pos["opened_t"]
        if liq is None:
            pos["liq_unknown_n"] += 1
            self._log("liq_unknown", now, pos, consecutive=pos["liq_unknown_n"])
        else:
            pos["liq_unknown_n"] = 0
        self._log("price", now, pos, ratio=ratio, sell_out_lamports=out, tokens_left=pos["tokens_left"], liq_usd=liq,
                  age_s=age, high_ratio=pos["high_ratio"], proceeds_lamports=pos["proceeds_lamports"],
                  fees_lamports=pos["fees_lamports"])
        reason = None
        e_liq = pos["entry_liq_usd"]
        if liq is not None and e_liq and liq <= (1 - LIQ_COLLAPSE) * e_liq:
            reason = "liq_collapse"
        elif pos["liq_unknown_n"] >= LIQ_UNKNOWN_MAX:
            reason = "liq_unknown_exit"
        elif not pos["taken"] and ratio <= 1 - STOP:
            reason = "stop"
        elif pos["taken"] and ratio <= (1 - TRAIL) * pos["high_ratio"]:
            reason = "trail"
        elif age >= TIME_STOP_S:
            reason = "time_stop"
        if reason:
            fill, basis = self._fresh_fill(pos, pos["tokens_left"], out)
            return self._exit(pos, self.now_fn(), fill, reason, trigger_out=out, fill_basis=basis, trigger_ratio=ratio)
        if not pos["taken"] and ratio >= 1 + TAKE:
            self._take_half(pos, now, ratio, out)
        else:
            self._save()
        return None

    def _take_half(self, pos: dict, now: float, ratio: float, trigger_out: int) -> None:
        sell = int(pos["tokens_left"] * TAKE_FRACTION)
        q, err = self._quote(pos["mint"], sell)
        if q is not None:
            fill, basis = q["out_lamports"], "requote"
        else:
            fill, basis = int(trigger_out * sell / pos["tokens_left"] * (1 - FALLBACK_HAIRCUT)), "trigger_less_1pct"
        pos["proceeds_lamports"] += fill
        pos["fees_lamports"] += TX_COST_LAMPORTS
        pos["tokens_left"] -= sell
        pos["taken"] = True
        pos["high_ratio"] = max(ratio, (fill / sell) / pos["entry_unit"])
        pos["legs"] += 1
        self._log("leg", now, pos, leg="take_half", tokens_sold=sell, trigger_ratio=ratio, fill_out_lamports=fill,
                  fill_ratio=(fill / sell) / pos["entry_unit"], fill_basis=basis, leg_no=pos["legs"])
        self._save()

    def _exit(self, pos: dict, now: float, fill: int, reason: str, *, trigger_out: int, fill_basis: str,
              trigger_ratio=None) -> dict:
        pos["proceeds_lamports"] += fill
        pos["fees_lamports"] += TX_COST_LAMPORTS
        pos["legs"] += 1
        net = pos["proceeds_lamports"] - pos["cost_lamports"] - pos["fees_lamports"]
        row = {"kind": "exit", "ts_utc": _iso(now), "t": now, "id": pos["id"], "arm": pos["arm"], "mint": pos["mint"],
               "reason": reason, "opened_t": pos["opened_t"], "hold_s": now - pos["opened_t"],
               "cost_lamports": pos["cost_lamports"], "proceeds_lamports": pos["proceeds_lamports"],
               "fees_lamports": pos["fees_lamports"], "net_lamports": net, "net_sol": net / 1e9,
               "trigger_out_lamports": trigger_out, "fill_out_lamports": fill, "fill_basis": fill_basis,
               "trigger_ratio": trigger_ratio, "took_half": pos["taken"], "leg_no": pos["legs"]}
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
