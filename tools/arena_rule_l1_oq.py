"""arena_rule_l1_oq: bot `l1-floor-oq-v1`, FLOOR (l1-floor-v2, tools/arena_rule_l1.py) on OTHER-QUOTE (non-WSOL) PumpSwap pools. PAPER only. No key, no network, no live path.
Design: floor-siblings-design.md section 4 Card 2. Trigger = scripts/research/floor_other_quote_funnel.py stage for stage (a parity test pins it). Nothing here says the rule makes money.

TRIGGER (same as the funnel module): PumpSwap SELL print, row quote_mint not WSOL and not empty; the quote has a price (below); V / V_ref (the pool's first-row V) in
[V_LO/V_MID, V_HI/V_MID]; age (print time - the pool's first print seen) in [24 h, 200 h]; vault after the print (quote units, FEE 1.25% formula as FLOOR and the funnel) x quote
price <= 0.5 SOL; a previous visible print; previous print <= 6 h; 24 h volume (quote units x price at each print) >= 1 SOL; post-trade spot / spot at the print 6 h back < 0.80.
Quote classes (usdc / pump / other) are the funnel's `quote_class`, kept as a meta label only.
EXITS: FLOOR's: take profit when the post-trade spot with our position kept >= 1.5 x the post-buy mark (a ratio of two spots of the same pool: the quote price cancels), else a time
exit at decision + 1.9 s + 24 h on the 1 s tick. No stop. One open position per pool, 60 s cooldown, stake 0.25 SOL.

QUOTE PRICE (every leg): the quote asset's OWN WSOL pool, from the SAME stream: the WSOL-quoted PumpSwap pool whose base mint is the quote mint (if several, the one with the
largest real quote reserve). Its latest row's POST-trade state (1.25% approximation as spot_after; the pre-trade state only when the trade fields are missing, counted) gives (Qw, Bw) = (quote_reserve + V, base_reserve) and mid = Qw / Bw lamports per raw quote unit. A state older than
QUOTE_STALE_MS_USDC (10 min, USDC) / QUOTE_STALE_MS_OTHER (2 min, any other quote) at the moment of use, or absent, REFUSES the row with a counted reason (`refused`), never priced at a guess. No static price file.

PRICING FORMULA (pure functions below; constant product on the pools' virtual reserves, fee taken on the input side of a buy and on the output side of a sell):
  entry:  stake S lamports of SOL -> conversion leg 1 (buy the quote asset in its WSOL pool, fee f_w):  Qu = Bw * S(1-f_w) / (Qw + S(1-f_w))
          then the token leg (pool's own fee f_p):  T = Be * Qu(1-f_p) / (Qe + Qu(1-f_p)),  Qe = pool quote reserve + V, Be = pool base reserve
  exit:   token leg:  Qo = Qe' * T / (Be' + T) * (1-f_p);  conversion leg 2 (sell the quote asset in its WSOL pool):  S' = Qw' * Qo / (Bw' + Qo) * (1-f_w)
  P&L (SOL lamports) = S' - S, before the engine's send / priority / rent stack (a host adds that; see PR body). The quote-conversion cost is the S -> Qu and Qo -> S' legs' fee
  f_w plus their price impact, i.e. mid-vs-fill on the quote's WSOL pool, at the entry state and at the exit state.
  Fees are per pool, read from the pool's own rows: f = (lp_fee + protocol_fee + creator_fee + buyback_fee when present) / pool_quote_amount of its latest row that has lp, protocol and amount; a pool without one refuses the pricing
  (reason no_pool_fee / no_quote_pool_fee). The 1.25% FEE constant is used ONLY in the trigger's vault / spot formulas, exactly as the funnel and FLOOR do.
WSOL pools never enter: on_row returns [] on a WSOL or empty quote mint, and the table never opens an other-quote pool for them.
NOT DONE HERE: tools/arena_paper.ArenaEngine prices buys as WSOL pools; this module does not change it. The landing-state pricing (entry latency) is `price_entry` / `price_exit` called
with explicit states by whatever host owns the latency model (see PR body, open questions).
"""

from __future__ import annotations

