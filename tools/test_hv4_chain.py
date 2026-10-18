"""Tests for tools/hv4_chain.py (Haiku trader v4, build plan item 2). No network.

Account bytes come from two places:
  * `build_*` helpers below, written from the spl-token-2022 layout (one fixture per extension type, plus classic SPL);
  * tools/fixtures/hv4_chain/recorded.json: public mainnet accounts fetched once from the public RPC (mint accounts as base64 with the
    node's own jsonParsed reading beside them, PumpSwap Pool accounts), committed as bytes.
"""
from __future__ import annotations

import ast
import base64
import json
import logging
import random
import struct
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

from tools import hv4_chain as H
from tools import pump_structure_monitor as M

FX_PATH = Path(__file__).parent / "fixtures" / "hv4_chain" / "recorded.json"

# ---- builders (spl-token-2022 layout) ---------------------------------------------------------------------------------------
K = lambda n: bytes([n]) * 32  # noqa: E731 - a 32-byte test key
KS = lambda n: M.b58encode(K(n))  # noqa: E731
ZERO = b"\0" * 32
MINT = M.b58encode(bytes([7]) * 31 + b"\1")
POOL = M.b58encode(bytes([9]) * 31 + b"\2")


def build_base(mint_auth: bytes | None = None, freeze_auth: bytes | None = None, supply: int = 10**15, decimals: int = 6,
               init: int = 1) -> bytes:
    def co(k: bytes | None) -> bytes:
        return struct.pack("<I", 0) + ZERO if k is None else struct.pack("<I", 1) + k

    return co(mint_auth) + struct.pack("<Q", supply) + bytes([decimals, init]) + co(freeze_auth)


def tlv(ext_id: int, value: bytes) -> bytes:
    return struct.pack("<HH", ext_id, len(value)) + value


def build_t22(exts: list[tuple[int, bytes]], base: bytes | None = None, pad_to: int = 0) -> bytes:
    base = build_base() if base is None else base
    out = base + b"\0" * (H.ACCOUNT_LEN - len(base)) + bytes([H.ACCOUNT_TYPE_MINT]) + b"".join(tlv(i, v) for i, v in exts)
    return out + b"\0" * pad_to


def v_transfer_fee(older_bps: int = 0, newer_bps: int = 0, older_max: int = 0, newer_max: int = 0) -> bytes:
    return (K(1) + K(2) + struct.pack("<Q", 0) + struct.pack("<QQH", 5, older_max, older_bps) + struct.pack("<QQH", 900, newer_max, newer_bps))


def v_hook(authority: bytes = ZERO, program: bytes = K(3)) -> bytes:
    return authority + program


def v_pausable(paused: int = 0) -> bytes:
    return K(4) + bytes([paused])


def v_metadata_pointer() -> bytes:
    return K(5) + K(6)


def v_interest() -> bytes:
    return K(5) + struct.pack("<qhqh", 1_700_000_000, 100, 1_700_000_100, 150)


def v_scaled_ui() -> bytes:
    return K(5) + struct.pack("<dqd", 1.0, 0, 1.0)


def v_conf_transfer() -> bytes:
    return K(5) + b"\1" + K(6)


def v_token_metadata() -> bytes:
    name, sym, uri = b"Name", b"SYM", b"https://example.invalid/x.json"
    return K(1) + K(2) + b"".join(struct.pack("<I", len(s)) + s for s in (name, sym, uri)) + struct.pack("<I", 0)


# ======================================================================================================================
# classic SPL
# ======================================================================================================================
def test_classic_spl_clean_mint():
    p = H.parse_mint_account(build_base(), H.SPL_TOKEN_PROGRAM)
    assert p.token_program == "spl-token" and p.extensions == () and p.veto_reasons == ()
    assert p.mint_authority is None and p.freeze_authority is None
    assert p.supply_raw == 10**15 and p.decimals == 6


