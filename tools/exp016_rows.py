"""Compact per-mint row storage for the EXP-016 screen (memory, job #296: about 1.35 KB per retained dict row).

A `RowStore` keeps one mint's tape rows as columns (`array('q')` for the integer fields, `array('d')` for the float fields, `array('B')` for the
categorical fields, `array('i')` interned ids for wallet and pool, ascii bytes for the signature) and yields plain dicts on iteration, so every
consumer (admission, `print_from_trade_row`, `make_wrapper`, the simulator, features, the block pass, `stamp_rows`) is unchanged.

What is and is not preserved, exactly:
  * A row whose keys are ALL in `STORED_KEYS` (the fields the consumers read, reviewed against `print_from_trade_row`, `make_wrapper`,
    `latency_curve`, `exploration_exits`, `exp016_rug`, `_Feat.record`, the admission gates and `adapt_trade_row`) and whose values fit the columns
    comes back with the same keys and values.
  * The only keys allowed to be dropped are the reviewed `IGNORABLE` ones (each carries its reason): no consumer of a trade row reads them.
  * ANY other key forces the whole row into `_fallback`, stored and returned as a dict copy. A key is never dropped silently, and the unknown key
    names are counted (`RowStore.unknown_keys`), so a real layout shows what to review. The same fallback holds a row whose value does not fit
    (a non-int in an int field, an out-of-range int, an unexpected categorical).

No outcome is computed here: this is storage only.
"""
from __future__ import annotations

import math
from array import array
from typing import Any, Iterator, Mapping

NONE = -(1 << 63)  # int64 sentinel for "absent or None"
INT_FIELDS = ("slot", "t_recv_ms", "block_time", "quote_reserve", "base_reserve", "sol_lamports", "token_raw", "tx_index", "event_index",
              "pool_quote_amount", "lp_fee", "protocol_fee", "creator_fee")  # the last four: `print_from_trade_row` reads them for PumpSwap v1 rows
FLOAT_FIELDS = ("price_sol", "market_cap_sol")
CAT_FIELDS = ("venue", "side", "type")  # str or None
STR_FIELDS = ("trader", "pool")  # interned (a wallet or pool repeats across rows)
SIG_MAX = 255  # `signature` (unique per row, skipped for slim rows) is kept as ascii bytes in one blob per mint, not as a str object
FIELDS = INT_FIELDS + FLOAT_FIELDS + CAT_FIELDS + STR_FIELDS + ("signature", "quote_is_wsol", "mint")
STORED_KEYS = frozenset(FIELDS)
# Reviewed (job #299 key sample of every source, then a grep of every consumer of a TRADE row: `print_from_trade_row` / `_Feat.record` /
# `make_wrapper` / `latency_curve` / `exploration_exits` / `exp016_rug` / admission / `adapt_trade_row`). None of these is read there; each reason is the grep result.
IGNORABLE = {
    "event_ts": "Oracle receive timestamp string; adapt_trade_row derives block_time from t_recv_ms and nothing reads event_ts",
    "v": "the listener's schema version tag; no consumer of a trade row reads it (`\"v\"` elsewhere is the V-map document and mcap_mode, not a row key)",
    "quote_mint": "read only on CREATE rows (latency_curve, exploration_exits, exploration_entry_model, oracle_live_adapter); trade rows are gated on quote_is_wsol, which is stored",
    "source": "listener provenance label; the `source` keys that are read belong to result rows (exp015/exp016 tables), not tape rows",
    "t_recv": "second-resolution duplicate of t_recv_ms (read only by fast_helius_pre / pump_history_backfill writers); every consumer uses t_recv_ms",
    "sol": "bonding-row alias of sol_lamports (the stored field is the one read); `sol` reads elsewhere are result/PnL rows",
    "token": "bonding-row alias of token_raw (the stored field is the one read)",
    "feed": "listener feed name; read only by the backfill writer (pump_history_backfill)",
    "commitment": "RPC commitment tag of the capture; no tape consumer reads it",
    "market_cap_supply_ui": "bonding-row display field; the pricing path reads market_cap_sol (stored) and recomputes the rest",
    "mint_source": "read only by the paper tape scan (paper_price_path stats / laya / signal core), not by print_from_trade_row or any screen consumer",
    "zero_sol": "backfill diagnostic flag on rare rows; read by no consumer (pump_history_backfill writes it)",
}
IGNORABLE_KEYS = frozenset(IGNORABLE)


UNKNOWN_KEY_CAP = 64  # distinct unknown key names remembered (counts only)


class Interner:
    """str <-> small int, shared by every store of one source."""

    def __init__(self) -> None:
        self.ids: dict[str, int] = {}
        self.vals: list[str] = []
        self.unknown_keys: dict[str, int] = {}  # key name -> rows stored whole because of it (shared by the source's stores)
        self.n_fallback = 0

    def get(self, s: str) -> int:
        i = self.ids.get(s)
        if i is None:
            i = len(self.vals)
            self.ids[s] = i
            self.vals.append(s)
        return i


