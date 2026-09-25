"""Crowd / momentum scorer: N distinct non-vetoed wallets buy inside W seconds.

Sniper prints (Δslot ≤ 2) never count. Wallets tagged sniper_bundler or bot
on the same tape (full-window, same lookahead as the noisy_v0 board) are
excluded. Creators are excluded. First crossing per mint.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Mapping, Sequence

from tools.paper_signal_core import Signal
from tools.wallet_leaderboard import SNIPER_MAX_DELTA_SLOTS, Trade

CROWD_N = (3, 5, 8)
CROWD_W_S = (1, 2, 5)


def create_slot_by_mint(trades: Sequence[Trade]) -> dict[str, int]:
    first: dict[str, int] = {}
    for tr in trades:
        prev = first.get(tr.mint)
        if prev is None or tr.slot < prev:
            first[tr.mint] = tr.slot
    return first


def creator_by_mint(trades: Sequence[Trade], overlay: Mapping[str, str] | None = None) -> dict[str, str]:
    """First bonding buyer is the create-proxy unless observe overlay has creator."""
    out: dict[str, str] = dict(overlay or {})
    seen: set[str] = set(out)
    for tr in trades:
        if tr.mint in seen:
            continue
        if tr.venue == "pump_bonding" and tr.side == "buy":
            out[tr.mint] = tr.trader
            seen.add(tr.mint)
    return out


def veto_sniper_bot_wallets(trades: Sequence[Trade], create_slots: Mapping[str, int]) -> set[str]:
    """Cheap full-window sniper/bot veto. Not a 30d research board."""
    buys: dict[str, int] = defaultdict(int)
    sniper_buys: dict[str, int] = defaultdict(int)
    first_buy: dict[tuple[str, str], int] = {}
    first_sell: dict[tuple[str, str], int] = {}
    for tr in trades:
        slot0 = create_slots.get(tr.mint, tr.slot)
        if tr.side == "buy":
            buys[tr.trader] += 1
            if tr.slot - slot0 <= SNIPER_MAX_DELTA_SLOTS:
                sniper_buys[tr.trader] += 1
            key = (tr.trader, tr.mint)
            if key not in first_buy:
                first_buy[key] = tr.t_ms
        elif tr.side == "sell":
            key = (tr.trader, tr.mint)
            if key not in first_sell:
                first_sell[key] = tr.t_ms
    vetoed: set[str] = set()
    short_holds: dict[str, list[bool]] = defaultdict(list)
    for key, buy_t in first_buy.items():
        trader, _mint = key
        sell_t = first_sell.get(key)
        if sell_t is None:
            continue
        short_holds[trader].append(sell_t - buy_t < 10_000)
    for trader, n_buy in buys.items():
        if n_buy <= 0:
            continue
        if sniper_buys[trader] / n_buy >= 0.50:
            vetoed.add(trader)
        holds = short_holds.get(trader) or []
        if len(holds) >= 3 and sum(1 for x in holds if x) / len(holds) >= 0.50:
            vetoed.add(trader)
    return vetoed


def emit_crowd_signals(
    trades: Sequence[Trade],
    *,
    n: int,
    window_ms: int,
    excluded_wallets: set[str],
    create_slots: Mapping[str, int],
    creators: Mapping[str, str],
    allowed_mints: set[str] | None = None,
) -> list[Signal]:
    """Fire at the buy that first makes N distinct eligible wallets inside W."""
    by_mint: dict[str, list[Trade]] = defaultdict(list)
    for tr in trades:
        if tr.side != "buy":
            continue
        if allowed_mints is not None and tr.mint not in allowed_mints:
            continue
        by_mint[tr.mint].append(tr)
    out: list[Signal] = []
    for mint, prints in by_mint.items():
        slot0 = create_slots.get(mint)
        creator = creators.get(mint)
        window: deque[tuple[int, str]] = deque()
        seen_now: dict[str, int] = {}
        fired = False
        for tr in prints:
            if slot0 is not None and tr.slot - slot0 <= SNIPER_MAX_DELTA_SLOTS:
                continue
            if tr.trader in excluded_wallets:
                continue
            if creator is not None and tr.trader == creator:
                continue
            t = tr.t_ms
            window.append((t, tr.trader))
            seen_now[tr.trader] = seen_now.get(tr.trader, 0) + 1
            cutoff = t - window_ms
            while window and window[0][0] < cutoff:
                _old_t, old_w = window.popleft()
                left = seen_now.get(old_w, 1) - 1
                if left <= 0:
                    seen_now.pop(old_w, None)
                else:
                    seen_now[old_w] = left
            if len(seen_now) >= n:
                out.append(
                    Signal(
                        mint=mint,
                        signal_t_ms=t,
                        family="crowd",
                        variant=f"n{n}_w{window_ms // 1000}s",
                        features={
                            "kind": "crowd",
                            "n": n,
                            "window_ms": window_ms,
                            "unique_wallets": len(seen_now),
                            "delta_ms_from_create_proxy": None if slot0 is None else (tr.slot - slot0),
                            "trigger_wallet": tr.trader,
                            "excluded_sniper_print": True,
                        },
                    )
                )
                fired = True
                break
        _ = fired
    out.sort(key=lambda s: (s.signal_t_ms, s.mint))
    return out


def crowd_books(
    trades: Sequence[Trade],
    *,
    excluded_wallets: set[str],
    create_slots: Mapping[str, int],
    creators: Mapping[str, str],
    allowed_mints: set[str] | None = None,
    ns: Sequence[int] = CROWD_N,
    windows_s: Sequence[int] = CROWD_W_S,
) -> list[tuple[str, float, list[Signal]]]:
    books: list[tuple[str, float, list[Signal]]] = []
    for n in ns:
        for w in windows_s:
            sigs = emit_crowd_signals(
                trades,
                n=n,
                window_ms=int(w * 1000),
                excluded_wallets=excluded_wallets,
                create_slots=create_slots,
                creators=creators,
                allowed_mints=allowed_mints,
            )
            books.append((f"n{n}_w{w}s", 1.0, sigs))
    return books