def test_classic_spl_authorities_veto():
    p = H.parse_mint_account(build_base(mint_auth=K(1)), H.SPL_TOKEN_PROGRAM)
    assert p.mint_authority == KS(1) and p.veto_reasons == (H.V_MINT_AUTH,)
    p = H.parse_mint_account(build_base(freeze_auth=K(2)), H.SPL_TOKEN_PROGRAM)
    assert p.freeze_authority == KS(2) and p.veto_reasons == (H.V_FREEZE_AUTH,)
    p = H.parse_mint_account(build_base(mint_auth=K(1), freeze_auth=K(2)), H.SPL_TOKEN_PROGRAM)
    assert p.veto_reasons == (H.V_MINT_AUTH, H.V_FREEZE_AUTH)


@pytest.mark.parametrize("data", [
    build_base()[:81],  # short
    build_base() + b"\0",  # spl-token mints are exactly 82 bytes
    build_base(init=0),  # not initialized
    build_base(init=2),
    struct.pack("<I", 2) + ZERO + build_base()[36:],  # COption tag 2
    build_base()[:46] + struct.pack("<I", 7) + ZERO,  # freeze COption tag 7
    b"",
])
def test_classic_spl_bad_bytes_fail_closed(data):
    with pytest.raises(H.ChainParseError):
        H.parse_mint_account(data, H.SPL_TOKEN_PROGRAM)


def test_owner_must_be_a_token_program():
    with pytest.raises(H.ChainParseError):
        H.parse_mint_account(build_base(), "11111111111111111111111111111111")


# ======================================================================================================================
# Token-2022: one test per extension type
# ======================================================================================================================
def test_t22_without_extensions_is_82_bytes_and_clean():
    p = H.parse_mint_account(build_base(), H.TOKEN_2022_PROGRAM)
    assert p.token_program == "token-2022" and p.extensions == () and p.veto_reasons == ()


def test_t22_transfer_hook_vetoes_and_exposes_program():
    p = H.parse_mint_account(build_t22([(14, v_hook(authority=K(8), program=K(3)))]), H.TOKEN_2022_PROGRAM)
    assert p.extensions == ("transferHook",) and p.veto_reasons == (H.V_HOOK,)
    assert p.ext_info["transferHook"]["program_id"] == KS(3) and p.ext_info["transferHook"]["authority"] == KS(8)


def test_t22_transfer_hook_with_null_program_still_vetoes():
    p = H.parse_mint_account(build_t22([(14, v_hook(ZERO, ZERO))]), H.TOKEN_2022_PROGRAM)
    assert p.veto_reasons == (H.V_HOOK,) and p.ext_info["transferHook"]["program_id"] is None


@pytest.mark.parametrize("older,newer,vetoed", [(0, 0, False), (25, 0, True), (0, 100, True), (30, 30, True), (0, 1, True)])
def test_t22_transfer_fee_only_vetoes_when_positive(older, newer, vetoed):
    p = H.parse_mint_account(build_t22([(1, v_transfer_fee(older, newer, 1000, 2000))]), H.TOKEN_2022_PROGRAM)
    assert p.extensions == ("transferFeeConfig",)
    assert (H.V_FEE in p.veto_reasons) is vetoed
    info = p.ext_info["transferFeeConfig"]
    assert info["older_bps"] == older and info["newer_bps"] == newer and info["older_max_fee"] == 1000 and info["newer_max_fee"] == 2000
    assert info["config_authority"] == KS(1) and info["withdraw_authority"] == KS(2)


def test_t22_permanent_delegate_vetoes_even_when_delegate_is_null():
    p = H.parse_mint_account(build_t22([(12, K(5))]), H.TOKEN_2022_PROGRAM)
    assert p.veto_reasons == (H.V_DELEGATE,) and p.ext_info["permanentDelegate"]["delegate"] == KS(5)
    p = H.parse_mint_account(build_t22([(12, ZERO)]), H.TOKEN_2022_PROGRAM)
    assert p.veto_reasons == (H.V_DELEGATE,) and p.ext_info["permanentDelegate"]["delegate"] is None


