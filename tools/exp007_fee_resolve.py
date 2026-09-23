"""Shared Pump fee-at-T parsing for EXP-007b enrich and EXP-007c restamp."""

from __future__ import annotations

from dataclasses import dataclass

from tools.exp003_rpc_backfill import _parse_pump_bonding_curve_account

PUMP_GLOBAL_PDA = "4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf"
_PUMP_GLOBAL_ACCOUNT_DISC = bytes.fromhex("a7e8e8b1c86c727f")
_GLOBAL_FEE_BPS_OFFSET = 8 + 1 + 32 + 32 + 32


@dataclass(frozen=True)
class FeeResolve:
    fee: str
    reason: str
    global_fee_bps: int | None
    is_holder_reward: bool | None
    creator_fee_bps: int | None


def parse_global_fee_bps(account_data: bytes) -> int | None:
    if len(account_data) < _GLOBAL_FEE_BPS_OFFSET + 8:
        return None
    if account_data[:8] != _PUMP_GLOBAL_ACCOUNT_DISC:
        return None
    return int.from_bytes(
        account_data[_GLOBAL_FEE_BPS_OFFSET : _GLOBAL_FEE_BPS_OFFSET + 8], "little"
    )


def parse_bonding_curve_fee_extension(account_data: bytes) -> tuple[bool | None, int | None]:
    """Return (is_holder_reward, creator_fee_bps) when extended layout present."""
    if _parse_pump_bonding_curve_account(account_data) is None:
        return None, None
    is_holder: bool | None = None
    creator_bps: int | None = None
    if len(account_data) >= 125:
        creator_bps = int.from_bytes(account_data[115:123], "little")
        is_holder = account_data[124] != 0
    elif len(account_data) >= 123:
        creator_bps = int.from_bytes(account_data[115:123], "little")
    return is_holder, creator_bps


def classify_fee_tag(
    *,
    global_fee_bps: int | None,
    global_ok: bool,
    is_holder_reward: bool | None,
    creator_fee_bps: int | None,
) -> FeeResolve:
    if is_holder_reward:
        return FeeResolve(
            fee="creator_dynamic",
            reason="bonding_curve.is_holder_reward",
            global_fee_bps=global_fee_bps,
            is_holder_reward=True,
            creator_fee_bps=creator_fee_bps,
        )
    if creator_fee_bps is not None and creator_fee_bps > 0:
        return FeeResolve(
            fee="creator_dynamic",
            reason="bonding_curve.creator_fee_bps",
            global_fee_bps=global_fee_bps,
            is_holder_reward=False,
            creator_fee_bps=creator_fee_bps,
        )
    if global_fee_bps == 100:
        return FeeResolve(
            fee="global_100bps",
            reason="global.fee_basis_points",
            global_fee_bps=global_fee_bps,
            is_holder_reward=is_holder_reward,
            creator_fee_bps=creator_fee_bps,
        )
    if global_fee_bps == 95:
        return FeeResolve(
            fee="global_95bps",
            reason="global.fee_basis_points",
            global_fee_bps=global_fee_bps,
            is_holder_reward=is_holder_reward,
            creator_fee_bps=creator_fee_bps,
        )
    if global_fee_bps is not None and global_fee_bps not in (95, 100):
        return FeeResolve(
            fee="unverified",
            reason=f"global.fee_basis_points_nonstandard_{global_fee_bps}",
            global_fee_bps=global_fee_bps,
            is_holder_reward=is_holder_reward,
            creator_fee_bps=creator_fee_bps,
        )
    if not global_ok:
        return FeeResolve(
            fee="unverified",
            reason="global_account_unreadable_at_slot",
            global_fee_bps=None,
            is_holder_reward=is_holder_reward,
            creator_fee_bps=creator_fee_bps,
        )
    return FeeResolve(
        fee="unverified",
        reason="fee_fields_missing",
        global_fee_bps=global_fee_bps,
        is_holder_reward=is_holder_reward,
        creator_fee_bps=creator_fee_bps,
    )
