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
import json
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
    "pe.exit_check_sha256": "7dcd289631cfe0418439df90f4b0901518d813dfd474f0cb2a6c660e3a51eb00",  # PR 3: the quote_out that min_out is a share of
    "c0.ladder_sha256": "06123a941d5a842fe9eccaed4277b898fe8c88ba7681c0feee5f059b397f3233",  # PR 3: the compiled 0.85 / 0.65 rungs and their escalation point (this module's own ladder source)
}

REFUSED_ARM_SUFFIXES = ("-L", "-P", "-R")
ALLOWED_ARM_SUFFIXES = ("-F", "-X")
_CONFIG_FORBIDDEN = ("sell_priority_lamports", "escalated_priority_lamports")


ESCALATE_AFTER_REVERTS = 2  # DEC-032 s3: "after 2 reverts or a time bound"
ESCALATE_AFTER_MS = 345_000  # the time bound: s3 names none; C0 takes h5_executor.ESCALATE_S (345 s) from the first sell attempt, a declared choice


def sell_escalated(sell_attempts: int, stuck: bool = False, elapsed_ms: int | None = None) -> bool:
    """The ONE escalation point of a C0 sell (DEC-032 s3): after 2 reverts/expiries, or ESCALATE_AFTER_MS after the first sell build, whichever comes
    first; a stuck position is escalated too (a backstop). The priority and the min_out ladders both read this, so they step together."""
    late = elapsed_ms is not None and int(elapsed_ms) >= ESCALATE_AFTER_MS
    return bool(stuck or late or int(sell_attempts) >= ESCALATE_AFTER_REVERTS)


def sell_priority(sell_attempts: int, stuck: bool = False, elapsed_ms: int | None = None) -> int:
    """Priority lamports for a sell, aligned with DEC-032 s3: 55,000 until the sell has reverted/expired twice (sell_attempts >= 2) or ESCALATE_AFTER_MS
    have passed since the first sell attempt (elapsed_ms), whichever comes first; then the escalated 150,000. A stuck position is escalated too
    (stuck needs more attempts than 2, so it is a backstop). The first retry after ONE revert stays at 55,000."""
    return PRIORITY_PIN["escalated"] if sell_escalated(sell_attempts, stuck, elapsed_ms) else PRIORITY_PIN["sell"]


# DEC-032 s3 sell rule: min_out = 0.85 of the quote at the first build; 0.65 at the escalation point (higher priority); NEVER 0. Compiled constants, in bps of
# the quote that must still be received (the executor's `slip_bps` = 10,000 minus these). Nothing in config can reach them (see `_CONFIG_FORBIDDEN`).
SELL_MIN_OUT_KEEP_BPS: dict[str, int] = {"first": 8_500, "escalated": 6_500}
# DECLARED RESIDUAL (DEC copy): the pinned builder computes quote_out * keep // 10000, so a quote_out of exactly 1 lamport gives min_out 0 at the 0.85 rung. Dust only; the pinned builder is not touched.
SELL_MIN_OUT_FLOOR_BPS = SELL_MIN_OUT_KEEP_BPS["escalated"]  # no sell is ever built below 0.65 of the quote


def sell_keep_bps(sell_attempts: int, stuck: bool = False, elapsed_ms: int | None = None) -> int:
    """Share of the quote (bps) the sell must still receive: 8,500 until the escalation point, then 6,500. Never below SELL_MIN_OUT_FLOOR_BPS, never 0."""
    return SELL_MIN_OUT_KEEP_BPS["escalated" if sell_escalated(sell_attempts, stuck, elapsed_ms) else "first"]


def sell_slip_bps(sell_attempts: int, stuck: bool = False, elapsed_ms: int | None = None) -> int:
    """The `slip_bps` the pinned sell builder takes (`tx.min_out_with_slippage(quote_out, slip_bps)`): 1,500 first, 3,500 escalated."""
    return 10_000 - sell_keep_bps(sell_attempts, stuck, elapsed_ms)


def ladder_pin_sha256() -> str:
    """sha256 of the ladder's compiled source and constants. It sits inside the sell pin (SELL_BUILDER_PIN['c0.ladder_sha256']): editing a rung is a reviewed change."""
    src = "".join(inspect.getsource(f) for f in (sell_escalated, sell_keep_bps, sell_slip_bps)) + repr((SELL_MIN_OUT_KEEP_BPS, ESCALATE_AFTER_REVERTS, ESCALATE_AFTER_MS))
    return hashlib.sha256(src.encode()).hexdigest()


