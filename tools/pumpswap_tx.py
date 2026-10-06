"""Keyless PumpSwap (pump.fun AMM) transaction builder. Pure: no RPC, no keys.

DEC-018 section 4 item 1, part 1. EXP-012 buys and sells on the PumpSwap pool
created at migration. This module builds the exact instructions the live
executor would send, as UNSIGNED transactions, so they can be run through
`simulateTransaction` (sigVerify off) before any wallet exists. Nothing here
loads, generates or stores a keypair. Nothing here sends anything.

Evidence (all public on-chain data, saved under tools/fixtures/pumpswap/):

* Program id: ``pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`` (PumpSwap AMM).
  Fee program: ``pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ``.
* Instruction data is Anchor: 8-byte discriminator + borsh args. Read off real
  transactions fetched with getTransaction on 2026-10-04 (current layout) and
  one from 2026-08-28 (older, 24-byte buy, 1 fewer trailing account):

    buy_exact_quote_in  c62e1552b4d9e870  spend_quote_in u64, min_base_amount_out u64,
                                          track_volume u8 (01 observed)          25 bytes
    buy                 66063d1201daebea  base_amount_out u64, max_quote_amount_in u64,
                                          track_volume u8 (absent in Aug-2026 txs) 24/25 bytes
    sell                33e685a4017f83ad  base_amount_in u64, min_quote_amount_out u64   24 bytes

* Accounts, current chain (fixtures buy_exact_quote_in_a/b, sell_a/b):

  buy / buy_exact_quote_in (26):
    0 pool (w), 1 user (signer, w), 2 global_config, 3 base_mint, 4 quote_mint (WSOL),
    5 user_base_ata (w), 6 user_quote_ata (w), 7 pool_base_vault (w), 8 pool_quote_vault (w),
    9 protocol_fee_recipient, 10 its WSOL ATA (w), 11 base_token_program, 12 quote_token_program,
    13 system, 14 associated_token, 15 event_authority, 16 pumpswap program,
    17 coin_creator_vault_ata (w), 18 coin_creator_vault_authority,
    19 global_volume_accumulator, 20 user_volume_accumulator (w), 21 fee_config, 22 fee_program,
    23 pool_v2 (PDA ["pool-v2", base_mint]), 24 buyback_fee_recipient, 25 its WSOL ATA (w)
  sell (24): 0..16 as above, 17 coin_creator_vault_ata (w), 18 coin_creator_vault_authority,
    19 fee_config, 20 fee_program, 21 pool_v2, 22 buyback_fee_recipient, 23 its WSOL ATA (w).

  PDAs (program pAMM unless noted): global_config ["global_config"], event_authority
  ["__event_authority"], global_volume_accumulator ["global_volume_accumulator"],
  user_volume_accumulator ["user_volume_accumulator", user], coin_creator_vault_authority
  ["creator_vault", coin_creator], pool_v2 ["pool-v2", base_mint], fee_config
  ["fee_config", pAMM_program_id] under the fee program.

* Surrounding instructions seen in real txs: ComputeBudget setComputeUnitLimit (02) and
  setComputeUnitPrice (03); associated-token create-idempotent (01); system transfer of the
  spend into the user's WSOL ATA then token SyncNative (17); CloseAccount (09) of the WSOL ATA.

Pool state needed (see ``PoolState``): from the pool account (base_mint, quote_mint,
base/quote vault, coin_creator), the base mint's owner (Tokenkeg or Token-2022; current pump
mints are Token-2022), the global_config (protocol-fee recipients at offset 57 x8, buyback fee
recipients at offset 675 x8, both inferred from the three recipients seen in real txs and
checked by simulation), and, for a quote only, the vault balances.

Open points are listed in the PR body (fee recipient choice, creator vault, cashback pools).
"""

from __future__ import annotations

import base64
import struct
from dataclasses import dataclass

from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.message import Message
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

PUMPSWAP_PROGRAM = Pubkey.from_string("pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA")
FEE_PROGRAM = Pubkey.from_string("pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ")
GLOBAL_CONFIG = Pubkey.from_string("ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw")
WSOL_MINT = Pubkey.from_string("So11111111111111111111111111111111111111112")
TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
TOKEN_2022_PROGRAM = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
ATA_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
SYSTEM_PROGRAM = Pubkey.from_string("11111111111111111111111111111111")

DISC_BUY = bytes.fromhex("66063d1201daebea")
DISC_BUY_EXACT_QUOTE_IN = bytes.fromhex("c62e1552b4d9e870")
DISC_SELL = bytes.fromhex("33e685a4017f83ad")

