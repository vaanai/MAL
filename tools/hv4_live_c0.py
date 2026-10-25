"""hv4_live_c0: the compiled DEC-032 C0 canary limits, stake rule and fee pin for an HV4 arm on `tools/arena_live_bridge`. PR 1 of the HV4 live adapter.

Pure functions and constants. No network, no key, no file read, no HV4 ledger. Installs nothing. The limits are CODE CONSTANTS: a config may only
restate or tighten them (`config_refusal`), never loosen. Scope doc: ARTIFACTS/lab/c0-readiness-2026-10-25.md section 7, items 1 to 5 and 9 (PR 2 adds the frozen_sha pin, the universe check, the
sell builder pin and the sell priority escalation).

Arms: only F or X arms are canary candidates (the L arms rewrite their playbook nightly = a prompt change = a new bot_id under DEC-032 s1.4). A bot_id
ending in -L, -P or -R is refused.

Stake: C0 stake S = min(card stake, 0.05 SOL) regardless of the HV4 size (0.25 / 0.5 / 1.0 in `hv4_arms.SIZES`, which are SOL stakes on the paper card).
The paper twin is priced at the same S.

Fee: the pin is the compiled default 55,000 buy / 55,000 sell / 150,000 escalated priority (manager decision 2026-10-25: C0 uses the default, not
p155; it equals h5_executor.H5_DEFAULT and a test asserts that). A config naming any other fee is refused.
"""

from __future__ import annotations

import hashlib
import inspect
from typing import Any, Iterable, Mapping

PROFILE = "hv4_c0"
TIER_NAME = "T1"  # the TIER file must read exactly this for a C0 start (as the arena H5 canary)

C0_LIMITS: dict[str, int] = {  # DEC-032 s3 C0 column, lamports. total_loss_lamports counts committed loss (realized loss plus open stakes).
    "stake_lamports": 50_000_000,
    "max_open": 2,
    "max_trades_per_day": 30,
    "daily_loss_lamports": 100_000_000,
    "total_loss_lamports": 200_000_000,
}
FUNDED_MIN_LAMPORTS, FUNDED_MAX_LAMPORTS = 350_000_000, 1_000_000_000
FLOOR_BELOW_FUNDED_LAMPORTS = 250_000_000
MIN_WALLET_FLOOR_LAMPORTS = 50_000_000
BASE_FEE_LAMPORTS = 5_000
LAMPORTS_PER_SOL = 1_000_000_000
HV4_SIZES = (0.25, 0.5, 1.0)  # mirrors tools/hv4_arms.SIZES (a test compares them; not imported so this module stays light)

PRIORITY_PIN: dict[str, Any] = {"profile": "default", "buy": 55_000, "sell": 55_000, "escalated": 150_000}
# Reserves derived from the pin (base fee on top of each send): one trip = buy + one escalated sell; an open position keeps one escalated sell reserved.
TRIP_FEE_RESERVE_LAMPORTS = (PRIORITY_PIN["buy"] + BASE_FEE_LAMPORTS) + (PRIORITY_PIN["escalated"] + BASE_FEE_LAMPORTS)
SELL_FEE_RESERVE_LAMPORTS = PRIORITY_PIN["escalated"] + BASE_FEE_LAMPORTS

FROZEN_SHA = "9ed4c5ff63935696b2a89150e13565e123047754"  # the HV4 runner checkout the decision code runs at; the `start` row must repeat it (else latch frozen_sha_mismatch)
UNIVERSE_V_RANGE = (17_500_000_000, 17_700_000_000)  # lamports; same band as tools/arena_live_bridge.UNIVERSE_V_RANGE (a test compares them)
WSOL_MINT = "So11111111111111111111111111111111111111112"
SKIP_UNIVERSE_LABELS = ("universe_v_out_of_range", "quote_not_wsol")  # a universe skip is a SKIP (logged `universe_skip`), never a cap or a refusal count

# The sell + close of the token ATA in ONE transaction is built by the unmodified probe_live.LiveExecutor._start_sell (tx.sell_instructions: budget,
# WSOL ATA, PumpSwap sell, WSOL close; then probe_live.close_token_account(base_ata, user, base_tp) with base_tp = the mint's own token program).
# Pinned by sha256 of the source of each piece; `sell_pin_problem` recomputes them and a live hv4_c0 start refuses on any mismatch. The file sha of
# probe_live.py is NOT pinned (an unrelated probe edit must not break the canary); only the three symbols the sell path runs through are.
SELL_BUILDER_PIN: dict[str, str] = {
    "path": "tools/probe_live.py", "symbol": "LiveExecutor._start_sell",
    "symbol_sha256": "a1931a785fe17f130aa18751229e1f7ee2358e9678a3626c7757b8b992deb57e",
    "close_token_account_sha256": "93119b2ea159bcb9defcb5e7d2424a5635df432f7ffc9b18d07355d2f364895a",  # tools/probe_live.py close_token_account
    "sell_instructions_sha256": "ad40960ef8cc18260f0682cf83989be8ab36f7366354cada1a84367194757eeb",  # tools/pumpswap_tx.py sell_instructions
}

