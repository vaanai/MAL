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
  * P&L per stake (filled, no fail blend) = SOL out - SOL in - N_SENDS x send_lamports; N_SENDS = 4 (two token txs and two quote-conversion txs per trip; the WSOL book pays 2).
    The fail models (flat 15% and pressure, the WSOL per-trip blend; no stranded-quote model) are the READER's, as in the WSOL book: tools/arena_oq_score.py over these rows. The rows carry the pressure inputs
    (`ssb`, `nearby_lamports`, from the pool's buys at the landing print, same-slot count and buy SOL over the 2 s before it).
  * HONESTY RULES (declared): (1) an entry that never lands in 24 h is a ROUND TRIP at the post-trigger state (both conversion legs, fees, sends): a `trade` row with unfilled_roundtrip=true,
    never dropped; (2) the entry latency is 4.085 s (the deciding cell), twins 3.010 / 5.590 (tools.arena_rule_l1_oq.LATENCY_VALUES_S), a pricing-only change: twins share the ENTRY decisions, but an exit decision_ms may differ by design (a sell decided before a twin's entry lands is refused SELL_EARLY and retried by the host); the
    conversion leg is read at the same landing print (its own earlier landing is NOT modelled); (3) an exit that finds no usable quote state WAITS at most EXIT_WAIT_CAP_MS after its decision, then
    is SCORED (`stale_exit_capped`): the last known pool and quote states priced, proceeds x (1 - STALE_EXIT_HAIRCUT), or 0 SOL out when there is no last state; never left unscored;
    (4) feed gaps (a `gap` row with data_loss / process_restart / feed_restart / feed_start, a `start` row, or silence between trade rows > GAP_SILENCE_MS) exclude a trade only on a LEG window, as
    the WSOL GapRangeEngine does by default: the entry window (entry decision .. landing print) or the exit window (exit decision .. exit fill); `excluded` row, counted, never priced. A gap
    elsewhere in the hold does NOT exclude (that would drop long holds, mostly time exits, more than quick take profits): the trade is priced at the first fresh print after it and flagged
    gap_in_hold (reported by exit reason); (5) ATA rent is report-only (`rent_report_lamports`, as RENT_REPORT in the WSOL stack; the deciding stack refunds it) (no rent is charged on a stuck quote leg: the quote
    conversion is not modelled as failing, declared on the method card); (6) buyback_fee is UNMODELLED (absent from the stored tape row) and a missing creator_fee counts as 0: both counted in `status()` under `fee_audit`.
    NOT modelled: priority fee beyond `send_lamports`, fee-tier changes between the quote leg and the token leg. A position with no later print stays `pending` and is counted in `status()`.
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
N_SENDS = 4  # sends per trip: quote buy, token buy, token sell, quote sell
GAP_SILENCE_MS = 60_000  # silence between consecutive trade rows beyond this is a data_loss gap (arena_paper.StackConfig.gap_silence_ms)
HARD_GAP_KINDS = ("process_restart", "feed_restart", "feed_start")
EXIT_WAIT_CAP_MS = 3_600_000  # declared: an exit without a usable quote state waits at most 1 h after its decision, then it is scored
STALE_EXIT_HAIRCUT = 0.5  # declared: capped exits are scored at the last known states with proceeds x (1 - 0.5)
NEARBY_MS = 2_000  # pressure window (arena_stack.NEARBY_WINDOW_S)
RENT_REPORT_LAMPORTS = 2_039_280  # per ATA; the token ATA and the quote ATA are two accounts (report-only leg, arena_stack.RENT_REPORT)
OQ_UNIVERSE = "pumpswap_other_quote"


def oq_in_universe(pool: Any) -> bool:
    qm = getattr(pool, "quote_mint", None)
    return pool is not None and pool.prog == "pumpswap" and bool(qm) and qm != O.WSOL


class OQHost(HostV2):
    def _on_row(self, row: dict) -> None:
        super()._on_row(row)
        t = row.get("t_recv_ms")
        if isinstance(t, int) and not isinstance(t, bool):
            self.engine.expire(t)
        ref, self.engine.refused = getattr(self.engine, "refused", []), []
        for bot, pid, why, t in ref:  # an entry the engine refused at its landing print leaves no position: drop it from the book (else it blocks the pool ~24 h)
            book = self.books.get(bot)
            if book is not None and pid in book.open:
                self._refused(bot, t, pid, "buy", R.ArenaRefused(why), reason="entry_refused_at_landing:" + why)
                book.drop(pid)
                self._count("entries_refused_at_landing")

    def __init__(self, cfg: Any, rules: Any, state: Any, engine: Any, **kw: Any) -> None:
        kw.setdefault("table", O.OQTable())  # HostV2 builds a PoolTable otherwise (tools/arena_rules_v2.py is pinned by the lane host and is not edited)
        super().__init__(cfg, rules, state, engine, **kw)

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
    def __init__(self, ledger_root: str | Path, stakes_sol: tuple[float, ...], send_lamports: int, lat_entry_s: float = O.LAT_PRICING_DECIDING_S) -> None:
        self.root = Path(ledger_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "oq-rows.jsonl"
        self.stakes, self.send, self.lat_ms = tuple(stakes_sol), int(send_lamports), int(round(lat_entry_s * 1000))
        self.table: O.OQTable | None = None  # set by the driver after the host is built (the host owns the table)
        self.positions: dict[str, Any] = {}
        self.now_fn = None
        self.counters: dict[str, int] = {}
        self.pending: dict[str, dict[str, Any]] = {}
        self.gaps: list[tuple[int, int]] = []
        self.last_t: int | None = None
        self._status_hour = -1
        self.last_state: dict[str, dict[str, Any]] = {}  # pool -> its latest print (post-trade reserves, quote mint, creator), for the post-trigger state of an entry
        self.hist: dict[str, list[tuple[int, Any, str, float]]] = {}  # pool -> recent prints (t, slot, side, sol_lamports) of pools with a pending position, for the pressure inputs
        self.refused: list[tuple[str, str, str, int]] = []  # (bot_id, position_id, reason, t_ms) entries refused at landing, drained by the host

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
        ls = self.last_state.get(pool)  # the trigger print itself (the host feeds the engine before the rule): the post-trigger state an unfilled entry is scored at
        qs0 = None
        if self.table is not None and m.get("quote_mint"):
            qs0, _ = self.table.quote_state(m["quote_mint"], int(decision_ms))
            qs0 = dict(qs0) if qs0 is not None else None
        self.positions[pid] = SimpleNamespace(bot_id=bot_id, pool=pool, meta=m, decision_ms=decision_ms, decision_slot=decision_slot)
        self.pending[pid] = {"state": "entry", "pool": pool, "qm": m.get("quote_mint"), "t_dec": int(decision_ms), "due": int(decision_ms) + self.lat_ms,
                             "ps0": dict(ls["ps"]) if ls and ls.get("ps") else None, "qs0": qs0, "creator": (ls or {}).get("creator")}
        return pid

    def sell(self, bot_id: str, pid: str, frac: float, decision_ms: int, decision_slot: int, reason: str = "") -> None:
        p = self.pending.get(pid)
        if pid not in self.positions or p is None:
            raise R.ArenaRefused(f"no open position {pid} for bot {bot_id}")
        if p["state"] == "exit":
            raise R.ArenaRefused(f"{pid} already has a sell")
        if p["state"] == "entry":  # the entry has not landed: no legs to sell
            if int(decision_ms) - p["t_dec"] >= O.HOLD_MS:  # the time exit: the entry never landed in 24 h: a ROUND TRIP at the post-trigger state, scored (never dropped)
                self._score_unfilled(pid, p, int(decision_ms))
                del self.pending[pid]
                del self.positions[pid]
                return
            raise R.ArenaRefused(f"{R.SELL_EARLY}: entry of {pid} has not filled")
        p.update(state="exit", due=int(decision_ms) + EXIT_LAG_MS, exit_dec=int(decision_ms), reason=reason, cap=int(decision_ms) + EXIT_WAIT_CAP_MS)

    # gaps and expiry
    def _gap_between(self, lo: int, hi: int) -> bool:
        return any(a <= hi and b >= lo for a, b in self.gaps)

    def _note_gap(self, lo: int, hi: int) -> None:
        self.gaps.append((int(lo), int(hi)))
        if len(self.gaps) > 256:  # keep only gaps that can still overlap an open position
            floor = min([p["t_dec"] for p in self.pending.values()] or [hi])
            self.gaps = [g for g in self.gaps if g[1] >= floor]

    def expire(self, now_ms: int) -> None:
        """Called by the host on every row: an exit whose wait passed EXIT_WAIT_CAP_MS is scored now (stale_exit_capped)."""
        for pid in [k for k, p in self.pending.items() if p["state"] == "exit" and now_ms >= p["cap"]]:
            self._stale_exit(pid, self.pending[pid], now_ms)
        h = now_ms // 3_600_000
        if h != self._status_hour:  # status.json next to the ledger once an hour: the scorer's CLI needs pending counts and the fee audit
            self._status_hour = h
            (self.root / "status.json").write_text(json.dumps(self.status(), sort_keys=True, default=float))

    def on_row(self, row: Mapping[str, Any]) -> None:
        t, typ = row.get("t_recv_ms"), row.get("type")
        if isinstance(t, bool) or not isinstance(t, int):
            return
        if typ == "start" or (typ == "gap" and (row.get("data_loss") or row.get("kind") in HARD_GAP_KINDS)):
            lo = self.last_t if self.last_t is not None else t
            self._note_gap(lo, t)
            self._c("gap_rows")
            return
        pool = row.get("pool")
        if not pool or typ != "trade" or self.table is None:
            return
        if self.last_t is not None and t - self.last_t > GAP_SILENCE_MS:
            self._note_gap(self.last_t, t)
            self._c("gap_silence")
        self.last_t = t
        post = self._post(row)
        if row.get("quote_mint") and row.get("quote_mint") != O.WSOL:
            self.last_state[pool] = {"ps": post, "qm": row.get("quote_mint"), "creator": row.get("creator") or row.get("coin_creator"), "t": t}
        mine = [k for k, p in self.pending.items() if p["pool"] == pool]
        if not mine:
            return
        h = self.hist.setdefault(pool, [])
        try:
            h.append((t, row.get("slot"), row.get("side"), float(row["sol_lamports"])))
        except (KeyError, TypeError, ValueError):
            pass
        while h and h[0][0] < t - NEARBY_MS:
            h.pop(0)
        for pid in mine:
            p = self.pending[pid]
            if post is not None:
                p["last_ps"] = post
            if t >= p["due"] and p["state"] in ("entry", "exit"):
                if p["state"] == "entry":
                    self._fill_entry(pid, p, row, t)
                else:
                    self._fill_exit(pid, p, row, t)
        if not any(p["pool"] == pool for p in self.pending.values()):
            self.hist.pop(pool, None)

    @staticmethod
    def _post(row: Mapping[str, Any]) -> dict[str, float] | None:
        """post-trade pool state in the pricing units (V included in Q), the 1.25% approximation of spot_after; the fee is added by the caller"""
        try:
            sol, tok, q, b, v = (float(row[k]) for k in ("sol_lamports", "token_raw", "quote_reserve", "base_reserve", "virtual_quote_reserves"))
            buy = row["side"] == "buy"
        except (KeyError, TypeError, ValueError):
            return None
        qp = q + (sol * (1 - 0.0125) if buy else -sol / (1 - 0.0125))
        bp = b - tok if buy else b + tok
        return {"Qe": qp + v, "Be": bp} if qp + v > 0 and bp > 0 else None

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

    def _pressure(self, row: Mapping[str, Any], t: int, qs: Mapping[str, Any]) -> tuple[float, float]:
        """(ssb, nearby SOL lamports) at the landing print: buys in the print's slot, and buy volume over the previous NEARBY_MS (quote units x the quote's mid)"""
        h = self.hist.get(row["pool"], [])
        slot = row.get("slot")
        ssb = float(sum(1 for (_, sl, sd, _) in h if sd == "buy" and sl == slot))
        return ssb, float(sum(x for (_, _, sd, x) in h if sd == "buy")) * qs["Qw"] / qs["Bw"]

    def _fill_entry(self, pid: str, p: dict, row: Mapping[str, Any], t: int) -> None:
        if self._gap_between(p["t_dec"], t):
            return self._drop_entry(pid, "feed_gap_entry", t, excluded=True)
        got, why = self._states(p, row, t)
        if got is None:
            return self._drop_entry(pid, str(why), t)
        qs, ps = got
        ssb, nearby = self._pressure(row, t, qs)
        legs = {}
        for s in self.stakes:
            lam = s * 1e9
            e = O.price_entry(lam, qs, ps)
            legs[s] = e
            self._w({"kind": "open", "position_id": pid, "stake_sol": s, "decision_ms": {"entry": p["t_dec"], "exit": None}, "fill_ms": t, "tokens": e["tokens"],
                     "quote_units": e["quote_units"], "conv_cost_lamports": e["conv_cost_lamports"], "quote_mint": p["qm"], "pool": p["pool"], "ssb": ssb,
                     "nearby_lamports": nearby, "lat_entry_s": self.lat_ms / 1000.0})
        p.update(state="open", legs=legs, ssb=ssb, nearby=nearby)
        self._c("entries_filled")

    def _drop_entry(self, pid: str, why: str, t: int, excluded: bool = False) -> None:
        self._w({"kind": "excluded" if excluded else "refused", "position_id": pid, "side": "buy", "reason": why, "t_ms": t})
        self._c(("excluded_" if excluded else "entry_refused_") + why)
        self.refused.append((self.positions[pid].bot_id, pid, why, t))  # the host drops its book entry (OQHost._on_row)
        del self.pending[pid]
        del self.positions[pid]

    def _trade_row(self, pid: str, p: Mapping[str, Any], s: float, e: Mapping[str, float], x: Mapping[str, float], pnl: float, fill_exit: int | None, **extra: Any) -> None:
        self._w({"kind": "trade", "position_id": pid, "stake_sol": s, "decision_ms": {"entry": p["t_dec"], "exit": p.get("exit_dec")}, "fill_ms": {"exit": fill_exit}, "reason": p.get("reason"),
                 "pnl_lamports": pnl, "entry_conv_cost_lamports": e["conv_cost_lamports"], "exit_conv_cost_lamports": x["conv_cost_lamports"], "sol_out_lamports": x["sol_lamports"],
                 "send_lamports_total": N_SENDS * self.send, "send_lamports": self.send, "n_sends": N_SENDS, "rent_report_lamports": 2 * RENT_REPORT_LAMPORTS, "ssb": p.get("ssb", 0.0),
                 "nearby_lamports": p.get("nearby", 0.0), "quote_mint": p["qm"], "pool": p["pool"], "creator": p.get("creator"), "lat_entry_s": self.lat_ms / 1000.0, **extra})

    def _fill_exit(self, pid: str, p: dict, row: Mapping[str, Any], t: int) -> None:
        got, why = self._states(p, row, t)
        if got is None:
            self._c("exit_waiting_" + str(why))
            return  # wait for a later print with a resolvable quote state and pool fee (at most until `cap`, see expire)
        if self._gap_between(p["exit_dec"], t):  # the exit leg only; a gap elsewhere in the hold is flagged on the trade row (gap_in_hold), not an exclusion
            self._w({"kind": "excluded", "position_id": pid, "side": "sell", "reason": "feed_gap_exit", "t_ms": t})
            self._c("excluded_feed_gap_exit")
            del self.pending[pid]
            del self.positions[pid]
            return
        qs, ps = got
        for s, e in p["legs"].items():
            x = O.price_exit(e["tokens"], qs, ps)
            self._trade_row(pid, p, s, e, x, O.trip_pnl(s * 1e9, e, x) - N_SENDS * self.send, t, **({"gap_in_hold": True} if self._gap_between(p["t_dec"], t) else {}))
        del self.pending[pid]
        del self.positions[pid]
        self._c("exits_filled")

    def _stale_exit(self, pid: str, p: dict, t: int) -> None:
        """the exit waited EXIT_WAIT_CAP_MS without a usable quote state: SCORED at the last known states, proceeds x (1 - STALE_EXIT_HAIRCUT); no last state = 0 SOL out"""
        qs = self.table.qstate.get(p["qm"]) if self.table is not None else None  # the last quote state, however old
        fee, lp = (self.table.pfee.get(p["pool"]) if self.table is not None else None), p.get("last_ps")
        priced = qs is not None and lp is not None and fee is not None
        for s, e in p["legs"].items():
            if priced:
                x = O.price_exit(e["tokens"], qs, {**lp, "fee": fee})
                x = {**x, "sol_lamports": x["sol_lamports"] * (1 - STALE_EXIT_HAIRCUT)}
            else:
                x = {"sol_lamports": 0.0, "conv_cost_lamports": 0.0}
            self._trade_row(pid, p, s, e, x, O.trip_pnl(s * 1e9, e, x) - N_SENDS * self.send, None, stale_exit_capped=True, stale_priced=priced, stale_haircut=STALE_EXIT_HAIRCUT)
        del self.pending[pid]
        del self.positions[pid]
        self._c("stale_exit_capped" if priced else "stale_exit_capped_unpriced")

    def _score_unfilled(self, pid: str, p: dict, t: int) -> None:
        """an entry that did not land in 24 h: round trip at the post-trigger state, both conversion legs, pool fees, N_SENDS sends (the buy WOULD have landed at about that state)"""
        qs0, ps0, fee = p.get("qs0"), p.get("ps0"), (self.table.pfee.get(p["pool"]) if self.table is not None else None)
        priced = qs0 is not None and ps0 is not None and fee is not None
        self._c("entry_unfilled_cancelled")
        if not priced:
            self._c("entry_unfilled_unpriced")
        for s in self.stakes:
            if priced:
                ps = {**ps0, "fee": fee}
                e = O.price_entry(s * 1e9, qs0, ps)
                x = O.price_exit(e["tokens"], qs0, ps)
                pnl = O.trip_pnl(s * 1e9, e, x) - N_SENDS * self.send
            else:  # no state to price a round trip at: the sends alone (a lower bound on the loss), flagged
                e, x, pnl = {"conv_cost_lamports": 0.0}, {"sol_lamports": s * 1e9, "conv_cost_lamports": 0.0}, -float(N_SENDS * self.send)
            self._trade_row(pid, {**p, "exit_dec": t, "reason": "entry_unfilled_at_exit"}, s, e, x, pnl, None, unfilled_roundtrip=True, unfilled_priced=priced)

    def status(self) -> dict[str, Any]:
        """pending counts by state, then the counters (entry_refused_*, excluded_*, entry_unfilled_cancelled, exit_waiting_*, stale_exit_capped*, entries_filled, exits_filled), the fee audit
        (rows, creator_fee_missing, buyback_fee_missing; buyback is UNMODELLED). An entry the engine refused is dropped from the host book (host count entries_refused_at_landing).
        Host-log `exits` overcount closed trades by entry_unfilled_cancelled: count trades from `trade` rows only."""
        fa = dict(self.table.fee_audit) if self.table is not None else {}
        return {"pending": {k: sum(1 for p in self.pending.values() if p["state"] == k) for k in ("entry", "exit", "open")}, **self.counters, "fee_audit": fa,
                "quote_post_fallback": getattr(self.table, "quote_post_fallback", 0), "buyback_fee_modelled": False, "lat_entry_s": self.lat_ms / 1000.0}