def test_t22_non_transferable_vetoes():
    p = H.parse_mint_account(build_t22([(9, b"")]), H.TOKEN_2022_PROGRAM)
    assert p.extensions == ("nonTransferable",) and p.veto_reasons == (H.V_NONTRANSFERABLE,)


@pytest.mark.parametrize("state,vetoed", [(1, False), (2, True)])
def test_t22_default_account_state(state, vetoed):
    p = H.parse_mint_account(build_t22([(6, bytes([state]))]), H.TOKEN_2022_PROGRAM)
    assert (H.V_FROZEN_DEFAULT in p.veto_reasons) is vetoed
    assert p.ext_info["defaultAccountState"]["state"] == ("frozen" if vetoed else "initialized")


@pytest.mark.parametrize("state", [0, 3, 255])
def test_t22_default_account_state_illegal_value_fails_closed(state):
    with pytest.raises(H.ChainParseError):
        H.parse_mint_account(build_t22([(6, bytes([state]))]), H.TOKEN_2022_PROGRAM)


@pytest.mark.parametrize("paused", [0, 1])
def test_t22_pausable_vetoes_paused_or_not(paused):
    p = H.parse_mint_account(build_t22([(26, v_pausable(paused))]), H.TOKEN_2022_PROGRAM)
    assert p.veto_reasons == (H.V_PAUSABLE,) and p.ext_info["pausableConfig"]["paused"] is bool(paused)


def test_t22_pausable_bad_flag_fails_closed():
    with pytest.raises(H.ChainParseError):
        H.parse_mint_account(build_t22([(26, v_pausable(2))]), H.TOKEN_2022_PROGRAM)


def test_t22_unknown_extension_id_vetoes():
    p = H.parse_mint_account(build_t22([(99, b"\1\2\3")]), H.TOKEN_2022_PROGRAM)
    assert p.extensions == ("unknown99",) and p.veto_reasons == (f"{H.V_UNKNOWN_EXT}:99",)


def test_t22_benign_extensions_do_not_veto():
    exts = [(18, v_metadata_pointer()), (19, v_token_metadata()), (3, K(5)), (10, v_interest()), (25, v_scaled_ui()),
            (4, v_conf_transfer()), (20, K(5) + K(6)), (22, K(5) + K(6)), (21, K(1) + K(2) + struct.pack("<QQ", 3, 10)),
            (23, K(1) + K(2) + struct.pack("<Q", 1))]
    p = H.parse_mint_account(build_t22(exts), H.TOKEN_2022_PROGRAM)
    assert p.extensions == ("metadataPointer", "tokenMetadata", "mintCloseAuthority", "interestBearingConfig", "scaledUiAmountConfig",
                            "confidentialTransferMint", "groupPointer", "groupMemberPointer", "tokenGroup", "tokenGroupMember")
    assert p.veto_reasons == ()
    assert p.ext_info["interestBearingConfig"]["current_rate_bps"] == 150
    assert p.ext_info["scaledUiAmountConfig"]["multiplier"] == 1.0
    assert p.ext_info["mintCloseAuthority"]["close_authority"] == KS(5)


def test_t22_combination_lists_every_veto_in_order():
    exts = [(18, v_metadata_pointer()), (14, v_hook()), (1, v_transfer_fee(10, 0)), (12, K(5)), (9, b""), (6, bytes([2])), (26, v_pausable())]
    p = H.parse_mint_account(build_t22(exts, base=build_base(mint_auth=K(1), freeze_auth=K(2))), H.TOKEN_2022_PROGRAM)
    assert p.veto_reasons == (H.V_MINT_AUTH, H.V_FREEZE_AUTH, H.V_HOOK, H.V_FEE, H.V_DELEGATE, H.V_NONTRANSFERABLE, H.V_FROZEN_DEFAULT,
                              H.V_PAUSABLE)


