"""Price paths for new pump.fun creates, built only from tape trades.

A path is the ordered bonding-curve prints for a mint, then PumpSwap prints
after migration. Create-payload reserves are an anchor for the fill when the
tape has not yet printed; they are not invented trades on the path.
"""

from __future__ import annotations

import gzip
import io
import json
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence, TextIO

from tools.paper_curve_math import pumpswap_pool_quote_delta, venue_fee_ppm

VENUE_BONDING = "pump_bonding"
VENUE_PUMPSWAP = "pumpswap"


@dataclass(frozen=True, slots=True)
class TapePrint:
    t_recv_ms: int
    slot: int
    event_index: int
    venue: str
    side: str
    sol_lamports: int
    quote_reserve: int
    base_reserve: int
    price_sol: float
    market_cap_sol: float
    signature: str | None = None
    tx_index: int = -1


@dataclass(frozen=True, slots=True)
class CreateSignal:
    mint: str
    t_signal_ms: int
    creator: str | None
    signature: str | None
    v_sol: float | None
    v_token_ui: float | None
    mcap_sol: float | None
    initial_buy_ui: float | None
    sol_amount: float | None


@dataclass
class MintPath:
    create: CreateSignal
    prints: list[TapePrint] = field(default_factory=list)

    def anchor(self) -> TapePrint | None:
        """Reserves on the create message. Knowable at T. Not a tape trade."""
        c = self.create
        if c.v_sol is None or c.v_token_ui is None:
            return None
        if c.v_sol <= 0 or c.v_token_ui <= 0:
            return None
        quote = int(round(c.v_sol * 1_000_000_000))
        base = int(round(c.v_token_ui * 1_000_000))
        if quote <= 0 or base <= 0:
            return None
        price = c.v_sol / c.v_token_ui
        mcap = c.mcap_sol if c.mcap_sol is not None and c.mcap_sol > 0 else price * 1_000_000_000
        return TapePrint(
            t_recv_ms=c.t_signal_ms,
            slot=0,
            event_index=-1,
            venue=VENUE_BONDING,
            side="create",
            sol_lamports=0,
            quote_reserve=quote,
            base_reserve=base,
            price_sol=price,
            market_cap_sol=mcap,
        )


@dataclass
class ScanStats:
    lines: int = 0
    bad_json: int = 0
    kept: int = 0
    unresolved: int = 0
    non_wsol: int = 0
    other_mint: int = 0
    skipped_kept_mint: int = 0
    t_min_ms: int | None = None
    t_max_ms: int | None = None

    def observe_t(self, t_ms: int) -> None:
        if self.t_min_ms is None or t_ms < self.t_min_ms:
            self.t_min_ms = t_ms
        if self.t_max_ms is None or t_ms > self.t_max_ms:
            self.t_max_ms = t_ms

    def as_dict(self) -> dict[str, Any]:
        return {
            "lines": self.lines,
            "bad_json": self.bad_json,
            "kept": self.kept,
            "unresolved": self.unresolved,
            "non_wsol": self.non_wsol,
            "non_wsol_reason": "quote_is_wsol is not true; excluded from SOL PnL",
            "other_mint": self.other_mint,
            "skipped_kept_mint": self.skipped_kept_mint,
            "t_min_ms": self.t_min_ms,
            "t_max_ms": self.t_max_ms,
        }