class RowStore:
    __slots__ = ("mint", "_it", "_ints", "_floats", "_cats", "_strs", "_wsol", "_sig", "_sigoff", "_siglen", "_fallback", "_cat_tab", "_n")

    def __init__(self, mint: str, interner: Interner) -> None:
        self.mint = mint
        self._it = interner
        self._ints = [array("q") for _ in INT_FIELDS]
        self._floats = [array("d") for _ in FLOAT_FIELDS]
        self._cats = [array("B") for _ in CAT_FIELDS]
        self._strs = [array("i") for _ in STR_FIELDS]
        self._wsol = array("B")  # 0 absent/None, 1 True, 2 False
        self._sig = bytearray()
        self._sigoff = array("I")
        self._siglen = array("B")  # 0 = absent
        self._fallback: dict[int, Mapping[str, Any]] = {}
        self._cat_tab: list[list[str | None]] = [[None] for _ in CAT_FIELDS]  # code 0 = absent
        self._n = 0

    def __len__(self) -> int:
        return self._n

    def __bool__(self) -> bool:
        return self._n > 0

    def _cat_code(self, k: int, v: Any) -> int | None:
        if v is None:
            return 0
        if type(v) is not str:
            return None
        tab = self._cat_tab[k]
        try:
            return tab.index(v)
        except ValueError:
            if len(tab) >= 255:
                return None
            tab.append(v)
            return len(tab) - 1

    def append(self, r: Mapping[str, Any]) -> None:
        ints = []
        ok = True
        for k in r:
            if k not in STORED_KEYS and k not in IGNORABLE_KEYS:  # FAIL SAFE: an unreviewed key keeps the whole row
                ok = False
                uk = self._it.unknown_keys
                if k in uk or len(uk) < UNKNOWN_KEY_CAP:
                    uk[k] = uk.get(k, 0) + 1
        for f in INT_FIELDS if ok else ():
            v = r.get(f)
            if v is None:
                ints.append(NONE)
            elif type(v) is int and NONE < v < (1 << 63):
                ints.append(v)
            else:
                ok = False
                break
        floats = []
        if ok:
            for f in FLOAT_FIELDS:
                v = r.get(f)
                if v is None:
                    floats.append(math.nan)
                elif type(v) is float and not math.isnan(v):
                    floats.append(v)
                else:
                    ok = False
                    break
        cats = []
        if ok:
            for k, f in enumerate(CAT_FIELDS):
                c = self._cat_code(k, r.get(f))
                if c is None:
                    ok = False
                    break
                cats.append(c)
        strs = []
        if ok:
            for f in STR_FIELDS:
                v = r.get(f)
                if v is None:
                    strs.append(-1)
                elif type(v) is str:
                    strs.append(self._it.get(v))
                else:
                    ok = False
                    break
        sig = b""
        if ok:
            sv = r.get("signature")
            if sv is not None:
                try:
                    sig = sv.encode("ascii") if type(sv) is str else b""
                except UnicodeEncodeError:
                    sig = b""
                if not sig or len(sig) > SIG_MAX or len(self._sig) > 0xFFFF0000:
                    ok = False
        w = r.get("quote_is_wsol")
        if ok and w is not None and type(w) is not bool:
            ok = False
        if ok and r.get("mint") not in (None, self.mint):
            ok = False  # a row filed under another mint: keep it whole
        if not ok:
            self._it.n_fallback += 1
            self._fallback[self._n] = dict(r)
            ints, floats, cats, strs, w, sig = [NONE] * len(INT_FIELDS), [math.nan] * len(FLOAT_FIELDS), [0] * len(CAT_FIELDS), [-1] * len(STR_FIELDS), None, b""
        for col, v in zip(self._ints, ints):
            col.append(v)
        for col, v in zip(self._floats, floats):
            col.append(v)
        for col, v in zip(self._cats, cats):
            col.append(v)
        for col, v in zip(self._strs, strs):
            col.append(v)
        self._wsol.append(0 if w is None else (1 if w else 2))
        self._sigoff.append(len(self._sig))
        self._siglen.append(len(sig))
        self._sig += sig
        self._n += 1

    def __iter__(self) -> Iterator[dict[str, Any]]:
        vals = self._it.vals
        for i in range(self._n):
            fb = self._fallback.get(i)
            if fb is not None:
                yield dict(fb)
                continue
            d: dict[str, Any] = {"mint": self.mint}
            for f, col in zip(INT_FIELDS, self._ints):
                v = col[i]
                if v != NONE:
                    d[f] = v
            for f, col in zip(FLOAT_FIELDS, self._floats):
                v = col[i]
                if v == v:
                    d[f] = v
            for k, (f, col) in enumerate(zip(CAT_FIELDS, self._cats)):
                c = col[i]
                if c:
                    d[f] = self._cat_tab[k][c]
            for f, col in zip(STR_FIELDS, self._strs):
                v = col[i]
                if v >= 0:
                    d[f] = vals[v]
            n = self._siglen[i]
            if n:
                o = self._sigoff[i]
                d["signature"] = self._sig[o:o + n].decode("ascii")
            w = self._wsol[i]
            if w:
                d["quote_is_wsol"] = w == 1
            yield d

    def nbytes(self) -> int:
        """Bytes held by the columns (excludes the shared interner)."""
        cols = [*self._ints, *self._floats, *self._cats, *self._strs, self._wsol, self._sigoff, self._siglen]
        return sum(c.buffer_info()[1] * c.itemsize for c in cols) + len(self._sig)