REFUSED_ARM_SUFFIXES = ("-L", "-P", "-R")
ALLOWED_ARM_SUFFIXES = ("-F", "-X")
_CONFIG_FORBIDDEN = ("sell_priority_lamports", "escalated_priority_lamports")


def sell_priority(sell_attempts: int, stuck: bool = False) -> int:
    """Priority lamports for a sell: the first attempt is 55,000; any retry (a failed or expired sell, or a stuck position) is the escalated 150,000."""
    return PRIORITY_PIN["escalated"] if (stuck or int(sell_attempts) >= 1) else PRIORITY_PIN["sell"]


def universe_problem(quote_mint: Any, v_lamports: Any) -> str | None:
    """WSOL quote and stored V inside the band. Labels are in SKIP_UNIVERSE_LABELS. HV4's own `filters` check PumpSwap + WSOL only, so the canary's
    universe (this band) is NARROWER than the read's: a DEC copy must declare the subset."""
    if str(quote_mint) != WSOL_MINT:
        return "quote_not_wsol"
    if isinstance(v_lamports, bool) or not isinstance(v_lamports, (int, float)) or not UNIVERSE_V_RANGE[0] <= v_lamports <= UNIVERSE_V_RANGE[1]:
        return "universe_v_out_of_range"
    return None


def frozen_sha_refusal(cfg: Mapping[str, Any]) -> str | None:
    return None if cfg.get("frozen_sha") == FROZEN_SHA else "frozen_sha_not_pinned"


def sell_pin_problem(pin: Mapping[str, str] | None = None) -> str | None:
    """Recompute SELL_BUILDER_PIN from the code on disk. None when all match; else the first item that differs. Imports the solana stack, so a dry
    bridge never calls it."""
    from tools import probe_live as pl
    from tools import pumpswap_tx as ptx
    pin = pin or SELL_BUILDER_PIN
    sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
    got = {"symbol_sha256": lambda: sha(inspect.getsource(pl.LiveExecutor._start_sell).encode()),
           "close_token_account_sha256": lambda: sha(inspect.getsource(pl.close_token_account).encode()),
           "sell_instructions_sha256": lambda: sha(inspect.getsource(ptx.sell_instructions).encode())}
    for k, fn in got.items():
        try:
            if fn() != pin.get(k):
                return f"sell_builder_pin_mismatch:{k}"
        except (OSError, TypeError):
            return f"sell_builder_pin_unreadable:{k}"
    return None


def arm_refusal(bot_id: Any) -> str | None:
    """Only an F or X arm. Anything ending -L, -P or -R, or not ending -F/-X, is refused."""
    if not isinstance(bot_id, str) or not bot_id.startswith("hv4-"):
        return "arm_not_hv4"
    if bot_id.endswith(REFUSED_ARM_SUFFIXES):
        return "arm_not_canary_candidate"
    return None if bot_id.endswith(ALLOWED_ARM_SUFFIXES) else "arm_not_f_or_x"


def funded_refusal(funded: Any) -> str | None:
    if funded is None:
        return "funded_lamports_missing"
    if isinstance(funded, bool) or not isinstance(funded, int) or not FUNDED_MIN_LAMPORTS <= funded <= FUNDED_MAX_LAMPORTS:
        return "funded_lamports_out_of_range"
    return None


def wallet_floor_lamports(funded: int, configured: int = 0) -> int:
    """max(configured, 0.05 SOL, funded - 0.25 SOL): config can raise the floor, never lower it."""
    return max(int(configured), MIN_WALLET_FLOOR_LAMPORTS, funded - FLOOR_BELOW_FUNDED_LAMPORTS)


def priority_refusal(cfg: Mapping[str, Any]) -> str | None:
    """The pin is compiled. A config may restate it (`priority_pin`, `buy_priority_lamports`) and nothing else."""
    decl = cfg.get("priority_pin")
    if decl is not None and decl != PRIORITY_PIN:
        return "priority_pin_mismatch"
    for k in _CONFIG_FORBIDDEN:
        if k in cfg:
            return f"{k}_not_configurable"
    if "buy_priority_lamports" in cfg and cfg["buy_priority_lamports"] != PRIORITY_PIN["buy"]:
        return "buy_priority_lamports_not_pin"
    if "priority_lamports" in cfg and cfg["priority_lamports"] != PRIORITY_PIN["buy"]:
        return "priority_lamports_not_pin"
    return None


def config_refusal(cfg: Mapping[str, Any], live: bool) -> str | None:
    """The first reason a C0 config is refused, else None. `funded_lamports` is required when live."""
    why = arm_refusal(cfg.get("bot_id")) or frozen_sha_refusal(cfg) or priority_refusal(cfg)
    if why:
        return why
    if live or cfg.get("funded_lamports") is not None:
        return funded_refusal(cfg.get("funded_lamports"))
    return None