PROTOCOL_RECIPIENTS_OFFSET = 57  # global_config: 8 x pubkey after lp/protocol bps + flags
BUYBACK_RECIPIENTS_OFFSET = 675  # inferred; 8 x pubkey, 18 bytes of tail follow
N_RECIPIENTS = 8

DEFAULT_PRIORITY_TOTAL_LAMPORTS = 500_000  # owner's trial term, per side
DEFAULT_BUY_CU_LIMIT = 250_000
DEFAULT_SELL_CU_LIMIT = 200_000
TX_SIZE_LIMIT = 1232
BASE_FEE_PER_SIGNATURE = 5_000


def pda(seeds: list[bytes], program: Pubkey = PUMPSWAP_PROGRAM) -> Pubkey:
    return Pubkey.find_program_address(seeds, program)[0]


def ata(owner: Pubkey, mint: Pubkey, token_program: Pubkey) -> Pubkey:
    return Pubkey.find_program_address([bytes(owner), bytes(token_program), bytes(mint)], ATA_PROGRAM)[0]


def event_authority() -> Pubkey:
    return pda([b"__event_authority"])


def global_volume_accumulator() -> Pubkey:
    return pda([b"global_volume_accumulator"])


def user_volume_accumulator(user: Pubkey) -> Pubkey:
    return pda([b"user_volume_accumulator", bytes(user)])


def creator_vault_authority(coin_creator: Pubkey) -> Pubkey:
    return pda([b"creator_vault", bytes(coin_creator)])


PUMP_PROGRAM = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")


def canonical_pool(mint: Pubkey) -> Pubkey:
    """The migrated pump.fun pool: pool_authority = PDA(["pool-authority", mint], pump program), pool = PDA(
    ["pool", index 0 (u16 LE), pool_authority, mint, WSOL], PumpSwap). NOT pool_v2, which is only a helper
    account in the swap's remaining accounts. Non-canonical pools (other index/creator) are not covered."""
    authority = pda([b"pool-authority", bytes(mint)], PUMP_PROGRAM)
    return pda([b"pool", (0).to_bytes(2, "little"), bytes(authority), bytes(mint), bytes(WSOL_MINT)])


def pool_v2(base_mint: Pubkey) -> Pubkey:
    return pda([b"pool-v2", bytes(base_mint)])


def fee_config() -> Pubkey:
    return pda([b"fee_config", bytes(PUMPSWAP_PROGRAM)], FEE_PROGRAM)


@dataclass(frozen=True)
class PoolState:
    """Everything the builder needs. Public keys only."""

    pool: Pubkey
    base_mint: Pubkey
    quote_mint: Pubkey
    base_vault: Pubkey
    quote_vault: Pubkey
    coin_creator: Pubkey
    base_token_program: Pubkey  # owner of base_mint: Tokenkeg or Token-2022
    protocol_fee_recipient: Pubkey  # any of global_config's 8
    buyback_fee_recipient: Pubkey  # any of global_config's buyback recipients
    quote_token_program: Pubkey = TOKEN_PROGRAM


V_OFFSET = 245  # pool account tail: flags at 243..245, then the stored virtual quote reserve V
_I64_MIN, _I64_MAX = -(2**63), 2**63 - 1


def parse_virtual_signed(data: bytes) -> int | None:
    """Stored V as a SIGNED value: i128 LE at 245..261 when the account has those bytes, else i64 LE at 245..253.
    None when the account is too short or the value does not fit in i64. Same semantics as
    `tools.pumpswap_virtual.parse_virtual`. Stored V = V0 - A - B, so it is negative on V0 = 0 pools with
    pending counters; reading it unsigned gives about 1.8e19 (ARTIFACTS/lab/pumpswap-v-layout-2026-10-06.md)."""
    if len(data) >= V_OFFSET + 16:
        v = int.from_bytes(data[V_OFFSET : V_OFFSET + 16], "little", signed=True)
    elif len(data) >= V_OFFSET + 8:
        v = int.from_bytes(data[V_OFFSET : V_OFFSET + 8], "little", signed=True)
    else:
        return None
    return v if _I64_MIN <= v <= _I64_MAX else None