def parse_time_ms(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e12:
            return int(v)
        if v > 1e9:
            return int(v * 1000)
        return None
    if not isinstance(value, str) or not value or value == "UNK":
        return None
    text = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "UNK" or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN
        return None
    return out


def _tx_kind(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    key = value.strip().lower()
    if key == "create":
        return "create"
    if key in ("migration", "migrate"):
        return "migration"
    return None


def create_from_observe_row(row: dict[str, Any]) -> CreateSignal | None:
    """subscribeNewToken creates only. Migrations are not entry signals."""
    stream = row.get("stream")
    tx = _tx_kind(row.get("txType"))
    if stream is not None and stream != "subscribeNewToken":
        return None
    if tx is not None and tx != "create":
        return None
    if stream != "subscribeNewToken" and tx != "create":
        return None
    mint = row.get("mint")
    if not isinstance(mint, str) or not mint or mint == "UNK":
        return None
    t_ms = parse_time_ms(row.get("t_ws"))
    if t_ms is None:
        return None
    payload = row.get("ws_payload") if isinstance(row.get("ws_payload"), dict) else {}
    creator = row.get("traderPublicKey") or payload.get("traderPublicKey")
    if not isinstance(creator, str) or creator == "UNK":
        creator = None
    signature = row.get("signature")
    if not isinstance(signature, str) or signature == "UNK":
        signature = None
    return CreateSignal(
        mint=mint,
        t_signal_ms=t_ms,
        creator=creator,
        signature=signature,
        v_sol=_float_or_none(row.get("vSolInBondingCurve", payload.get("vSolInBondingCurve"))),
        v_token_ui=_float_or_none(row.get("vTokensInBondingCurve", payload.get("vTokensInBondingCurve"))),
        mcap_sol=_float_or_none(row.get("marketCapSol", payload.get("marketCapSol"))),
        initial_buy_ui=_float_or_none(row.get("initialBuy", payload.get("initialBuy"))),
        sol_amount=_float_or_none(row.get("solAmount", payload.get("solAmount"))),
    )


def _is_zst(path: Path) -> bool:
    name = path.name
    return name.endswith(".jsonl.zst") or path.suffix == ".zst"


class _ZstdText:
    """Line iterator over `zstd -dc`. The CLI is already on the tape host."""

    def __init__(self, path: Path) -> None:
        self._proc = subprocess.Popen(
            ["zstd", "-dc", "--quiet", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if self._proc.stdout is None:
            raise RuntimeError("zstd produced no stdout")
        self._text = io.TextIOWrapper(self._proc.stdout, encoding="utf-8")

    def __iter__(self) -> Iterator[str]:
        return iter(self._text)

    def close(self) -> None:
        self._text.close()
        try:
            code = self._proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=10)
            return
        # -13 / 141: the reader closed the pipe early (span cut). zstd then dies on SIGPIPE.
        if code not in (0, None, -13, 141):
            raise RuntimeError(f"zstd exited {code}")


@contextmanager
def open_text(path: Path) -> Iterator[TextIO | _ZstdText]:
    if _is_zst(path):
        stream = _ZstdText(path)
        try:
            yield stream
        finally:
            stream.close()
        return
    fh: TextIO
    if path.suffix == ".gz":
        fh = gzip.open(path, "rt", encoding="utf-8")
    else:
        fh = path.open("r", encoding="utf-8")
    try:
        yield fh
    finally:
        fh.close()


def load_creates(
    paths: Iterable[Path],
    *,
    t_min_ms: int | None = None,
    t_max_ms: int | None = None,
    pad_before_ms: int = 2_000,
) -> dict[str, CreateSignal]:
    """Earliest subscribeNewToken create per mint, optionally inside a window."""
    found: dict[str, CreateSignal] = {}
    lo = None if t_min_ms is None else t_min_ms - pad_before_ms
    for path in paths:
        with open_text(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                create = create_from_observe_row(row)
                if create is None:
                    continue
                if lo is not None and create.t_signal_ms < lo:
                    continue
                if t_max_ms is not None and create.t_signal_ms > t_max_ms:
                    continue
                prev = found.get(create.mint)
                if prev is None or create.t_signal_ms < prev.t_signal_ms:
                    found[create.mint] = create
    return found


def row_signature(row: dict[str, Any]) -> str | None:
    sig = row.get("signature")
    if not isinstance(sig, str) or not sig or sig == "UNK":
        return None
    return sig


def row_tx_index(row: dict[str, Any]) -> int:
    raw = row.get("tx_index")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return -1
    return raw


class TxOrder:
    """Position of a signature inside a slot.

    An explicit ``tx_index`` on the first event of a signature wins. Otherwise
    the position is the order that signature was first read in that slot.
    Later events of the same signature share it. ``event_index`` is only the
    order inside one transaction.
    """

    def __init__(self) -> None:
        self._seen: dict[tuple[int, str], int] = {}
        self._next: dict[int, int] = {}

    def position(self, slot: int, signature: str | None, explicit: int | None) -> int:
        if signature:
            found = self._seen.get((slot, signature))
            if found is not None:
                return found
        if explicit is not None and explicit >= 0:
            pos = int(explicit)
            self._next[slot] = max(self._next.get(slot, 0), pos + 1)
        elif signature:
            pos = self._next.get(slot, 0)
            self._next[slot] = pos + 1
        else:
            return -1
        if signature:
            self._seen[(slot, signature)] = pos
        return pos

    def stamp(self, pr: TapePrint) -> TapePrint:
        explicit = pr.tx_index if pr.tx_index >= 0 else None
        pos = self.position(int(pr.slot), pr.signature, explicit)
        if pos == pr.tx_index:
            return pr
        return replace(pr, tx_index=pos)


def _optional_int(row: dict[str, Any], key: str) -> int | None:
    if key not in row or row.get(key) is None:
        return None
    try:
        return int(row[key])
    except (TypeError, ValueError):
        return None


def pumpswap_post_trade_reserves(
    *,
    side: str,
    quote_reserve: int,
    base_reserve: int,
    sol_lamports: int,
    token_raw: int,
    fee_ppm: int | None = None,
    pool_quote_amount: int | None = None,
    lp_fee: int | None = None,
    protocol_fee: int | None = None,
    creator_fee: int | None = None,
) -> tuple[int, int] | None:
    """PumpSwap event reserves are the pool before that trade.

    The next print's base moves by exactly this print's token_raw. Quote
    moves by the pool-net amount, not by gross user_quote_amount_in/out.
    Bonding-curve reserves are already post-trade; do not call this for them.
    """
    if token_raw <= 0 or sol_lamports <= 0 or quote_reserve <= 0 or base_reserve <= 0:
        return None
    delta = pumpswap_pool_quote_delta(
        side=side,
        user_quote_lamports=sol_lamports,
        pool_quote_amount=pool_quote_amount,
        lp_fee=lp_fee,
        protocol_fee=protocol_fee,
        creator_fee=creator_fee,
        fee_ppm=fee_ppm,
    )
    if delta is None or delta <= 0:
        return None
    if side == "buy":
        if token_raw >= base_reserve:
            return None
        return quote_reserve + delta, base_reserve - token_raw
    if side == "sell":
        if delta >= quote_reserve:
            return None
        return quote_reserve - delta, base_reserve + token_raw
    return None


def print_from_trade_row(row: dict[str, Any]) -> tuple[str, TapePrint] | None:
    """Return (mint, print) or None if this row cannot price a SOL path."""
    if row.get("type") not in (None, "trade"):
        return None
    mint = row.get("mint")
    if not isinstance(mint, str) or not mint or mint == "UNK":
        return None
    venue = row.get("venue")
    if venue not in (VENUE_BONDING, VENUE_PUMPSWAP):
        return None
    # PumpSwap reserves are SOL only when the quote mint resolved to wSOL.
    # A missing flag is not assumed to be SOL.
    if venue == VENUE_PUMPSWAP and row.get("quote_is_wsol") is not True:
        return None
    if row.get("quote_is_wsol") is False:
        return None
    try:
        t_ms = int(row["t_recv_ms"])
        quote = int(row["quote_reserve"])
        base = int(row["base_reserve"])
    except (KeyError, TypeError, ValueError):
        return None
    if t_ms < 0 or quote <= 0 or base <= 0:
        return None
    price = _float_or_none(row.get("price_sol"))
    if price is None or price <= 0:
        price = quote / (base * 1000)
    mcap = _float_or_none(row.get("market_cap_sol"))
    if mcap is None or mcap <= 0:
        mcap = price * 1_000_000_000
    try:
        sol_lamports = int(row.get("sol_lamports") or 0)
    except (TypeError, ValueError):
        sol_lamports = 0
    try:
        slot = int(row.get("slot") or 0)
    except (TypeError, ValueError):
        slot = 0
    try:
        event_index = int(row.get("event_index") or 0)
    except (TypeError, ValueError):
        event_index = 0
    side = row.get("side") if row.get("side") in ("buy", "sell") else "buy"
    try:
        token_raw = int(row.get("token_raw") or 0)
    except (TypeError, ValueError):
        token_raw = 0
    # PumpSwap rows store the pool from before this trade. Apply it before
    # anyone can fill, using only fields on this row (no later print).
    if venue == VENUE_PUMPSWAP:
        posted = pumpswap_post_trade_reserves(
            side=side,
            quote_reserve=quote,
            base_reserve=base,
            sol_lamports=sol_lamports,
            token_raw=token_raw,
            fee_ppm=venue_fee_ppm(VENUE_PUMPSWAP, mcap),
            pool_quote_amount=_optional_int(row, "pool_quote_amount"),
            lp_fee=_optional_int(row, "lp_fee"),
            protocol_fee=_optional_int(row, "protocol_fee"),
            creator_fee=_optional_int(row, "creator_fee"),
        )
        if posted is not None:
            quote, base = posted
            price = quote / (base * 1000)
            mcap = price * 1_000_000_000
    return mint, TapePrint(
        t_recv_ms=t_ms,
        slot=slot,
        event_index=event_index,
        venue=venue,
        side=side,
        sol_lamports=max(0, sol_lamports),
        quote_reserve=quote,
        base_reserve=base,
        price_sol=price,
        market_cap_sol=mcap,
        signature=row_signature(row),
        tx_index=row_tx_index(row),
    )


def _sort_key(p: TapePrint) -> tuple[int, int, int, int]:
    return (p.t_recv_ms, p.slot, p.tx_index, p.event_index)


def _dedupe_sorted(prints: list[TapePrint]) -> list[TapePrint]:
    """Drop a trade written twice across a rotation boundary."""
    if len(prints) < 2:
        return prints
    out: list[TapePrint] = []
    prev: tuple[Any, ...] | None = None
    for pr in prints:
        key = (
            pr.t_recv_ms,
            pr.slot,
            pr.tx_index,
            pr.event_index,
            pr.signature,
            pr.venue,
            pr.quote_reserve,
            pr.base_reserve,
        )
        if key == prev:
            continue
        prev = key
        out.append(pr)
    return out


def finalize_prints(prints: list[TapePrint]) -> list[TapePrint]:
    """Read order in. Fill order is (time, slot, tx position, event index)."""
    order = TxOrder()
    stamped = [order.stamp(p) for p in prints]
    return _dedupe_sorted(sorted(stamped, key=_sort_key))


def collapse_fillable(prints: Sequence[TapePrint]) -> list[TapePrint]:
    """One fillable print per signature: reserves after the last inner event.

    The pool is visible only at the latest receive time of that signature, so
    a paper fill cannot land between two instructions of the same transaction.
    Prints with no signature stay as their own states.
    """
    if not prints or all(pr.signature is None for pr in prints):
        return list(prints) if not isinstance(prints, list) else prints
    groups: dict[tuple[int, str], list[TapePrint]] = {}
    order_keys: list[tuple[int, str]] = []
    solo: list[TapePrint] = []
    for pr in prints:
        sig = pr.signature
        if not sig:
            solo.append(pr)
            continue
        key = (pr.slot, sig)
        bucket = groups.get(key)
        if bucket is None:
            groups[key] = [pr]
            order_keys.append(key)
        else:
            bucket.append(pr)
    out: list[TapePrint] = list(solo)
    for key in order_keys:
        events = groups[key]
        last = max(events, key=lambda pr: (pr.tx_index, pr.event_index))
        visible = max(pr.t_recv_ms for pr in events)
        if last.t_recv_ms != visible:
            last = replace(last, t_recv_ms=visible)
        out.append(last)
    out.sort(key=_sort_key)
    return out


def fillable_prints(path: MintPath) -> list[TapePrint]:
    """Cached fillable view. Invalid when the print list object or length changes."""
    prints = path.prints
    cached = path.__dict__.get("_fillable")
    if isinstance(cached, tuple) and len(cached) == 3 and cached[0] is prints and cached[1] == len(prints):
        return cached[2]
    built = collapse_fillable(prints)
    path.__dict__["_fillable"] = (prints, len(prints), built)
    return built


def build_paths(
    creates: dict[str, CreateSignal],
    trade_rows: Iterable[dict[str, Any]],
) -> dict[str, MintPath]:
    """In-memory builder. Prints for mints that are not creates are ignored."""
    buckets: dict[str, list[TapePrint]] = {mint: [] for mint in creates}
    for row in trade_rows:
        parsed = print_from_trade_row(row)
        if parsed is None:
            continue
        mint, pr = parsed
        bucket = buckets.get(mint)
        if bucket is not None:
            bucket.append(pr)
    paths: dict[str, MintPath] = {}
    for mint, create in creates.items():
        paths[mint] = MintPath(create=create, prints=finalize_prints(buckets[mint]))
    return paths


def stream_paths(creates: dict[str, CreateSignal], tape_paths: Iterable[Path]) -> tuple[dict[str, MintPath], ScanStats]:
    """One pass over tape files. Keeps prints only for `creates`."""
    buckets: dict[str, list[TapePrint]] = {mint: [] for mint in creates}
    stats = ScanStats()
    wanted = buckets
    for path in tape_paths:
        with open_text(path) as fh:
            for line in fh:
                stats.lines += 1
                if stats.lines % 250_000 == 0:
                    print(f"tape_lines={stats.lines} kept={stats.kept}", file=sys.stderr)
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    stats.bad_json += 1
                    continue
                if not isinstance(row, dict):
                    stats.bad_json += 1
                    continue
                t_raw = row.get("t_recv_ms")
                if isinstance(t_raw, int):
                    stats.observe_t(t_raw)
                # False, or a PumpSwap row with the flag missing (hourly files
                # after rotation). Neither is priced as SOL.
                flag = row.get("quote_is_wsol")
                if flag is False or (row.get("venue") == "pumpswap" and flag is not True):
                    stats.non_wsol += 1
                    continue
                mint = row.get("mint")
                if mint not in wanted:
                    if not mint or row.get("mint_source") == "unresolved":
                        stats.unresolved += 1
                    else:
                        stats.other_mint += 1
                    continue
                parsed = print_from_trade_row(row)
                if parsed is None:
                    stats.skipped_kept_mint += 1
                    continue
                _, pr = parsed
                wanted[mint].append(pr)
                stats.kept += 1
    paths: dict[str, MintPath] = {}
    for mint, create in creates.items():
        paths[mint] = MintPath(create=create, prints=finalize_prints(wanted[mint]))
    return paths, stats


def peek_trade_bounds(path: Path) -> tuple[int | None, int | None]:
    """First and last t_recv_ms without reading the whole file. Plain JSONL only."""
    if path.suffix == ".gz":
        return None, None
    with path.open("rb") as fh:
        first = fh.readline()
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - 1_000_000))
        tail = fh.read().splitlines()
    return _t_from_raw(first), _last_t(tail)


def _t_from_raw(raw: bytes) -> int | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        row = json.loads(raw)
    except json.JSONDecodeError:
        return None
    t_ms = row.get("t_recv_ms") if isinstance(row, dict) else None
    return t_ms if isinstance(t_ms, int) else None


def _last_t(lines: list[bytes]) -> int | None:
    for raw in reversed(lines):
        t_ms = _t_from_raw(raw)
        if t_ms is not None:
            return t_ms
    return None


def state_as_of(path: MintPath, t_ms: int, *, allow_anchor: bool) -> TapePrint | None:
    """Reserves after the last fillable print at or before t.

    A signature contributes one state: the pool after its last inner event,
    and only once every inner event has been applied. Once a PumpSwap print
    exists, the bonding curve is closed and later bonding prints are not a
    sell venue. The create anchor is used only when the tape has no print
    yet and the caller is still at the signal.
    """
    prints = fillable_prints(path)
    lo, hi = 0, len(prints)
    while lo < hi:
        mid = (lo + hi) // 2
        if prints[mid].t_recv_ms <= t_ms:
            lo = mid + 1
        else:
            hi = mid
    last_bond: TapePrint | None = None
    for i in range(lo - 1, -1, -1):
        pr = prints[i]
        if pr.venue == VENUE_PUMPSWAP:
            return pr
        if last_bond is None and pr.venue == VENUE_BONDING:
            last_bond = pr
    if last_bond is not None:
        return last_bond
    if allow_anchor:
        anchor = path.anchor()
        if anchor is not None and anchor.t_recv_ms <= t_ms:
            return anchor
    return None


def price_path_records(path: MintPath) -> list[dict[str, Any]]:
    """Columnar path rows. Tape trades only, in time order. No future fields."""
    rows: list[dict[str, Any]] = []
    for i, pr in enumerate(path.prints):
        rows.append(
            {
                "schema": "paper_price_path_v1",
                "mint": path.create.mint,
                "t_signal_ms": path.create.t_signal_ms,
                "i": i,
                "t_recv_ms": pr.t_recv_ms,
                "venue": pr.venue,
                "side": pr.side,
                "sol_lamports": pr.sol_lamports,
                "quote_reserve": pr.quote_reserve,
                "base_reserve": pr.base_reserve,
                "price_sol": pr.price_sol,
                "market_cap_sol": pr.market_cap_sol,
                "slot": pr.slot,
                "event_index": pr.event_index,
                "signature": pr.signature,
                "tx_index": pr.tx_index,
            }
        )
    return rows


def iter_price_path_jsonl(paths: Iterable[MintPath]) -> Iterator[str]:
    for path in paths:
        for row in price_path_records(path):
            yield json.dumps(row, separators=(",", ":"), ensure_ascii=False)
