"""Follow-signal scorer: buy when a noisy_v0 leaderboard wallet buys.

Reads the JSONL hook from `tools.wallet_leaderboard` (`type=follow_signal`).
`signal_t_ms` is the leader's tape `t_recv_ms`. Fill latency is added on top.

The board is scored on the same short tape (lookahead). Filter
`delta_slot_from_create > 2` for the copyable slice — slot 0–2 is uncopyable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.paper_signal_core import Signal
from tools.wallet_leaderboard import SNIPER_MAX_DELTA_SLOTS

FOLLOW_LATENCIES = (0.5, 1.0, 2.0, 5.0)


def load_follow_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
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
            if row.get("type") not in (None, "follow_signal"):
                continue
            mint = row.get("mint")
            t_ms = row.get("signal_t_ms")
            if not isinstance(mint, str) or not mint:
                continue
            if not isinstance(t_ms, int):
                continue
            rows.append(row)
    rows.sort(key=lambda r: (int(r["signal_t_ms"]), str(r.get("wallet") or ""), r["mint"]))
    return rows


def emit_follow_signals(
    rows: Sequence[Mapping[str, Any]],
    *,
    variant: str,
    copyable_only: bool = False,
    min_delta_slot: int = SNIPER_MAX_DELTA_SLOTS + 1,
    allowed_mints: set[str] | None = None,
) -> list[Signal]:
    """One signal per mint: the earliest leader buy that passes the filter."""
    out: list[Signal] = []
    for row in rows:
        mint = row["mint"]
        if allowed_mints is not None and mint not in allowed_mints:
            continue
        feats = row.get("features") if isinstance(row.get("features"), dict) else {}
        delta = feats.get("delta_slot_from_create")
        if copyable_only:
            if not isinstance(delta, int) or delta < min_delta_slot:
                continue
        wallet = row.get("wallet") if isinstance(row.get("wallet"), str) else None
        features = {
            "kind": "follow",
            "copyable": copyable_only,
            "wallet": wallet,
            "delta_slot_from_create": delta,
            "buyer_rank": feats.get("buyer_rank"),
            "wallet_win_rate": feats.get("wallet_win_rate"),
            "wallet_closed_mints": feats.get("wallet_closed_mints"),
            "wallet_realized_pnl_sol": feats.get("wallet_realized_pnl_sol"),
            "wallet_median_hold_ms": feats.get("wallet_median_hold_ms"),
            "sol": feats.get("sol"),
            "board": feats.get("board"),
            "signal_kind": feats.get("signal_kind"),
            "venue": feats.get("venue"),
        }
        out.append(
            Signal(
                mint=mint,
                signal_t_ms=int(row["signal_t_ms"]),
                family="follow",
                variant=variant,
                features=features,
            )
        )
    return out


def follow_books(
    rows: Sequence[Mapping[str, Any]],
    *,
    allowed_mints: set[str] | None = None,
) -> list[tuple[str, float, list[Signal]]]:
    """(variant, latency_s, signals) for all / copyable × follow latencies."""
    books: list[tuple[str, float, list[Signal]]] = []
    for copyable, tag in ((False, "noisy_v0"), (True, "noisy_v0_copyable")):
        sigs = emit_follow_signals(
            rows,
            variant=tag,
            copyable_only=copyable,
            allowed_mints=allowed_mints,
        )
        for latency in FOLLOW_LATENCIES:
            books.append((tag, latency, sigs))
    return books