def parse_pool_account(data: bytes) -> dict[str, Pubkey | int]:
    """Pool account layout: disc8, bump u8, index u16, creator, base_mint, quote_mint,
    lp_mint, pool_base_vault, pool_quote_vault (32 each), lp_supply u64, coin_creator."""
    if len(data) < 243:  # coin_creator ends at byte 243; the old 211 guard let a short account panic in Pubkey.from_bytes
        raise ValueError("pool account too short")
    o = 11
    out: dict[str, Pubkey | int] = {"index": int.from_bytes(data[9:11], "little")}
    for name in ("creator", "base_mint", "quote_mint", "lp_mint", "base_vault", "quote_vault"):
        out[name] = Pubkey.from_bytes(data[o : o + 32])
        o += 32
    out["lp_supply"] = int.from_bytes(data[o : o + 8], "little")
    o += 8
    out["coin_creator"] = Pubkey.from_bytes(data[o : o + 32])
    o += 32
    # Tail: two flag bytes, then V, a SIGNED value (~17.58 SOL = 17.58e9 lamports on fresh migrations).
    # The sim-vs-paper decomposition shows the swap math adds it to the quote reserve.
    # Key omitted when V is not representable in i64 (callers treat a missing key as no V).
    if len(data) >= o + 10:
        out["flags"] = data[o : o + 2].hex()
        v = parse_virtual_signed(data)
        if v is not None:
            out["virtual_quote_reserves"] = v
    return out


def parse_global_config(data: bytes) -> dict[str, object]:
    protocol = [Pubkey.from_bytes(data[PROTOCOL_RECIPIENTS_OFFSET + 32 * i : PROTOCOL_RECIPIENTS_OFFSET + 32 * (i + 1)]) for i in range(N_RECIPIENTS)]
    buyback = [Pubkey.from_bytes(data[BUYBACK_RECIPIENTS_OFFSET + 32 * i : BUYBACK_RECIPIENTS_OFFSET + 32 * (i + 1)]) for i in range(N_RECIPIENTS)]
    return {
        "lp_fee_bps": int.from_bytes(data[40:48], "little"),
        "protocol_fee_bps": int.from_bytes(data[48:56], "little"),
        "protocol_fee_recipients": protocol,
        "buyback_fee_recipients": buyback,
    }


def pool_state_from_accounts(
    pool: Pubkey,
    pool_data: bytes,
    base_token_program: Pubkey,
    protocol_fee_recipient: Pubkey,
    buyback_fee_recipient: Pubkey,
) -> PoolState:
    p = parse_pool_account(pool_data)
    return PoolState(
        pool=pool,
        base_mint=p["base_mint"],  # type: ignore[arg-type]
        quote_mint=p["quote_mint"],  # type: ignore[arg-type]
        base_vault=p["base_vault"],  # type: ignore[arg-type]
        quote_vault=p["quote_vault"],  # type: ignore[arg-type]
        coin_creator=p["coin_creator"],  # type: ignore[arg-type]
        base_token_program=base_token_program,
        protocol_fee_recipient=protocol_fee_recipient,
        buyback_fee_recipient=buyback_fee_recipient,
    )


def pool_state_from_b64(pool: str, pool_b64: str, **kw) -> PoolState:
    return pool_state_from_accounts(Pubkey.from_string(pool), base64.b64decode(pool_b64), **kw)


# --- amounts ---------------------------------------------------------------