import collections
import os
import pickle
from bisect import bisect_left, bisect_right
from pathlib import Path
from typing import Any, Mapping

from tools.arena_pool_table import FEE, HIST_HOURS, HOUR_MS, V_HI, V_LO, WSOL, PoolPrints, PoolTable, spot_after
from tools.arena_rules import Buy, Ctx, Rule, Sell

BOT_ID = "l1-floor-oq-v1"
CELL = "l1oq"
STAKE_SOL = 0.25
STAKE_LAMPORTS = 0.25e9
H = 3600
V_MID = 17_600_000_000
AGE_LO, AGE_HI, Q_STAR, ALIVE_S, VOL_MIN, R6_MAX = 24 * H, 200 * H, 0.5e9, 6 * H, 1e9, 0.80
AGE_DROP_S = AGE_HI + H
TP = 1.5
HOLD_S = 86400.0
LEG_MS = 1_900
HOLD_MS = int(HOLD_S * 1000) + LEG_MS
COOLDOWN_MS = 60_000
FEED_D = 7
LAT_DECIDING = 1.9
QUOTE_STALE_MS_USDC = 600_000  # declared: a USDC quote's WSOL pool state must be at most 10 min old
QUOTE_STALE_MS_OTHER = 120_000  # declared (QP 47229af2 item 4): a pump-class / other quote (a memecoin) moves fast: its WSOL pool state must be at most 2 min old
QUOTE_STALE_MS = QUOTE_STALE_MS_USDC  # kept name: the USDC bound
LATENCY_VALUES_S = (3.010, 4.085, 5.590)  # DEC-035 Am.9 Erratum 2 latency values (total chain time, decision to entry landing)
LAT_PRICING_DECIDING_S = 4.085  # the deciding pricing latency of the book (Card 2 believe bar); the rule's DECISIONS stay on LEG_MS / LAT_DECIDING so twins share decisions
REASON_TP, REASON_TIME = "tp_1.5x", "time_24h"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
TABLE_MODE = "other_quote"
STAGES = ["other_quote_sell_rows", "quote_price_known", "v_scaled_band", "age_24_200", "vault_le_0.5_soleq", "prev_print_visible", "prev_le_6h", "vol24_ge_1_soleq",
          "r6_lt_0.80(trigger)"]


def quote_class(mint: Any) -> str:
    if mint == USDC:
        return "usdc"
    if isinstance(mint, str) and mint.endswith("pump"):
        return "pump"
    return "other"


def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def row_fee(row: Mapping[str, Any]) -> float | None:
    """The pool's own fee fraction from one row: (lp_fee + protocol_fee + creator_fee [+ buyback_fee]) / pool_quote_amount. lp_fee, protocol_fee and pool_quote_amount are required
    (None otherwise: never a default). creator_fee is the decoder's trader-charged creator leg (observe/trade_decode.py, stored on the tape row); buyback_fee is added when a row carries
    it (the stored row currently does not). A missing or None creator_fee / buyback_fee counts as 0: the decoder reads 0 for events too short to carry it."""
    lp, pf, amt = row.get("lp_fee"), row.get("protocol_fee"), row.get("pool_quote_amount")
    if not (_num(lp) and _num(pf) and _num(amt)) or amt <= 0 or lp < 0 or pf < 0:
        return None
    extra = 0.0
    for k in ("creator_fee", "buyback_fee"):
        v = row.get(k)
        if v is None:
            continue
        if not _num(v) or v < 0:
            return None
        extra += v
    f = (lp + pf + extra) / amt
    return f if 0 <= f < 1 else None


# ------------------------------------------------------------------------------------------------------------------------------ pricing (pure)
def cp_buy(Qe: float, Be: float, x: float, fee: float) -> float:
    """Tokens out for `x` quote in: fee on the input, constant product on (Qe, Be)."""
    net = x * (1 - fee)
    return Be * net / (Qe + net)


def cp_sell(Qe: float, Be: float, t: float, fee: float) -> float:
    """Quote out for `t` tokens in: constant product, fee on the output."""
    return Qe * t / (Be + t) * (1 - fee)


