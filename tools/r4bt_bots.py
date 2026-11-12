"""r4bt_bots: the per-bot identity and clock table of the r4-b-T gate, pricer and read tools. PAPER ONLY. No key, no network, no tape.

One row per registered successor of the r4-b-T rule: `r4bt-a1` (DEC-031 Amendment 27) and `r4bt-a2` (DEC-031 Amendment 30, draft). The decision code is the same
for both; only the bot id, the clock and the fixed instants differ. `tools/r4bt_gate.py` and `tools/r4bt_price.py` are NOT edited (the C0 twin pins their blobs,
`tools/r4bt_c0_twin.py` MODULE_BLOBS) and keep their `r4bt-a1` defaults; `tools/r4bt_attempts.py` (a pinned counter) is not edited either. The read, confirm and A3
mains take `--bot-id` (default `r4bt-a1`, so a1 inputs behave byte-for-byte as before); `tools/r4bt_price_bot.py` binds the same flag for the pricer.

Nothing here says r4bt-a1 or r4bt-a2 makes money. No book has cleared the promotion gate.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from tools import r4bt_gate as G

DEFAULT_BOT_ID = "r4bt-a1"
DAY_MS = 86_400_000
P0_FLOOR_MEAN = 2.0  # DEC-031 Amendment 27 item 15 / Amendment 30 item 7: "a mean of at least 2.0 a day and no day at 0"
H_MIN_DAYS, H_MAX_DAYS, H_NUM = 14, 90, 150  # Amendment 4 item 2.2; Amendment 27 item 16; Amendment 30 item 7
T_MAX_S = 176_460  # Amendment 27 item 21 / Amendment 30 item 11

BOTS: Mapping[str, Mapping[str, Any]] = {
    "r4bt-a1": {
        "clock_ms": G.CLOCK_MS,  # 2026-11-07T06:00:00Z, the gate's own constant (a1 stays on it)
        "p0_days": ("2026-11-04", "2026-11-05", "2026-11-06"),  # the ruling QP-RULING-1107-r4bt-p0-fail section 0: P0 FAIL {1, 0, 0}
    },
    "r4bt-a2": {
        "clock_ms": 1_795_132_800_000,  # 2026-11-20T00:00:00Z (Amendment 30 item 9)
        "p0_days": ("2026-11-16", "2026-11-17", "2026-11-18"),  # Amendment 30 item 7
        "p0_count_job_ms": 1_795_047_000_000,  # 2026-11-19T00:10:00Z
        "counted_end_h14_ms": 1_796_342_400_000,  # clock + 14 d = 2026-12-04T00:00:00Z
        "screen_boundary_min_ms": 1_795_564_800_000,  # 2026-11-25T00:00:00Z (dates 11-20 to 11-24)
    },
}


def spec(bot_id: str) -> Mapping[str, Any]:
    """The row of a registered bot, or a refusal. An unknown bot id never falls back to another's constants."""
    if not isinstance(bot_id, str) or bot_id not in BOTS:
        raise G.Refused(f"bot id {bot_id!r} is not in the r4-b-T table {sorted(BOTS)}")
    return BOTS[bot_id]


def check_clock(bot_id: str, clock_ms: int) -> None:
    """Refuse a registry clock that is not the table's clock for this bot."""
    want = spec(bot_id)["clock_ms"]
    if int(clock_ms) != want:
        raise G.Refused(f"{bot_id}: the registry clock_start_ms {clock_ms} is not the registered {want}")


def p0_days_problem(bot_id: str, days: Sequence[str]) -> str | None:
    want = list(spec(bot_id)["p0_days"])
    return None if list(days) == want else f"{bot_id}: P0 days {list(days)} are not the named {want}"


def h_days(total: int, n_days: int) -> int | None:
    """H = min(90, max(14, ceil(150 / rate))), rate = total / n_days, in integers (the same arithmetic as tools/r4bt_attempts.h_days)."""
    if total <= 0:
        return None
    return min(H_MAX_DAYS, max(H_MIN_DAYS, -(-(H_NUM * n_days) // total)))


def p0_floor_ok(per_day: Mapping[str, int]) -> bool:
    """Mean of at least 2.0 a day and no day at 0."""
    n = len(per_day)
    return n > 0 and sum(per_day.values()) / n >= P0_FLOOR_MEAN and min(per_day.values()) > 0


def day_ms(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