# --- the paper twin (DEC-032 s5 "Twin comparator"): arena_twin_writer re-prices the book's paper position at S, nofail leg, 1.9 s, E1 excluded, at the fees
# the trip actually sent (landed buy fee; landed sell fee plus earlier failed sells, escalated resends included). The pins below are what its run record must carry.
HV4_STACK_PIN = {"e1_floor_per_005": 384022, "exit_lag_s": 0.55, "feed_delay_slots": 7, "guard": 1.15, "h_in": 0.002608, "h_out": 0.0016, "k_rounding": "ceil",
                 "portal_ppm": 0, "press_intercept_provisional": -1.4197242838379789, "rent": 0, "send": 505000, "strand_rule": "both"}  # StackConfig.as_dict() at 9ed4c5ff, delay 7
CONFIG_PIN_SHA256 = "1e611a5a636dfe6bd563ab5258f6b498cdb8906500ee8bf2f0264a2621621986"  # sha256 of HV4_STACK_PIN, sort_keys, compact separators (ARTIFACTS/lab/hv4-registration-1023.md 2.3)
TWIN_WRITER_SHA256 = "1e9cb00e08ec69df269b19b9774d02feed7d3e91a61c7f727b7abbdc5fe560bb"  # sha256 of tools/arena_twin_writer.py (the same writer the H5 arena canary pins)


def stack_sha256(stack: Mapping[str, Any]) -> str:
    """The twin writer's `stack_hash` rule: canonical JSON of the stack block without the per-position `sps_*` slot rates."""
    core = {k: v for k, v in stack.items() if not str(k).startswith("sps_")}
    return hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def config_pin_problem(stack: Any) -> str | None:
    """None when `stack` hashes to the registered HV4 config pin (every key, value for value)."""
    try:
        return None if isinstance(stack, Mapping) and stack_sha256(stack) == CONFIG_PIN_SHA256 else "config_pin_mismatch"
    except (TypeError, ValueError):
        return "config_pin_mismatch"


def twin_pins() -> tuple[str, str]:
    """(config_hash, code_sha256) the twin writer's run record must carry for a C0 book."""
    return CONFIG_PIN_SHA256, TWIN_WRITER_SHA256


def fees_sent(buy_sends: int, sell_priorities: Iterable[int]) -> tuple[int, int]:
    """The twin's per-trip fees from the schedule actually sent: (buy fee, sell fee). A send costs its priority plus the 5,000 base. `buy_sends` is 1 for the
    landed buy (earlier failed buy sends are not part of the trip, as arena_twin_writer's FEE_BASIS says). Every sell send counts, escalated resends included."""
    return buy_sends * (PRIORITY_PIN["buy"] + BASE_FEE_LAMPORTS), sum(int(p) + BASE_FEE_LAMPORTS for p in sell_priorities)


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
    from tools import probe_executor as pe
    from tools import pumpswap_tx as ptx
    sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
    items: dict[str, Any] = {"symbol_sha256": lambda: sha(inspect.getsource(pl.LiveExecutor._start_sell).encode()),
                             "_sign_sha256": lambda: sha(inspect.getsource(pl.LiveExecutor._sign).encode())}
    for n in CALLEES_PL:
        items[f"{n}_sha256"] = (lambda n=n: sha(inspect.getsource(getattr(pl, n)).encode()))
    for n in CALLEES_PTX:
        items[f"ptx.{n}_sha256"] = (lambda n=n: sha(inspect.getsource(getattr(ptx, n)).encode()))
    items["pe.exit_check_sha256"] = lambda: sha(inspect.getsource(pe.exit_check).encode())
    items["c0.ladder_sha256"] = ladder_pin_sha256
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