def test_t22_trailing_zero_padding_after_terminator_is_accepted():
    data = build_t22([(14, v_hook())], pad_to=12)
    assert H.parse_mint_account(data, H.TOKEN_2022_PROGRAM).veto_reasons == (H.V_HOOK,)


# ---- Token-2022 malformed ---------------------------------------------------------------------------------------------------
def _clean_ext_mint() -> bytes:
    return build_t22([(18, v_metadata_pointer())])


@pytest.mark.parametrize("name,data", [
    ("account type is Account", bytearray(_clean_ext_mint())),
    ("account type zero", bytearray(_clean_ext_mint())),
    ("length 100", bytearray(build_base() + b"\0" * 18)),
    ("length 165", bytearray(build_base() + b"\0" * 83)),
    ("length 355 (multisig size)", bytearray(build_base() + b"\0" * 273)),
    ("nonzero padding", bytearray(_clean_ext_mint())),
    ("value runs past the end", bytearray(_clean_ext_mint()[:-5])),
    ("account-only extension (immutableOwner)", bytearray(build_t22([(7, b"")]))),
    ("account-only extension (transferHookAccount)", bytearray(build_t22([(15, b"\0" * 2)]))),
    ("repeated extension", bytearray(build_t22([(9, b""), (9, b"")]))),
    ("hook of the wrong length", bytearray(build_t22([(14, b"\1" * 63)]))),
    ("fee config of the wrong length", bytearray(build_t22([(1, b"\0" * 100)]))),
    ("pausable of the wrong length", bytearray(build_t22([(26, b"\0" * 32)]))),
    ("nonzero byte after the terminator", bytearray(build_t22([(9, b"")], pad_to=8))),
    ("trailing partial header", bytearray(build_t22([(9, b"")], pad_to=0) + b"\0\1")),
])
def test_t22_malformed_fails_closed(name, data):
    data = bytearray(data)
    if name == "account type is Account":
        data[H.ACCOUNT_TYPE_OFFSET] = 2
    elif name == "account type zero":
        data[H.ACCOUNT_TYPE_OFFSET] = 0
    elif name == "nonzero padding":
        data[100] = 1
    elif name == "nonzero byte after the terminator":
        data[-1] = 9
    with pytest.raises(H.ChainParseError):
        H.parse_mint_account(bytes(data), H.TOKEN_2022_PROGRAM)


def test_t22_fuzz_only_ever_raises_chain_parse_error():
    rng = random.Random(1)
    seeds = [build_t22([(14, v_hook()), (1, v_transfer_fee(5, 0)), (12, K(5)), (26, v_pausable()), (19, v_token_metadata())]),
             build_t22([(18, v_metadata_pointer()), (6, bytes([1]))]), build_base()]
    for _ in range(3000):
        data = bytearray(rng.choice(seeds))
        for _ in range(rng.randint(1, 4)):
            data[rng.randrange(len(data))] = rng.randrange(256)
        if rng.random() < 0.3:
            data = data[: rng.randrange(1, len(data) + 1)]
        try:
            p = H.parse_mint_account(bytes(data), H.TOKEN_2022_PROGRAM)
        except H.ChainParseError:
            continue
        assert isinstance(p.veto_reasons, tuple)


def test_truncating_a_hook_mint_never_hides_the_hook():
    full = build_t22([(14, v_hook())])
    for cut in range(H.MINT_LEN, len(full)):
        try:
            p = H.parse_mint_account(full[:cut], H.TOKEN_2022_PROGRAM)
        except H.ChainParseError:
            continue
        # only the two extension-free prefixes are legal accounts; anything that cuts into the hook TLV must raise
        assert cut in (H.MINT_LEN, H.TLV_START) and p.extensions == ()