def guard_problem() -> str | None:
    """Tripwire run at a C0 start: the compiled constants are still at or inside DEC-032 s3. None when they hold; it only refuses."""
    spec = {"stake_lamports": 50_000_000, "max_open": 2, "max_trades_per_day": 30, "daily_loss_lamports": 100_000_000, "total_loss_lamports": 200_000_000}
    for k, cap in spec.items():
        v = C0_LIMITS.get(k)
        if isinstance(v, bool) or not isinstance(v, int) or not 0 < v <= cap:
            return f"c0_guard:{k}"
    checks = (
        ("funded_range", 350_000_000 <= FUNDED_MIN_LAMPORTS <= FUNDED_MAX_LAMPORTS <= 1_000_000_000),
        ("floor_below_funded", 0 < FLOOR_BELOW_FUNDED_LAMPORTS <= 250_000_000),
        ("tier", TIER_NAME == "T1"),
        ("priority_pin", (PRIORITY_PIN["buy"], PRIORITY_PIN["sell"], PRIORITY_PIN["escalated"]) == (55_000, 55_000, 150_000)),
        ("fee_reserve", TRIP_FEE_RESERVE_LAMPORTS == 215_000 and SELL_FEE_RESERVE_LAMPORTS == 155_000),
        ("frozen_sha", isinstance(FROZEN_SHA, str) and len(FROZEN_SHA) == 40 and FROZEN_SHA.startswith("9ed4c5ff")),
        ("daily_le_total", C0_LIMITS["daily_loss_lamports"] <= C0_LIMITS["total_loss_lamports"]),
    )
    for name, ok in checks:
        if not ok:
            return f"c0_guard:{name}"
    return None


def live_ok_problem(text: str, bot_id: str) -> str | None:
    """LIVE_OK names the bot_id (exactly one line)."""
    lines = text.strip().splitlines()
    return None if len(lines) == 1 and lines[0].strip() == bot_id else "live_ok_other_book"


def c0_stake(row: Mapping[str, Any], cap_lamports: int = C0_LIMITS["stake_lamports"]) -> tuple[int | None, dict[str, Any]]:
    """(stake lamports or None, mapping record). S = min(card stake, cap) whatever the size is. The card stake is the entry row's `stake_sol`
    (falls back to `size`); a `size` that is not one of 0.25 / 0.5 / 1.0, or a card stake that is missing or not positive, is refused (None)."""
    cap = min(int(cap_lamports), C0_LIMITS["stake_lamports"])
    card = row.get("stake_sol", row.get("size"))
    size = row.get("size")
    rec: dict[str, Any] = {"card_stake_sol": card, "size": size, "cap_lamports": cap}
    if size is not None and (isinstance(size, bool) or not isinstance(size, (int, float)) or float(size) not in HV4_SIZES):
        rec["refused"] = "size_invalid"
        return None, rec
    if isinstance(card, bool) or not isinstance(card, (int, float)) or card != card or card <= 0:
        rec["refused"] = "card_stake_invalid"
        return None, rec
    card_lamports = int(round(float(card) * LAMPORTS_PER_SOL))
    s = min(card_lamports, cap)
    rec["stake_lamports"] = s
    rec["twin_stake_lamports"] = s  # the paper twin is priced at S, equal by construction
    rec["clamped"] = card_lamports > cap
    return s, rec


LIVE_REFUSAL = "hv4_c0_incomplete"  # a live start of this profile is refused until a later PR (sell escalation, parity pricing) removes it


def position_worst_case_lamports(stake: int, rent_reserve: int) -> int:
    """One position's worst case, the terms the wallet-floor check uses (`TRIP_FEE_RESERVE_LAMPORTS`, the bridge's rent reserve,
    `SELL_FEE_RESERVE_LAMPORTS`): stake at zero + buy fee + rent reserve + reserved sell fee. Applies to an open, pending or in-flight buy and to the new buy."""
    return int(stake) + TRIP_FEE_RESERVE_LAMPORTS + int(rent_reserve) + SELL_FEE_RESERVE_LAMPORTS


def committed_loss_lamports(realized_loss: int, open_stakes: Iterable[int], rent_reserve: int) -> int:
    """DEC-032 s3 (Total stop includes open exposure), quoted: "committed loss = realized loss since the first send (floored at 0) + every open or
    pending position's stake at zero + its buy fee + rent reserve + reserved sell fees + every in-flight buy's worst case." Open and in-flight
    orders are one list here (the bridge's `open_orders` includes buy_sending), each at `position_worst_case_lamports`."""
    return max(0, int(realized_loss)) + sum(position_worst_case_lamports(s, rent_reserve) for s in open_stakes)


def total_stop_problem(realized_loss: int, open_stakes: Iterable[int], new_stake: int, rent_reserve: int) -> str | None:
    """DEC-032 s3, quoted: "No new buy is sent unless committed loss plus that buy's worst case is at most the total stop." (0.20 SOL at C0.)
    The daily stop is not touched: DEC-032 s3 defines it as "realized loss in a UTC day" only, which the bridge already checks."""
    need = committed_loss_lamports(realized_loss, open_stakes, rent_reserve) + position_worst_case_lamports(new_stake, rent_reserve)
    return "total_loss_committed" if need > C0_LIMITS["total_loss_lamports"] else None