# --- PR4 gates: read record, owner line, model id, per-trade loss, 14-day window ---------------------------------------------------------------------------
MODEL_ID = "claude-haiku-5-5"  # the one model id an F arm may have been read under (the read record's `model_ids`)
OWNER_DEC_PATH = "/etc/mal-hv4-c0/CANARY_DEC.md"  # ROOT-OWNED, outside the installed tree (the Q50 re-install problem: a DEC/ file in the tree is overwritten by a deploy)
CANARY_DEC_SHA256 = ""  # sha256 of the owner's CANARY_DEC.md blob; set by the reviewed PR that records the owner's confirmation (empty = unset = no live send, ever)
OWNER_LINE_FMT = "OWNER_CANARY_CONFIRMED: bot_id={bot_id}"
PER_TRADE_LOSS_PCT = 30  # per-trade loss latch: 30% of the order's own S (integer lamports, floor)
MAX_WINDOW_MS = 14 * 86_400_000  # a C0 canary window is 14 days from the first start; end_ms may not reach past it
READS_DIR_CANON = "/data/mal/arena/reads"  # where arena_score.record_read writes `<bot_id>.json`; the only place under the sealed prefix a record may be read from
READ_RECORD_KEYS = ("bot_id", "status", "read_boundary_ms", "attempts", "model_ids")


def per_trade_loss_lamports(stake: int, configured: int | None = None) -> int:
    """30% of S (floor). A config value may only tighten it."""
    cap = int(stake) * PER_TRADE_LOSS_PCT // 100
    return cap if configured is None else min(cap, int(configured))


def window_cap_ms(first_start_ms: int) -> int:
    return int(first_start_ms) + MAX_WINDOW_MS


def end_cap_problem(end_ms: int, first_start_ms: int) -> str | None:
    """end_ms may not be later than the first start + 14 days (the first start is persisted in the bridge state, so a restart does not extend it)."""
    return "end_ms_over_14d" if int(end_ms) > window_cap_ms(first_start_ms) else None


WINDOW_DIR = "/var/lib/mal-hv4-c0/window"  # write-once `<bot_id>.json` per bot, outside the bridge's state dir: a new state dir, a deleted state file or a mode switch cannot reset it


