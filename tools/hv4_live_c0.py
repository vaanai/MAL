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
# Pinned by sha256 of the source of each piece AND of every callee that shapes the transaction: the pumpswap_tx helpers (CALLEES_PTX: sell_data, ata,
# pda, create_ata_idempotent, sync_native, close_account, compute_budget_ixs, priority_price_for_total, swap_accounts and its PDA helpers,
# min_out_with_slippage), the pumpswap_tx program/discriminator/CU constants, and probe_live's signing guard (_sign, validate_message).
# `sell_pin_problem` recomputes them; it is checked at STARTUP only (a live hv4_c0 start refuses on any mismatch), not per send: code is not edited
# under a running process, and a changed tree needs a restart, which re-checks. A decode test is the backstop for what the pin cannot see.
# The file sha of probe_live.py is NOT pinned (an unrelated probe edit must not break the canary).
CALLEES_PTX = ("sell_instructions", "sell_data", "ata", "pda", "create_ata_idempotent", "sync_native", "close_account", "compute_budget_ixs",
               "priority_price_for_total", "swap_accounts", "_m", "creator_vault_authority", "event_authority", "fee_config", "pool_v2", "min_out_with_slippage")
CALLEES_PL = ("close_token_account", "validate_message")  # module-level functions of tools/probe_live.py
CONSTANTS_PTX = ("PUMPSWAP_PROGRAM", "FEE_PROGRAM", "GLOBAL_CONFIG", "WSOL_MINT", "TOKEN_PROGRAM", "TOKEN_2022_PROGRAM", "ATA_PROGRAM", "SYSTEM_PROGRAM",
                 "PUMP_PROGRAM", "DISC_SELL", "DEFAULT_SELL_CU_LIMIT")
SELL_BUILDER_PIN: dict[str, str] = {
    "path": "tools/probe_live.py", "symbol": "LiveExecutor._start_sell",
    "symbol_sha256": "a1931a785fe17f130aa18751229e1f7ee2358e9678a3626c7757b8b992deb57e",
    "_sign_sha256": "7cd13e70d5b27dd80c960b412de1b4ea8ae9cd48a95f8c4541677ff7fc9b6e5c",
    "close_token_account_sha256": "93119b2ea159bcb9defcb5e7d2424a5635df432f7ffc9b18d07355d2f364895a",
    "validate_message_sha256": "13e59229628a516a63e5439228bebb9b11cab4c69b6c3db4eb6b04ac72ce487b",
    "ptx.sell_instructions_sha256": "ad40960ef8cc18260f0682cf83989be8ab36f7366354cada1a84367194757eeb",
    "ptx.sell_data_sha256": "78fa481df272f366949b4d5e3d75e882ded7e7a7cbbf57ec9985cd3b94c6ac97",
    "ptx.ata_sha256": "c52d59af62f7820795964c0388b8c3780805ced454aa301bcc5a3b1823a64854",
    "ptx.pda_sha256": "b7296ab8ade148933f9684036d10ffaa2b9e713fb8422cd900fd77f0167532f3",
    "ptx.create_ata_idempotent_sha256": "8a242b5d7bb266d3b3f7e847d78f14d8b46a48971bf7917c59d0dddb86e5522e",
    "ptx.sync_native_sha256": "42c0ed957f2b7df26c6d76eb3f1c96fdb7e350f59e0149b8f371fd32bd6315f6",
    "ptx.close_account_sha256": "cd6c205f3612bdd1409ad6d1e634b0a6409c5f2a6bbff70033271f9962eb3756",
    "ptx.compute_budget_ixs_sha256": "0de51d40730bd5199a56d7bde0f8f291961cb6813b4a665de5119717461a969c",
    "ptx.priority_price_for_total_sha256": "2c9a5e350f7de92a43e6100e836175502600deadb833754eef90c481da3e286d",
    "ptx.swap_accounts_sha256": "88c8d26585820fe3799cf7859128b0236b0d07b3dc4512d1ec2b491e768ad090",
    "ptx._m_sha256": "ccac4adaa98ed9c14820cb6f8414e25d1296b188c159e2c32c9824875cb5e279",
    "ptx.creator_vault_authority_sha256": "ee4e434cdda7771de94cdb1eb6dc739258cf37a54f84b97ba06492ba65bf2ec2",
    "ptx.event_authority_sha256": "f1ad7f6331bd48abd3189647c4df915f1a34d03f5eacf720df725a0ec8b9ac8e",
    "ptx.fee_config_sha256": "6a7ab50d4dc406713f471e492c212ee9b05312b5ef7a5e9eb488448a878434de",
    "ptx.pool_v2_sha256": "28a04b8884d924854d997a7fbcad0c6aac8f36644ca52f6f2bf78a41fa81a40f",
    "ptx.min_out_with_slippage_sha256": "0573c7e1c465a744cbd3d843f6271a109c04f2e21e64bb36d3636e2e71a1c49f",
    "ptx.constants_sha256": "e2b66274132a760ee6d440ae8cbd03294bb0ba29f97a0d8b6aecd2b286dbe79c",
}