def price_entry(stake_lamports: float, qs: Mapping[str, Any], ps: Mapping[str, Any]) -> dict[str, float]:
    """qs = the quote's WSOL pool state {Qw, Bw, fee}; ps = the OQ pool state {Qe, Be, fee} (pre-trade, V included in Q). Returns quote units bought, tokens received and
    the conversion leg's cost in SOL lamports (stake - SOL value of Qu at the quote pool's mid)."""
    qu = cp_buy(qs["Qw"], qs["Bw"], stake_lamports, qs["fee"])
    tok = cp_buy(ps["Qe"], ps["Be"], qu, ps["fee"])
    return {"quote_units": qu, "tokens": tok, "conv_cost_lamports": stake_lamports - qu * qs["Qw"] / qs["Bw"]}


def price_exit(tokens: float, qs: Mapping[str, Any], ps: Mapping[str, Any]) -> dict[str, float]:
    """The reverse trip at the exit-time states. conv_cost_lamports = SOL value of the quote received at mid minus the SOL actually received."""
    qo = cp_sell(ps["Qe"], ps["Be"], tokens, ps["fee"])
    sol = cp_sell(qs["Qw"], qs["Bw"], qo, qs["fee"])
    return {"quote_units": qo, "sol_lamports": sol, "conv_cost_lamports": qo * qs["Qw"] / qs["Bw"] - sol}


def trip_pnl(stake_lamports: float, entry: Mapping[str, float], exit_: Mapping[str, float]) -> float:
    return exit_["sol_lamports"] - stake_lamports