def window_start_ms(bot_id: str, now_ms: int, create: bool) -> tuple[int | None, str | None]:
    """(first start ms, problem). The first start of this bot_id, kept in WINDOW_DIR/<bot_id>.json (created O_EXCL, 0444; the dir must exist and be owned by
    root or the running user, not group/other writable). `create=False` (main's pre-check) returns (None, None) when there is no file yet."""
    import os
    import stat
    if not isinstance(bot_id, str) or "/" in bot_id or bot_id.startswith("."):
        return None, "window_bot_id"
    try:
        if not _owner_mode_ok(os.stat(WINDOW_DIR)):
            return None, "window_dir_owner_mode"
    except OSError:
        return None, "window_dir_missing"
    path = os.path.join(WINDOW_DIR, f"{bot_id}.json")
    for attempt in (0, 1):
        try:
            ls = os.lstat(path)
        except FileNotFoundError:
            if not create:
                return None, None
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
            except FileExistsError:
                continue
            except OSError:
                return None, "window_file_unwritable"
            with os.fdopen(fd, "w") as fh:
                fh.write(json.dumps({"bot_id": bot_id, "first_start_ms": int(now_ms)}, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            return int(now_ms), None
        except OSError:
            return None, "window_file_unreadable"
        if stat.S_ISLNK(ls.st_mode) or not stat.S_ISREG(ls.st_mode) or ls.st_nlink != 1 or not _owner_mode_ok(ls):
            return None, "window_file_unsafe"
        try:
            rec = json.loads(open(path, "rb").read())
        except (OSError, ValueError):
            return None, "window_file_unreadable"
        v = rec.get("first_start_ms") if isinstance(rec, dict) else None
        if not isinstance(rec, dict) or rec.get("bot_id") != bot_id or isinstance(v, bool) or not isinstance(v, int):
            return None, "window_file_unreadable"
        return v, None
    return None, "window_file_unreadable"


def model_problem(bot_id: str, model_ids: Any) -> str | None:
    """F arm: the recorded model_ids must be exactly [claude-haiku-5-5]. X arm: empty, or every id that single model. Anything else is refused."""
    ok = isinstance(model_ids, list) and all(isinstance(m, str) for m in model_ids)
    if not ok:
        return "model_ids_unreadable"
    if str(bot_id).endswith("-F"):
        return None if model_ids == [MODEL_ID] else "model_id_not_pinned"
    return None if all(m == MODEL_ID for m in model_ids) else "model_id_not_pinned"


def _same_inode(a: Any, b: Any) -> bool:
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def _alias_of_sealed(path: str, extra: Iterable[str] = ()) -> bool:
    """True when `path`, or any ancestor of its realpath, is the SAME (st_dev, st_ino) as the sealed prefix or one of `extra` sealed paths (a bind mount or
    a hard-linked directory shows the sealed inode under a /data2 or other name; realpath alone cannot see it). Stat only, nothing is listed or opened."""
    import os
    sealed = []
    for sp in (SEALED_ROOT_PREFIX, *extra):
        try:
            sealed.append(os.stat(sp))
        except (FileNotFoundError, NotADirectoryError):
            if sp == SEALED_ROOT_PREFIX:
                return True  # fail closed: without the prefix's identity no alias can be ruled out
        except OSError:
            return True  # fail closed: a sealed path that cannot be stat-ed is not a pass
    cur = os.path.realpath(path)
    while True:
        try:
            st = os.stat(cur)
            if any(_same_inode(st, x) for x in sealed):
                return True
        except OSError:
            pass
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def _owner_mode_ok(st: Any) -> bool:
    """Owned by root or by the running user, and not writable by group or other."""
    import os
    return st.st_uid in (0, os.geteuid()) and not (st.st_mode & 0o022)


def synthetic_root(root: Any) -> bool:
    """A root that carries SYNTHETIC_MARKER (a regular file) and does not resolve to, or alias, the sealed prefix."""
    import os
    import stat
    try:
        if _under_sealed(str(root)):
            return False
        return stat.S_ISREG(os.lstat(os.path.join(os.path.realpath(str(root)), SYNTHETIC_MARKER)).st_mode)
    except OSError:
        return False


def read_record_problem(reads_dir: Any, bot_id: Any, pinned: Mapping[str, Any] | None = None, synthetic_ok: bool = False) -> tuple[str | None, dict[str, Any]]:
    """The gate before ANY decision row of an hv4- bot is read (QP #913): a recorded PASS read for THIS bot (arena_score.record_read output,
    `<reads_dir>/<bot_id>.json`, canonical JSON with bot_id, status PASS, read_boundary_ms, attempts, model_ids, not boundary_source forced, am15 block).
    Returns (problem or None, identity). The identity {path, dev, ino, sha256} of the first good read is passed back as `pinned` on every later check, and the
    file must still be the same inode with the same bytes (a swap, a rewrite or an unlink after the first pass is a refusal).
    Checks: reads_dir absolute; the record's realpath equals reads_dir realpath + name (no symlink hop); lstat regular, not a symlink, st_nlink == 1;
    opened O_NOFOLLOW and fstat (dev, ino) equal the lstat; the realpath is not under the sealed arena prefix except exactly READS_DIR_CANON, and no ancestor
    is the same (st_dev, st_ino) as the sealed prefix (a /data2/mal bind or alias); then the content. Applies to any hv4- bot_id.
    PROVENANCE (QP #965 B1): the record must be the pinned scorer's write-once file, so realpath(reads_dir) must EQUAL READS_DIR_CANON; the only exception is
    `synthetic_ok` (a DRY start on a root with SYNTHETIC_MARKER; the caller decides). The record's `frozen_sha` must equal FROZEN_SHA, and the reads dir and the
    record must be owned by root or the running user and not writable by group or other.
    RUNBOOK NOTE (QP N9): the identity is also persisted in the bridge state. Rewriting or replacing the record (a new inode or other bytes) latches `read_record`
    and halts the bridge FOR GOOD: only the manager clears a latch, with the bridge stopped. That is correct, not a bug."""
    import os
    import stat
    ident: dict[str, Any] = {}
    if not isinstance(bot_id, str) or not bot_id.startswith("hv4-") or "/" in bot_id or bot_id.startswith("."):
        return "read_record_bot_id", ident
    if not isinstance(reads_dir, str) or not reads_dir or not os.path.isabs(reads_dir):
        return "read_record_dir_missing", ident
    real_dir = os.path.realpath(reads_dir)
    path = os.path.join(real_dir, f"{bot_id}.json")
    under = real_dir == SEALED_ROOT_PREFIX or real_dir.startswith(SEALED_ROOT_PREFIX + "/")
    if under and real_dir != READS_DIR_CANON:
        return "read_record_dir_sealed", ident
    if real_dir != os.path.realpath(READS_DIR_CANON) and not synthetic_ok:
        return "read_record_dir_not_canonical", ident
    if real_dir != READS_DIR_CANON and _alias_of_sealed(real_dir, (READS_DIR_CANON,)):
        return "read_record_dir_sealed_alias", ident
    try:
        if not _owner_mode_ok(os.stat(real_dir)):
            return "read_record_dir_owner_mode", ident
        ls = os.lstat(path)
    except OSError:
        return "read_record_missing", ident
    if stat.S_ISLNK(ls.st_mode) or not stat.S_ISREG(ls.st_mode):
        return "read_record_not_regular", ident
    if not _owner_mode_ok(ls):
        return "read_record_owner_mode", ident
    if ls.st_nlink != 1:
        return "read_record_hardlink", ident
    if os.path.realpath(path) != path:
        return "read_record_path_alias", ident
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return "read_record_unreadable", ident
    try:
        fs = os.fstat(fd)
        if not _same_inode(fs, ls) or fs.st_nlink != 1 or not stat.S_ISREG(fs.st_mode):
            return "read_record_swapped", ident
        chunks = []
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            chunks.append(b)
        data = b"".join(chunks)
    finally:
        os.close(fd)
    ident = {"path": path, "dev": fs.st_dev, "ino": fs.st_ino, "sha256": hashlib.sha256(data).hexdigest()}
    if pinned and {k: pinned.get(k) for k in ident} != ident:
        return "read_record_changed", ident
    try:
        rec = json.loads(data)
    except ValueError:
        return "read_record_unreadable", ident
    if not isinstance(rec, dict) or any(k not in rec for k in READ_RECORD_KEYS):
        return "read_record_unreadable", ident
    if rec["bot_id"] != bot_id:
        return "read_record_other_bot", ident
    if rec.get("frozen_sha") != FROZEN_SHA:
        return "read_record_frozen_sha", ident
    if rec["status"] != "PASS" or rec.get("fail_label") or rec.get("boundary_source") == "forced" or rec.get("pending"):
        return "read_record_not_pass", ident
    if isinstance(rec["read_boundary_ms"], bool) or not isinstance(rec["read_boundary_ms"], int) or not isinstance(rec.get("am15"), dict):
        return "read_record_not_pass", ident
    why = model_problem(bot_id, rec["model_ids"])
    return (why, ident)


def owner_line_problem(path: Any, bot_id: str, pin: str | None = None, root_check: Any = None) -> str | None:
    """The owner's confirmation: the exact line `OWNER_CANARY_CONFIRMED: bot_id=<id>` in a ROOT-OWNED file (OWNER_DEC_PATH) whose sha256 equals the pinned
    blob. NOT a file of the installed tree. `root_check(path)` is the bridge's root:root check (None = not run, tests only). The file is opened O_NOFOLLOW and
    hashed from the bytes that are then searched. Exactly one line may equal the wanted line; a prefix or another bot's line never matches."""
    import os
    pin = CANARY_DEC_SHA256 if pin is None else pin
    if not (isinstance(pin, str) and len(pin) == 64 and all(c in "0123456789abcdef" for c in pin)):
        return "owner_dec_pin_unset"
    if root_check is not None:
        why = root_check(path)
        if why:
            return f"owner_dec_{why}"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return "owner_dec_missing"
    except OSError:
        return "owner_dec_unreadable"
    try:
        data = b""
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            data += b
    finally:
        os.close(fd)
    if hashlib.sha256(data).hexdigest() != pin:
        return "owner_dec_sha_mismatch"
    want = OWNER_LINE_FMT.format(bot_id=bot_id)
    return None if sum(1 for ln in data.decode("utf-8", "replace").split("\n") if ln == want) == 1 else "owner_line_missing"


def start_row_fields() -> dict[str, Any]:
    """Pins the bridge writes into its own START log row (`bridge_start`). The key `frozen_sha` is the key the HV4 runner's own `start` row uses
    (tools/hv4_runner.py: {"kind": "start", "binding", "frozen_sha", "lane_h"}); the runner row carries no config or ladder sha (the runner is frozen), so
    these pins live in the bridge's row."""
    return {"frozen_sha": FROZEN_SHA, "config_sha256": CONFIG_PIN_SHA256, "ladder_sha256": ladder_pin_sha256(), "twin_writer_sha256": TWIN_WRITER_SHA256,
            "sell_pin_sha256": hashlib.sha256(json.dumps(SELL_BUILDER_PIN, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "priority_pin": dict(PRIORITY_PIN), "model_id": MODEL_ID, "window_ms": MAX_WINDOW_MS, "per_trade_loss_pct": PER_TRADE_LOSS_PCT}




def _under_sealed(path: str) -> bool:
    import os
    real = os.path.realpath(path)
    return real == SEALED_ROOT_PREFIX or real.startswith(SEALED_ROOT_PREFIX + "/") or _alias_of_sealed(path)  # realpath OR the same (st_dev, st_ino): one rule for every caller


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


class SealedPath(Exception):
    """A decision-log read hit a path that could be an alias of the sealed arena (symlink, hard link, swapped directory or inode, realpath under the arena)."""


def guard_dir(root: str, bot_id: str) -> str:
    """Check `<root>/decisions/<bot_id>` before listing or reading it: realpath not under the sealed prefix, and neither `decisions` nor the bot dir is
    a symlink (lstat) or anything but a directory. Returns the dir path. Missing is fine (nothing to read); raises SealedPath otherwise."""
    import os
    import stat
    dec = os.path.join(root, "decisions")
    d = os.path.join(dec, bot_id)
    if _under_sealed(d) or _alias_of_sealed(d, (SEALED_ROOT_PREFIX + "/decisions", SEALED_ROOT_PREFIX + "/decisions/" + bot_id)):
        raise SealedPath("realpath_under_arena")
    for x in (dec, d):
        try:
            st = os.lstat(x)
        except FileNotFoundError:
            return d
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise SealedPath("symlink_or_not_dir")
    return d


def guarded_listdir(root: str, bot_id: str) -> list[str]:
    """Sorted `*.jsonl` names in the bot dir, after `guard_dir`. Names only; no file is opened."""
    import os
    d = guard_dir(root, bot_id)
    try:
        return sorted(n for n in os.listdir(d) if n.endswith(".jsonl"))
    except FileNotFoundError:
        return []


def guarded_read(root: str, bot_id: str, name: str) -> bytes:
    """Read one day file, running EVERY check on EVERY read (QP #913): dir guard; lstat must be a regular file, not a symlink, with st_nlink == 1 (a
    hard link to an arena file has 2); realpath not under the sealed prefix; open with O_NOFOLLOW and compare fstat (dev, ino, nlink) with the lstat,
    so a swap between the check and the open is caught. Raises SealedPath on any hit; reads through the fd it checked."""
    import os
    import stat
    d = guard_dir(root, bot_id)
    path = os.path.join(d, name)
    if _under_sealed(path):
        raise SealedPath("realpath_under_arena")
    try:
        ls = os.lstat(path)
    except FileNotFoundError:
        return b""
    if stat.S_ISLNK(ls.st_mode):
        raise SealedPath("symlink")
    if not stat.S_ISREG(ls.st_mode):
        raise SealedPath("not_regular")
    if ls.st_nlink != 1:
        raise SealedPath("hardlink")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise SealedPath("open_nofollow_failed") from None
    try:
        fs = os.fstat(fd)
        if (fs.st_dev, fs.st_ino) != (ls.st_dev, ls.st_ino) or fs.st_nlink != 1 or not stat.S_ISREG(fs.st_mode):
            raise SealedPath("inode_swapped")
        try:  # a bind or alias of an arena day file: same (st_dev, st_ino) as the sealed counterpart of this name
            sealed_file = os.stat(os.path.join(SEALED_ROOT_PREFIX, "decisions", bot_id, name))
            if (sealed_file.st_dev, sealed_file.st_ino) == (fs.st_dev, fs.st_ino):
                raise SealedPath("inode_is_arena_file")
        except OSError:
            pass
        chunks = []
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            chunks.append(b)
        return b"".join(chunks)
    finally:
        os.close(fd)


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