REFUSED_ARM_SUFFIXES = ("-L", "-P", "-R")
ALLOWED_ARM_SUFFIXES = ("-F", "-X")
_CONFIG_FORBIDDEN = ("sell_priority_lamports", "escalated_priority_lamports")


def sell_priority(sell_attempts: int, stuck: bool = False) -> int:
    """Priority lamports for a sell: the first attempt is 55,000; any retry (a failed or expired sell, or a stuck position) is the escalated 150,000."""
    return PRIORITY_PIN["escalated"] if (stuck or int(sell_attempts) >= 1) else PRIORITY_PIN["sell"]


def universe_problem(quote_mint: Any, v_lamports: Any) -> str | None:
    """WSOL quote and stored V inside the band. Labels are in SKIP_UNIVERSE_LABELS. At the frozen sha 9ed4c5ff, hv4_cards `filters` apply the same
    v_band [17.5e9, 17.7e9] (inclusive) and a separate `v_missing`, so the canary universe equals the read's. One residual difference: a missing V is
    `v_missing` in HV4 but `universe_v_out_of_range` here (also a logged skip)."""
    if str(quote_mint) != WSOL_MINT:
        return "quote_not_wsol"
    if isinstance(v_lamports, bool) or not isinstance(v_lamports, (int, float)) or not UNIVERSE_V_RANGE[0] <= v_lamports <= UNIVERSE_V_RANGE[1]:
        return "universe_v_out_of_range"
    return None


def frozen_sha_refusal(cfg: Mapping[str, Any]) -> str | None:
    return None if cfg.get("frozen_sha") == FROZEN_SHA else "frozen_sha_not_pinned"


def _sell_pin_items() -> dict[str, Any]:
    """name -> callable returning the sha256 of the current code. Imports the solana stack."""
    from tools import probe_live as pl
    from tools import pumpswap_tx as ptx
    sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
    items: dict[str, Any] = {"symbol_sha256": lambda: sha(inspect.getsource(pl.LiveExecutor._start_sell).encode()),
                             "_sign_sha256": lambda: sha(inspect.getsource(pl.LiveExecutor._sign).encode())}
    for n in CALLEES_PL:
        items[f"{n}_sha256"] = (lambda n=n: sha(inspect.getsource(getattr(pl, n)).encode()))
    for n in CALLEES_PTX:
        items[f"ptx.{n}_sha256"] = (lambda n=n: sha(inspect.getsource(getattr(ptx, n)).encode()))
    items["ptx.constants_sha256"] = lambda: sha(repr([(n, getattr(ptx, n)) for n in CONSTANTS_PTX]).encode())
    return items


def sell_pin_problem(pin: Mapping[str, str] | None = None) -> str | None:
    """Recompute SELL_BUILDER_PIN from the code on disk. None when all match; else the first item that differs (also an item missing from the pin).
    Imports the solana stack, so a dry bridge never calls it. Startup check only."""
    pin = pin or SELL_BUILDER_PIN
    for k, fn in _sell_pin_items().items():
        try:
            if fn() != pin.get(k):
                return f"sell_builder_pin_mismatch:{k}"
        except (OSError, TypeError, AttributeError):
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


SYNTHETIC_MARKER = "SYNTHETIC_FIXTURE_ROOT"
SEALED_ROOT_PREFIX = "/data/mal/arena"


def _under_sealed(path: str) -> bool:
    import os
    real = os.path.realpath(path)
    return real == SEALED_ROOT_PREFIX or real.startswith(SEALED_ROOT_PREFIX + "/")


def decisions_path_refusal(root: str, bot_id: str | None) -> str | None:
    """QP PR1 condition 1: a marked synthetic root can still hold a symlink into the arena. Refuse when the realpath of `<root>/decisions/<bot_id>`, or of
    any day file in it, lies under the sealed arena prefix (no PASS is recorded; none exists yet). Reads directory entry names and resolves links only;
    it opens no file. Fail closed: an unlistable decisions dir is not a pass."""
    import os
    if not bot_id:
        return None
    d = os.path.join(root, "decisions", bot_id)
    if _under_sealed(d):
        return LIVE_REFUSAL
    try:
        names = os.listdir(d) if os.path.isdir(d) else []
    except OSError:
        return LIVE_REFUSAL
    return LIVE_REFUSAL if any(_under_sealed(os.path.join(d, n)) for n in names) else None


def start_refusal(root: str, live: bool, bot_id: str | None = None) -> str | None:
    """CLI start gate (QP-RULING-1025 R2.1: no bridge process, dry or live, reads an HV4 decision row before a recorded read PASS; no gate exists yet).
    Refuse every start, except a dry start on a root that carries the explicit marker file and is not under /data/mal/arena (allowlist; a resolved
    path under the arena is always refused, marker or not). Only checks paths; it opens nothing in the root."""
    import os
    real = os.path.realpath(root)
    if real == SEALED_ROOT_PREFIX or real.startswith(SEALED_ROOT_PREFIX + "/") or os.path.realpath(os.path.abspath(root)).startswith(SEALED_ROOT_PREFIX + "/"):
        return LIVE_REFUSAL
    if live or not os.path.isfile(os.path.join(real, SYNTHETIC_MARKER)):
        return LIVE_REFUSAL
    return decisions_path_refusal(real, bot_id)


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