# ------------------------------------------------------------------------------------------------------------------------------ table
class OQTable(PoolTable):
    """History for the rule (the WSOL PoolTable is not edited and still drops non-WSOL rows). Reads every PumpSwap row: WSOL rows only feed the quote-price states and the slot
    rate; other-quote rows feed the pool's prints (sol field = SOL-equivalent lamports at the print's moment). `mode` is "other_quote"; no admission / create / complete logic."""

    def __init__(self, mode: str = TABLE_MODE, t0: int | None = None) -> None:
        super().__init__("complete", t0)
        self.mode = mode
        self.first: dict[str, int] = {}
        self.vref: dict[str, float | None] = {}
        self.pfee: dict[str, float] = {}
        self.qstate: dict[str, dict[str, Any]] = {}  # quote mint -> {pool, q (real quote reserve), Qw, Bw, fee, t}
        self.refused: collections.Counter = collections.Counter()
        self.trig_rows = 0
        self.fee_audit: collections.Counter = collections.Counter()  # rows seen by the fee reader: rows, creator_fee_missing, buyback_fee_missing (never silent)
        self.quote_post_fallback = 0

    # reading
    def quote_state(self, qm: str, now_ms: int) -> tuple[dict[str, Any] | None, str | None]:
        s = self.qstate.get(qm)
        if s is None:
            return None, "no_quote_price"
        if now_ms - s["t"] > (QUOTE_STALE_MS_USDC if quote_class(qm) == "usdc" else QUOTE_STALE_MS_OTHER):
            return None, "stale_quote_price"
        return s, None

    def quote_mid(self, qm: str, now_ms: int) -> float | None:
        s, _ = self.quote_state(qm, now_ms)
        return None if s is None else s["Qw"] / s["Bw"]

    # writing
    def update(self, row: Mapping[str, Any]) -> bool:
        t = row.get("t_recv_ms")
        if isinstance(t, bool) or not isinstance(t, int):
            return False
        if row.get("type") != "trade" or (row.get("venue") or row.get("prog")) != "pumpswap":
            return False
        qm, pool, ts, side = row.get("quote_mint"), row.get("pool"), row.get("event_ts"), row.get("side")
        if not qm or not pool or row.get("slot") is None or side not in ("buy", "sell") or not _num(ts) or ts <= 0:
            return False
        key = (row.get("signature"), row.get("type"), row.get("event_index"))
        if self.skip_before:  # right after a rebuild / load only: rows the table already holds are skipped once (the host's warm-up replay feeds them again)
            if t < self.skip_before or (t == self.skip_before and key in self.hwm_keys):
                return False
            if t > self.skip_before:
                self.skip_before = 0
        if t > self.hwm_ms:
            self.hwm_ms, self.hwm_keys = t, set()
        if t == self.hwm_ms:
            self.hwm_keys.add(key)
        bt, hidx = int(ts), t // HOUR_MS
        self.rows_applied += 1
        sl_ = int(row["slot"])
        rh = self.rate_h.get(hidx)  # the slot rate over every PumpSwap row (the WSOL table uses WSOL rows only; the rate only sets the landing-slot k)
        if rh is None:
            self.rate_h[hidx] = [sl_, sl_, bt, bt, 1]
            for old in [h for h in self.rate_h if h < hidx - 2]:
                del self.rate_h[old]
        else:
            rh[0], rh[1], rh[2], rh[3], rh[4] = min(rh[0], sl_), max(rh[1], sl_), min(rh[2], bt), max(rh[3], bt), rh[4] + 1
        f = row_fee(row)
        self.fee_audit["rows"] += 1
        if row.get("creator_fee") is None:
            self.fee_audit["creator_fee_missing"] += 1
        if row.get("buyback_fee") is None:
            self.fee_audit["buyback_fee_missing"] += 1  # the stored tape row never carries it: buyback_fee is UNMODELLED (declared in the output)
        if qm == WSOL:
            self._wsol_row(row, t, f)
            return True
        if f is not None:
            self.pfee[pool] = f
        if pool not in self.first:
            self.first[pool] = bt
            v0 = row.get("virtual_quote_reserves")
            self.vref[pool] = float(v0) if _num(v0) and v0 > 0 else None
        s, why = self.quote_state(qm, t)
        if s is not None:
            try:
                sol_eq, spot = float(row["sol_lamports"]) * s["Qw"] / s["Bw"], spot_after(row)
            except (KeyError, TypeError, ValueError):
                return True
            pp = self.prints.setdefault(pool, PoolPrints())
            pp.add(bt, sol_eq, spot, hidx)
            pp.trim(hidx - (HIST_HOURS - 1))
            if bt - self.first[pool] > AGE_DROP_S:
                del self.prints[pool]
        return True

    def _wsol_row(self, row: Mapping[str, Any], t: int, f: float | None) -> None:
        m = row.get("mint")
        v, q, b = row.get("virtual_quote_reserves"), row.get("quote_reserve"), row.get("base_reserve")
        if not m or f is None or not (_num(v) and _num(q) and _num(b)) or b <= 0 or q + v <= 0:
            return
        cur = self.qstate.get(m)
        if cur is not None and cur["pool"] != row["pool"] and q <= cur["q"] and t - cur["t"] <= QUOTE_STALE_MS:
            return  # another, shallower pool of the same mint: the deepest live pool is the quote's price source
        Qw, Bw = float(q) + float(v), float(b)
        try:  # POST-trade state (the row's reserves are pre-trade): the same 1.25% approximation as spot_after
            sol, tok, buy = float(row["sol_lamports"]), float(row["token_raw"]), row["side"] == "buy"
            qp = float(q) + (sol * (1 - FEE) if buy else -sol / (1 - FEE))
            bp = b - tok if buy else b + tok
            if qp + float(v) > 0 and bp > 0:
                Qw, Bw = qp + float(v), float(bp)
            else:
                self.quote_post_fallback += 1
        except (KeyError, TypeError, ValueError):
            self.quote_post_fallback += 1  # no usable trade fields: the pre-trade state is kept and counted
        self.qstate[m] = {"pool": row["pool"], "q": float(q), "Qw": Qw, "Bw": Bw, "fee": f, "t": t}

    def sweep(self, now_ms: int) -> int:
        for m in [m for m, s in self.qstate.items() if now_ms - s["t"] > 86_400_000]:
            del self.qstate[m]
        for pool in list(self.prints):
            pp = self.prints[pool]
            pp.trim(now_ms // HOUR_MS - (HIST_HOURS - 1))
            if len(pp) == 0 and now_ms // 1000 - self.first.get(pool, now_ms // 1000) > AGE_DROP_S:
                del self.prints[pool]
        for pid in [k for k in self.marks if k not in self.live_positions]:
            del self.marks[pid]
        for pool in [p for p, ms in self.last_exit.items() if now_ms - ms > 3_600_000]:
            del self.last_exit[pool]
        return 0

    def sizes(self) -> dict[str, int]:
        return {"first": len(self.first), "prints_pools": len(self.prints), "prints_rows": sum(map(len, self.prints.values())), "quote_states": len(self.qstate),
                "last_exit": len(self.last_exit), "marks": len(self.marks), "seen": 0, "uni": 0}

    # persistence (own pickle; the WSOL table's to_dict is not used)
    def to_dict(self) -> dict[str, Any]:
        return {"version": 101, "mode": self.mode, "hwm_ms": self.hwm_ms, "hwm_keys": sorted(map(tuple, self.hwm_keys), key=repr), "first": self.first, "vref": self.vref, "pfee": self.pfee, "qstate": self.qstate, "last_exit": self.last_exit,
                "rate_h": self.rate_h, "marks": self.marks, "refused": dict(self.refused), "trig_rows": self.trig_rows, "rows_applied": self.rows_applied, "fee_audit": dict(self.fee_audit), "quote_post_fallback": self.quote_post_fallback,
                "prints": {p: (list(pp.bt), list(pp.sol), list(pp.spot), list(pp.hidx)) for p, pp in self.prints.items()}}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "OQTable":
        from array import array

        if d.get("version") != 101:
            raise ValueError("other-quote pool table checkpoint: unknown version")
        t = cls(d["mode"])
        t.hwm_ms, t.hwm_keys, t.skip_before = int(d["hwm_ms"]), {tuple(k) for k in d.get("hwm_keys", [])}, int(d["hwm_ms"])
        t.first, t.vref, t.pfee, t.qstate = dict(d["first"]), dict(d["vref"]), dict(d["pfee"]), dict(d["qstate"])
        t.last_exit, t.rate_h, t.marks = dict(d["last_exit"]), {int(k): list(v) for k, v in d["rate_h"].items()}, {k: tuple(v) for k, v in d["marks"].items()}
        t.refused, t.trig_rows, t.rows_applied = collections.Counter(d["refused"]), int(d["trig_rows"]), int(d["rows_applied"])
        t.fee_audit, t.quote_post_fallback = collections.Counter(d.get("fee_audit", {})), int(d.get("quote_post_fallback", 0))
        for p, (bt, sol, spot, hidx) in d["prints"].items():
            pp = PoolPrints()
            pp.bt, pp.sol, pp.spot, pp.hidx = array("l", bt), array("d", sol), array("d", spot), array("l", hidx)
            t.prints[p] = pp
        return t

    def checkpoint(self, path: str | os.PathLike) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        with open(tmp, "wb") as fh:
            pickle.dump(self.to_dict(), fh, protocol=4)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)

    @classmethod
    def load(cls, path: str | os.PathLike) -> "OQTable":
        with open(path, "rb") as fh:
            return cls.from_dict(pickle.load(fh))

    def rebuild(self, tape_dir: str | os.PathLike, to_ms: int) -> int:
        """Apply every PumpSwap trade row of the hour files of `tape_dir` with t_recv_ms < to_ms (the walk of PoolTable.rebuild, minus create/complete rows)."""
        import json
        from datetime import datetime, timezone

        from tools.arena_pool_table import _lines

        n0 = self.rows_applied
        self.skip_before = self.hwm_ms
        files: dict[str, list[Path]] = {}
        for f in Path(tape_dir).iterdir():
            nm = f.name
            if nm.startswith("tape-") and (nm.endswith(".jsonl") or nm.endswith(".jsonl.zst")):
                files.setdefault(nm.split(".")[0], []).append(f)
        for h in sorted(files):
            f = sorted(files[h], key=lambda x: x.name.endswith(".zst"))[0]
            hs = int(datetime.strptime(h[5:], "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)
            if hs + HOUR_MS <= self.hwm_ms or hs >= to_ms:
                continue
            for line in _lines(f):
                if b'"trade"' not in line or b"pumpfun" in line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                t = r.get("t_recv_ms")
                if isinstance(t, int) and t < to_ms:
                    self.update(r)
        self.skip_before = self.hwm_ms
        return self.rows_applied - n0


# ------------------------------------------------------------------------------------------------------------------------------ rule
class OQRule(Rule):
    bot_id = BOT_ID
    deciding_stake = STAKE_SOL
    model_ids: tuple[str, ...] = ()
    uses_ticks = True
    universe = "pumpswap_other_quote"
    table_cls = OQTable

    def __init__(self, table_mode: str = TABLE_MODE) -> None:
        self.table_mode = table_mode

    @staticmethod
    def trigger(tab: OQTable, row: Mapping[str, Any]) -> tuple[str | None, dict[str, Any]]:
        """(stage reached name or None, info). The row has ALREADY been applied to `tab` (its print is the table's last). Returns the LAST stage name the row passed (STAGES[-1]
        = trigger), mirroring floor_other_quote_funnel.Counter.row; None when it is not an other-quote PumpSwap sell."""
        t = row.get("t_recv_ms")
        if row.get("type") != "trade" or not isinstance(t, int) or isinstance(t, bool) or (row.get("venue") or row.get("prog")) != "pumpswap":
            return None, {}
        qm, pool, ts = row.get("quote_mint"), row.get("pool"), row.get("event_ts")
        if not qm or qm == WSOL or not pool or row.get("slot") is None or row.get("side") != "sell" or not _num(ts) or ts <= 0 or pool not in tab.first:
            return None, {}
        bt, hidx = int(ts), t // HOUR_MS
        s, why = tab.quote_state(qm, t)
        if s is None:
            return STAGES[0], {"refused": why}
        v, vref = row.get("virtual_quote_reserves"), tab.vref[pool]
        if not _num(v) or vref is None or not V_LO / V_MID <= v / vref <= V_HI / V_MID:
            return STAGES[1], {}
        age = bt - tab.first[pool]
        if not AGE_LO <= age <= AGE_HI:
            return STAGES[2], {}
        try:
            qpost = float(row["quote_reserve"]) - float(row["sol_lamports"]) / (1 - FEE)
        except (KeyError, TypeError, ValueError):
            return STAGES[3], {}
        mid = s["Qw"] / s["Bw"]
        if not qpost * mid <= Q_STAR:
            return STAGES[3], {}
        pp = tab.prints.get(pool)
        if pp is None or not len(pp):
            return STAGES[4], {}
        n = len(pp)
        j = n - 1
        lo = bisect_left(pp.hidx, hidx - (HIST_HOURS - 1))
        if j - lo <= 0 or pp.bt[j] != bt:
            return STAGES[4], {}
        if pp.bt[j] - pp.bt[j - 1] > ALIVE_S:
            return STAGES[5], {}
        j24 = bisect_left(pp.bt, pp.bt[j] - 86400, lo, n)
        vol = float(sum(pp.sol[j24:j + 1]))
        if vol < VOL_MIN:
            return STAGES[6], {}
        j6 = bisect_right(pp.bt, pp.bt[j] - ALIVE_S, lo, j + 1) - 1
        if j6 < lo:
            return STAGES[7], {}
        r6 = pp.spot[j] / pp.spot[j6] if pp.spot[j6] != 0 else float("nan")
        if not r6 < R6_MAX:
            return STAGES[7], {}
        return STAGES[8], {"age_s": age, "vol24_sol": vol / 1e9, "r6": r6, "quote_mid": mid, "quote_class": quote_class(qm), "quote_mint": qm}

    def on_row(self, ctx: Ctx, row: Mapping[str, Any]) -> list:
        tab = ctx.table
        orders: list = []
        if row.get("type") != "trade" or (row.get("venue") or row.get("prog")) != "pumpswap":
            return orders
        qm, pool = row.get("quote_mint"), row.get("pool")
        if not qm or qm == WSOL or not pool:  # WSOL pools never enter this rule (and an unresolved quote is not an other quote)
            return orders
        mine = [p for p in ctx.positions if p.pool == pool]
        for pos in mine:
            if not pos.exiting and self._tp_hit(ctx, pos, row):
                orders.append(Sell(pos.position_id, REASON_TP))
        if mine:
            return orders
        stage, info = self.trigger(tab, row)
        if stage is None:
            return orders
        if "refused" in info:
            tab.refused[info["refused"]] += 1
            return orders
        if stage != STAGES[-1]:
            return orders
        tab.trig_rows += 1
        last = tab.exit_ms(pool)
        if last is not None and ctx.now_ms < last + COOLDOWN_MS:
            return orders
        s, _ = tab.quote_state(qm, row["t_recv_ms"])
        f_p = tab.pfee.get(pool)
        if f_p is None:
            tab.refused["no_pool_fee"] += 1
            return orders
        if s["fee"] is None:  # row_fee gated the state, kept for safety
            tab.refused["no_quote_pool_fee"] += 1
            return orders
        v = row["virtual_quote_reserves"]
        ps = {"Qe": float(row["quote_reserve"]) + float(v), "Be": float(row["base_reserve"]), "fee": f_p}
        ent = price_entry(STAKE_LAMPORTS, s, ps)
        meta = {"rule": BOT_ID, "cell": CELL, "hold_s": HOLD_S, "tp": TP, "mint": row.get("mint"), "v": v, "age_s": info["age_s"], "vol24_sol": info["vol24_sol"], "r6": info["r6"],
                "quote_mint": qm, "quote_class": info["quote_class"], "quote_mid_lamports_per_raw": info["quote_mid"], "pool_fee": f_p, "quote_pool_fee": s["fee"],
                "decision_state_entry": ent, "mark0": spot_after(row), "rate": tab.rate(row["t_recv_ms"] // HOUR_MS), "signal_slot": row.get("slot"),
                "signal_sig": row.get("signature"), "signal_t_recv_ms": row["t_recv_ms"]}
        return orders + [Buy(pool, meta)]

    @staticmethod
    def _tp_hit(ctx: Any, pos: Any, row: Mapping[str, Any]) -> bool:
        """FLOOR's take profit in quote units: post-trade spot with our position kept >= 1.5 x the post-buy mark, mark = (Qe + net) / (Be - tok) at the landing state, net = the
        quote units bought (entry conversion already applied) x (1 - the pool's own fee). A ratio of two spots of one pool: the quote price cancels."""
        from tools import arena_stack as F

        meta = pos.meta
        sd, rate, slot = meta.get("signal_slot"), meta.get("rate"), row.get("slot")
        if not all(_num(x) for x in (sd, rate, slot)) or rate <= 0:
            return False
        k = int(F.k_slots(LAT_DECIDING, 1.0 / rate, "ceil"))
        if slot < sd + FEED_D + k + 1:
            return False
        ent, f_p = meta.get("decision_state_entry"), meta.get("pool_fee")
        if not isinstance(ent, Mapping) or not _num(f_p):
            return False
        tab = ctx.table
        try:
            q, b, so, tk = float(row["quote_reserve"]), float(row["base_reserve"]), float(row["sol_lamports"]), float(row["token_raw"])
            v = float(row.get("virtual_quote_reserves"))
        except (KeyError, TypeError, ValueError):
            return False
        m = tab.marks.get(pos.position_id)
        if m is None:
            Qe, Be = q + v, b
            net = ent["quote_units"] * (1 - f_p)
            tok = Be * net / (Qe + net)
            if not (0 < tok < Be):
                return False
            m = (float((Qe + net) / (Be - tok)), net, tok)
            tab.marks[pos.position_id] = m
        mark, net, tok = m
        if not mark > 0:
            return False
        buy = row.get("side") == "buy"
        qp = (q + so * 0.99 if buy else q - so * 1.01) + v
        bp = b - tk if buy else b + tk
        bp = bp if bp > 0 else b
        return (qp + net) / max(bp - tok, 1.0) >= TP * mark

    def on_tick(self, ctx: Ctx, T: int) -> list:
        return [Sell(pos.position_id, REASON_TIME) for pos in ctx.positions if not pos.exiting and T >= pos.decision_ms + HOLD_MS]


def make_rule(table_mode: str = TABLE_MODE) -> OQRule:
    return OQRule(table_mode)