def priority_price_for_total(total_lamports: int, cu_limit: int) -> int:
    """setComputeUnitPrice value (micro-lamports per CU) whose fee over cu_limit CUs
    is at least total_lamports. Exact when total * 1e6 divides by cu_limit."""
    if total_lamports < 0 or cu_limit <= 0:
        raise ValueError("bad priority inputs")
    return -(-total_lamports * 1_000_000 // cu_limit)


def priority_fee_lamports(price_micro: int, cu_limit: int) -> int:
    """What the runtime charges: ceil(price * limit / 1e6)."""
    return -(-price_micro * cu_limit // 1_000_000)


def min_out_with_slippage(expected_out: int, slippage_bps: int) -> int:
    """Lower bound on tokens / lamports received. floor(expected * (1 - slip))."""
    if not 0 <= slippage_bps <= 10_000:
        raise ValueError("slippage_bps out of range")
    return expected_out * (10_000 - slippage_bps) // 10_000


def max_in_with_slippage(expected_in: int, slippage_bps: int) -> int:
    """Upper bound on lamports paid. ceil(expected * (1 + slip))."""
    if slippage_bps < 0:
        raise ValueError("slippage_bps out of range")
    return -(-expected_in * (10_000 + slippage_bps) // 10_000)


def cp_buy_out(spend_lamports: int, quote_reserve: int, base_reserve: int, fee_ppm: int) -> int:
    """Tokens out for a spend that includes fees: net = spend*(1-fee), then x*y=k."""
    net = spend_lamports * (1_000_000 - fee_ppm) // 1_000_000
    return net * base_reserve // (quote_reserve + net)


def cp_sell_out(token_in: int, quote_reserve: int, base_reserve: int, fee_ppm: int) -> int:
    gross = token_in * quote_reserve // (base_reserve + token_in)
    return gross * (1_000_000 - fee_ppm) // 1_000_000


# --- instructions ----------------------------------------------------------


def _m(pk: Pubkey, writable: bool = False, signer: bool = False) -> AccountMeta:
    return AccountMeta(pk, is_signer=signer, is_writable=writable)


def swap_accounts(ps: PoolState, user: Pubkey, *, side: str) -> list[AccountMeta]:
    wsol = ps.quote_mint
    user_base = ata(user, ps.base_mint, ps.base_token_program)
    user_quote = ata(user, wsol, ps.quote_token_program)
    fee_ata = ata(ps.protocol_fee_recipient, wsol, ps.quote_token_program)
    cc_auth = creator_vault_authority(ps.coin_creator)
    cc_ata = ata(cc_auth, wsol, ps.quote_token_program)
    bb_ata = ata(ps.buyback_fee_recipient, wsol, ps.quote_token_program)
    head = [
        _m(ps.pool, True),
        _m(user, True, True),
        _m(GLOBAL_CONFIG),
        _m(ps.base_mint),
        _m(wsol),
        _m(user_base, True),
        _m(user_quote, True),
        _m(ps.base_vault, True),
        _m(ps.quote_vault, True),
        _m(ps.protocol_fee_recipient),
        _m(fee_ata, True),
        _m(ps.base_token_program),
        _m(ps.quote_token_program),
        _m(SYSTEM_PROGRAM),
        _m(ATA_PROGRAM),
        _m(event_authority()),
        _m(PUMPSWAP_PROGRAM),
        _m(cc_ata, True),
        _m(cc_auth),
    ]
    if side == "buy":
        tail = [
            _m(global_volume_accumulator()),
            _m(user_volume_accumulator(user), True),
            _m(fee_config()),
            _m(FEE_PROGRAM),
            _m(pool_v2(ps.base_mint)),
            _m(ps.buyback_fee_recipient),
            _m(bb_ata, True),
        ]
    elif side == "sell":
        tail = [
            _m(fee_config()),
            _m(FEE_PROGRAM),
            _m(pool_v2(ps.base_mint)),
            _m(ps.buyback_fee_recipient),
            _m(bb_ata, True),
        ]
    else:
        raise ValueError(side)
    return head + tail


def create_ata_idempotent(payer: Pubkey, owner: Pubkey, mint: Pubkey, token_program: Pubkey) -> Instruction:
    return Instruction(
        ATA_PROGRAM,
        bytes([1]),
        [
            _m(payer, True, True),
            _m(ata(owner, mint, token_program), True),
            _m(owner),
            _m(mint),
            _m(SYSTEM_PROGRAM),
            _m(token_program),
        ],
    )


def sync_native(wsol_ata: Pubkey) -> Instruction:
    return Instruction(TOKEN_PROGRAM, bytes([17]), [_m(wsol_ata, True)])


def close_account(account: Pubkey, destination: Pubkey, owner: Pubkey) -> Instruction:
    return Instruction(TOKEN_PROGRAM, bytes([9]), [_m(account, True), _m(destination, True), _m(owner, False, True)])


def buy_exact_quote_in_data(spend_quote_in: int, min_base_out: int, track_volume: bool = True) -> bytes:
    return DISC_BUY_EXACT_QUOTE_IN + struct.pack("<QQB", spend_quote_in, min_base_out, 1 if track_volume else 0)


def buy_data(base_amount_out: int, max_quote_in: int, track_volume: bool | None = True) -> bytes:
    d = DISC_BUY + struct.pack("<QQ", base_amount_out, max_quote_in)
    return d if track_volume is None else d + bytes([1 if track_volume else 0])


def sell_data(base_amount_in: int, min_quote_out: int) -> bytes:
    return DISC_SELL + struct.pack("<QQ", base_amount_in, min_quote_out)


def compute_budget_ixs(total_priority_lamports: int, cu_limit: int) -> list[Instruction]:
    price = priority_price_for_total(total_priority_lamports, cu_limit)
    return [set_compute_unit_limit(cu_limit), set_compute_unit_price(price)]


def buy_instructions(
    ps: PoolState,
    user: Pubkey,
    sol_in_lamports: int,
    expected_base_out: int,
    max_slippage_bps: int,
    *,
    priority_total_lamports: int = DEFAULT_PRIORITY_TOTAL_LAMPORTS,
    cu_limit: int = DEFAULT_BUY_CU_LIMIT,
    exact_quote_in: bool = True,
    track_volume: bool = True,
) -> list[Instruction]:
    """Spend `sol_in_lamports` (fees come out of it) for at least
    expected_base_out * (1 - slippage) tokens. With exact_quote_in=False uses the
    `buy` instruction instead: ask for expected_base_out, pay at most
    sol_in * (1 + slippage)."""
    if sol_in_lamports <= 0 or expected_base_out <= 0:
        raise ValueError("amounts must be positive")
    wsol_ata = ata(user, ps.quote_mint, ps.quote_token_program)
    if exact_quote_in:
        data = buy_exact_quote_in_data(sol_in_lamports, min_out_with_slippage(expected_base_out, max_slippage_bps), track_volume)
        wrap = sol_in_lamports
    else:
        max_in = max_in_with_slippage(sol_in_lamports, max_slippage_bps)
        data = buy_data(expected_base_out, max_in, track_volume)
        wrap = max_in
    return [
        *compute_budget_ixs(priority_total_lamports, cu_limit),
        create_ata_idempotent(user, user, ps.base_mint, ps.base_token_program),
        create_ata_idempotent(user, user, ps.quote_mint, ps.quote_token_program),
        transfer(TransferParams(from_pubkey=user, to_pubkey=wsol_ata, lamports=wrap)),
        sync_native(wsol_ata),
        Instruction(PUMPSWAP_PROGRAM, data, swap_accounts(ps, user, side="buy")),
        close_account(wsol_ata, user, user),
    ]


def sell_instructions(
    ps: PoolState,
    user: Pubkey,
    token_amount: int,
    min_sol_out: int,
    *,
    priority_total_lamports: int = DEFAULT_PRIORITY_TOTAL_LAMPORTS,
    cu_limit: int = DEFAULT_SELL_CU_LIMIT,
) -> list[Instruction]:
    """Sell `token_amount` raw base tokens; revert if proceeds < min_sol_out lamports.
    Compute min_sol_out with min_out_with_slippage(expected, bps)."""
    if token_amount <= 0 or min_sol_out < 0:
        raise ValueError("bad sell amounts")
    wsol_ata = ata(user, ps.quote_mint, ps.quote_token_program)
    return [
        *compute_budget_ixs(priority_total_lamports, cu_limit),
        create_ata_idempotent(user, user, ps.quote_mint, ps.quote_token_program),
        Instruction(PUMPSWAP_PROGRAM, sell_data(token_amount, min_sol_out), swap_accounts(ps, user, side="sell")),
        close_account(wsol_ata, user, user),
    ]


def build_buy(
    pool_state: PoolState,
    user_pubkey: Pubkey,
    sol_in_lamports: int,
    max_slippage_bps: int,
    expected_base_out: int,
    *,
    priority_total_lamports: int = DEFAULT_PRIORITY_TOTAL_LAMPORTS,
    cu_limit: int = DEFAULT_BUY_CU_LIMIT,
    exact_quote_in: bool = True,
    blockhash: Hash | None = None,
) -> Message:
    """Unsigned legacy Message. `expected_base_out` is the caller's quote (raw tokens)."""
    ixs = buy_instructions(
        pool_state, user_pubkey, sol_in_lamports, expected_base_out, max_slippage_bps,
        priority_total_lamports=priority_total_lamports, cu_limit=cu_limit, exact_quote_in=exact_quote_in,
    )
    return Message.new_with_blockhash(ixs, user_pubkey, blockhash or Hash.default())


def build_sell(
    pool_state: PoolState,
    user_pubkey: Pubkey,
    token_amount: int,
    min_sol_out: int,
    *,
    priority_total_lamports: int = DEFAULT_PRIORITY_TOTAL_LAMPORTS,
    cu_limit: int = DEFAULT_SELL_CU_LIMIT,
    blockhash: Hash | None = None,
) -> Message:
    ixs = sell_instructions(
        pool_state, user_pubkey, token_amount, min_sol_out,
        priority_total_lamports=priority_total_lamports, cu_limit=cu_limit,
    )
    return Message.new_with_blockhash(ixs, user_pubkey, blockhash or Hash.default())


def unsigned_transaction(message: Message) -> VersionedTransaction:
    """Wrap a Message with all-default (zero) signatures. Not valid on chain; only for
    simulateTransaction with sigVerify false."""
    n = message.header.num_required_signatures
    return VersionedTransaction.populate(message, [Signature.default()] * n)


def serialized_size(message: Message) -> int:
    return len(bytes(unsigned_transaction(message)))
