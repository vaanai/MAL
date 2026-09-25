"""Clean-launch scorer: low sniper/bundle share in the first slots + unrugged creator.

Decision clock matches the buy-every baseline (create T, fill T+1s). The
filter only reads prints with t_recv_ms <= T+1s. A prior mint is a rug if
its creator sold within 60s of that mint's first print.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Mapping, Sequence

from tools.paper_price_path import CreateSignal
from tools.paper_signal_core import Signal
from tools.wallet_leaderboard import SNIPER_MAX_DELTA_SLOTS, Trade

SNIPER_SHARE_CAPS = (0.25, 0.50)
CREATOR_DUMP_MS = 60_000
HEADLINE_WAIT_MS = 1_000


def first_print_ms(trades: Sequence[Trade]) -> dict[str, int]:
    out: dict[str, int] = {}
    for tr in trades:
        prev = out.get(tr.mint)
        if prev is None or tr.t_ms < prev:
            out[tr.mint] = tr.t_ms
    return out


def creator_prior_rugs(
    creates: Mapping[str, CreateSignal],
    trades: Sequence[Trade],
    creators: Mapping[str, str],
) -> dict[str, int]:
    """Knowable at create T: how many earlier mints this creator dumped."""
    first_t = first_print_ms(trades)
    dump_mints: set[str] = set()
    for tr in trades:
        if tr.side != "sell":
            continue
        creator = creators.get(tr.mint)
        if creator is None or tr.trader != creator:
            continue
        t0 = first_t.get(tr.mint)
        if t0 is not None and tr.t_ms - t0 <= CREATOR_DUMP_MS:
            dump_mints.add(tr.mint)
    # Map each create → count of that creator's dump mints with earlier T.
    mint_t = {mint: path.t_signal_ms for mint, path in creates.items()}
    by_creator: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for mint, t_ms in mint_t.items():
        cr = creates[mint].creator or creators.get(mint)
        if cr:
            by_creator[cr].append((t_ms, mint))
    for cr, items in by_creator.items():
        items.sort()
    rugs: dict[str, int] = {}
    for mint, t_ms in mint_t.items():
        cr = creates[mint].creator or creators.get(mint)
        n = 0
        if cr:
            for other_t, other_mint in by_creator.get(cr, ()):
                if other_t >= t_ms:
                    break
                if other_mint in dump_mints:
                    n += 1
        rugs[mint] = n
    return rugs


def sniper_share_as_of(
    mint_trades: Sequence[Trade],
    *,
    t_ms: int,
    slot0: int | None,
) -> tuple[float | None, int, int, int]:
    """SOL share of non-creator buys with Δslot ≤ 2 among buys at or before t.

    Returns (share, sniper_lamports, total_lamports, buy_n). share is None
    when there is no eligible volume (cannot verify a clean launch).
    """
    sniper = total = buys = 0
    creator = None
    # First bonding buy is the create-proxy if we need to skip the creator.
    for tr in mint_trades:
        if tr.side == "buy" and tr.venue == "pump_bonding":
            creator = tr.trader
            break
    for tr in mint_trades:
        if tr.t_ms > t_ms:
            break
        if tr.side != "buy":
            continue
        if creator is not None and tr.trader == creator:
            continue
        total += tr.sol_lamports
        buys += 1
        if slot0 is not None and tr.slot - slot0 <= SNIPER_MAX_DELTA_SLOTS:
            sniper += tr.sol_lamports
        elif slot0 is None:
            sniper += tr.sol_lamports
    if total <= 0:
        return None, sniper, total, buys
    return sniper / total, sniper, total, buys


def emit_clean_signals(
    creates: Mapping[str, CreateSignal],
    trades: Sequence[Trade],
    *,
    create_slots: Mapping[str, int],
    creators: Mapping[str, str],
    max_sniper_share: float | None,
    require_no_prior_rug: bool,
    prior_rugs: Mapping[str, int],
    variant: str,
    wait_ms: int = HEADLINE_WAIT_MS,
) -> list[Signal]:
    by_mint: dict[str, list[Trade]] = defaultdict(list)
    for tr in trades:
        if tr.mint in creates:
            by_mint[tr.mint].append(tr)
    out: list[Signal] = []
    for mint, create in creates.items():
        t_asof = create.t_signal_ms + wait_ms
        share, sniper_lp, total_lp, buy_n = sniper_share_as_of(
            by_mint.get(mint) or (),
            t_ms=t_asof,
            slot0=create_slots.get(mint),
        )
        rugs = int(prior_rugs.get(mint, 0))
        if require_no_prior_rug and rugs > 0:
            continue
        if max_sniper_share is not None:
            if share is None or share > max_sniper_share:
                continue
        out.append(
            Signal(
                mint=mint,
                signal_t_ms=create.t_signal_ms,
                family="clean",
                variant=variant,
                features={
                    "kind": "clean_launch",
                    "sniper_share": share,
                    "sniper_lamports": sniper_lp,
                    "early_buy_lamports": total_lp,
                    "early_buy_n": buy_n,
                    "max_sniper_share": max_sniper_share,
                    "creator_prior_rugs": rugs,
                    "require_no_prior_rug": require_no_prior_rug,
                },
            )
        )
    out.sort(key=lambda s: (s.signal_t_ms, s.mint))
    return out


def clean_books(
    creates: Mapping[str, CreateSignal],
    trades: Sequence[Trade],
    *,
    create_slots: Mapping[str, int],
    creators: Mapping[str, str],
) -> list[tuple[str, float, list[Signal]]]:
    rugs = creator_prior_rugs(creates, trades, creators)
    books: list[tuple[str, float, list[Signal]]] = []
    for cap in SNIPER_SHARE_CAPS:
        tag = f"s{int(cap * 100)}_norug"
        sigs = emit_clean_signals(
            creates,
            trades,
            create_slots=create_slots,
            creators=creators,
            max_sniper_share=cap,
            require_no_prior_rug=True,
            prior_rugs=rugs,
            variant=tag,
        )
        books.append((tag, 1.0, sigs))
    books.append(
        (
            "s50",
            1.0,
            emit_clean_signals(
                creates,
                trades,
                create_slots=create_slots,
                creators=creators,
                max_sniper_share=0.50,
                require_no_prior_rug=False,
                prior_rugs=rugs,
                variant="s50",
            ),
        )
    )
    books.append(
        (
            "norug",
            1.0,
            emit_clean_signals(
                creates,
                trades,
                create_slots=create_slots,
                creators=creators,
                max_sniper_share=None,
                require_no_prior_rug=True,
                prior_rugs=rugs,
                variant="norug",
            ),
        )
    )
    return books
