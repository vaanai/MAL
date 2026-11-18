"""arena_oq_engine: paper pricing engine and host fence for `l1-floor-oq-v1` (tools/arena_rule_l1_oq.py). PAPER only. No key, no network. Nothing here says any book makes money.

Opt-in: nothing imports this module except scripts/research/osb_follow_oq.py and its tests; tools/arena_rules.py, arena_paper.py and every WSOL path are untouched.

OQHost  = HostV2 whose buy fence admits the universe "pumpswap_other_quote": a PumpSwap pool whose quote mint is KNOWN and not WSOL (a WSOL, None or empty quote is refused).
          It swaps `arena_rules.in_universe` for the duration of one `_buy` call only (single-threaded), because that pinned file is not edited; every other universe goes to the
          original function.
OQEngine = the engine surface the host uses, pricing in the pool's quote units with the declared conversion legs (formulas in arena_rule_l1_oq.py):
  * entry: priced at the FIRST print of the pool at or after decision_ms + lat_entry_s (default 1.9 s, the rule's LEG_MS; a latency twin passes 3.010 / 4.085 / 5.590), from that print's
    PRE-trade reserves (V included in Q) and the pool's own fee (the table's `pfee`), converted through the quote's own WSOL pool state AT THAT PRINT (OQTable.quote_state);
  * exit: priced at the first print at or after the exit decision + EXIT_LAG_MS (0.55 s, arena_stack.EXIT_LAG_S), same state sources, reverse trip;
  * a landing without a resolvable quote state or pool fee is REFUSED (entry: a `refused` row and no position; exit: the position waits for the next print that has them);
  * P&L per stake = SOL out - SOL in - 2 x send_lamports. NOT modelled: priority fee beyond `send_lamports`, ATA rent, fee-tier changes, strands, gap exclusion (the lane host's
    GapRangeEngine does those for WSOL books). A position with no later print stays `pending` and is counted in `status()`.
Ledger rows (JSONL, deterministic: no wall clock, no pid): kind `open` (one per stake), `trade` (one per stake, at the exit fill), `refused`. decisions_md5_from_rows of osb_follow reads
open/trade rows by `position_id` and `decision_ms.{entry,exit}`.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from tools import arena_rules as R
from tools import arena_rule_l1_oq as O
from tools.arena_rules import position_id
from tools.arena_rules_v2 import HostV2

EXIT_LAG_MS = 550
OQ_UNIVERSE = "pumpswap_other_quote"


def oq_in_universe(pool: Any) -> bool:
    qm = getattr(pool, "quote_mint", None)
    return pool is not None and pool.prog == "pumpswap" and bool(qm) and qm != O.WSOL


class OQHost(HostV2):
    def _buy(self, rule: Any, book: Any, o: Any, dec_ms: int) -> None:
        if getattr(rule, "universe", None) != OQ_UNIVERSE:
            return super()._buy(rule, book, o, dec_ms)
        orig = R.in_universe
        R.in_universe = lambda u, p: oq_in_universe(p) if u == OQ_UNIVERSE else orig(u, p)
        try:
            super()._buy(rule, book, o, dec_ms)
        finally:
            R.in_universe = orig


def _jline(r: Mapping[str, Any]) -> str:
    return json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n"


class OQEngine:
    def __init__(self, ledger_root: str | Path, stakes_sol: tuple[float, ...], send_lamports: int, lat_entry_s: float = O.LEG_MS / 1000.0) -> None:
        self.root = Path(ledger_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "oq-rows.jsonl"
        self.stakes, self.send, self.lat_ms = tuple(stakes_sol), int(send_lamports), int(round(lat_entry_s * 1000))
        self.table: O.OQTable | None = None  # set by the driver after the host is built (the host owns the table)
        self.positions: dict[str, Any] = {}
        self.now_fn = None
        self.counters: dict[str, int] = {}
        self.pending: dict[str, dict[str, Any]] = {}

    # surface used by the host
    def recover(self) -> int:
        return 0

    def start_record(self, bot_id: str) -> bool:
        return False

    def trim_history(self) -> None:
        return None

    def _w(self, r: Mapping[str, Any]) -> None:
        with open(self.path, "a") as fh:
            fh.write(_jline(r))

    def _c(self, k: str) -> None:
        self.counters[k] = self.counters.get(k, 0) + 1

    def buy(self, bot_id: str, pool: str, sol_in: Any, decision_ms: int, decision_slot: int, meta: Mapping[str, Any] | None = None) -> str:
        pid = position_id(bot_id, pool, decision_ms, decision_slot)
        m = dict(meta or {})
        self.positions[pid] = SimpleNamespace(bot_id=bot_id, pool=pool, meta=m, decision_ms=decision_ms, decision_slot=decision_slot)
        self.pending[pid] = {"state": "entry", "pool": pool, "qm": m.get("quote_mint"), "t_dec": int(decision_ms), "due": int(decision_ms) + self.lat_ms}
        return pid

    def sell(self, bot_id: str, pid: str, frac: float, decision_ms: int, decision_slot: int, reason: str = "") -> None:
        p = self.pending.get(pid)
        if pid not in self.positions or p is None:
            raise R.ArenaRefused(f"no open position {pid} for bot {bot_id}")
        if p["state"] == "exit":
            raise R.ArenaRefused(f"sell already pending for {pid}")
        p.update(state="exit", due=int(decision_ms) + EXIT_LAG_MS, exit_dec=int(decision_ms), reason=reason)

    def on_row(self, row: Mapping[str, Any]) -> None:
        pool, t = row.get("pool"), row.get("t_recv_ms")
        if not pool or not isinstance(t, int) or row.get("type") != "trade" or self.table is None:
            return
        for pid in [k for k, p in self.pending.items() if p["pool"] == pool and p["state"] in ("entry", "exit") and t >= p["due"]]:
            p = self.pending[pid]
            if p["state"] == "entry":
                self._fill_entry(pid, p, row, t)
            else:
                self._fill_exit(pid, p, row, t)

    def _states(self, p: Mapping[str, Any], row: Mapping[str, Any], t: int):
        qs, why = self.table.quote_state(p["qm"], t)
        fee = self.table.pfee.get(row["pool"])
        v = row.get("virtual_quote_reserves")
        if qs is None:
            return None, why
        if fee is None:
            return None, "no_pool_fee"
        try:
            ps = {"Qe": float(row["quote_reserve"]) + float(v), "Be": float(row["base_reserve"]), "fee": fee}
        except (KeyError, TypeError, ValueError):
            return None, "bad_pool_row"
        return (qs, ps), None

    def _fill_entry(self, pid: str, p: dict, row: Mapping[str, Any], t: int) -> None:
        got, why = self._states(p, row, t)
        if got is None:
            self._w({"kind": "refused", "position_id": pid, "side": "buy", "reason": why, "t_ms": t})
            self._c("entry_refused_" + str(why))
            del self.pending[pid]
            del self.positions[pid]
            return
        qs, ps = got
        legs = {}
        for s in self.stakes:
            lam = s * 1e9
            e = O.price_entry(lam, qs, ps)
            legs[s] = e
            self._w({"kind": "open", "position_id": pid, "stake_sol": s, "decision_ms": {"entry": p["t_dec"], "exit": None}, "fill_ms": t, "tokens": e["tokens"],
                     "quote_units": e["quote_units"], "conv_cost_lamports": e["conv_cost_lamports"], "quote_mint": p["qm"], "pool": p["pool"]})
        p.update(state="open", legs=legs)
        self._c("entries_filled")

    def _fill_exit(self, pid: str, p: dict, row: Mapping[str, Any], t: int) -> None:
        got, why = self._states(p, row, t)
        if got is None:
            self._c("exit_waiting_" + str(why))
            return  # wait for a later print with a resolvable quote state and pool fee
        qs, ps = got
        for s, e in p["legs"].items():
            x = O.price_exit(e["tokens"], qs, ps)
            pnl = O.trip_pnl(s * 1e9, e, x) - 2 * self.send
            self._w({"kind": "trade", "position_id": pid, "stake_sol": s, "decision_ms": {"entry": p["t_dec"], "exit": p["exit_dec"]}, "fill_ms": {"exit": t}, "reason": p["reason"],
                     "pnl_lamports": pnl, "entry_conv_cost_lamports": e["conv_cost_lamports"], "exit_conv_cost_lamports": x["conv_cost_lamports"], "sol_out_lamports": x["sol_lamports"],
                     "send_lamports_total": 2 * self.send, "quote_mint": p["qm"], "pool": p["pool"]})
        del self.pending[pid]
        del self.positions[pid]
        self._c("exits_filled")

    def status(self) -> dict[str, Any]:
        return {"pending": {k: sum(1 for p in self.pending.values() if p["state"] == k) for k in ("entry", "exit", "open")}, **self.counters}
