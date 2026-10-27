# P83 VOLUME-ACCRUAL: desk check and two public account reads

Lane P83, manager17, 2026-10-27T14:16Z (`date -u` 14:16:14). Row `p81-volume-accrual`. No trade, no pricing, no tape row, no Helius, no sealed, forward, holdout or meta-capture path opened. Nothing here says a book makes money; no book has cleared the promotion gate.

## Verdict: INACTIVE

Both `GlobalVolumeAccumulator` accounts exist, are initialised, and hold only zeros. Reward per SOL of volume is 0, so no tier has reward above cost.

## 1. Mechanism (from `tools/fixtures/boost_watch/idl_pump_amm.json`; the pump IDL has the same names per P81)

- Global account: `GlobalVolumeAccumulator`, PDA seed `"global_volume_accumulator"` under each program. Fields: `start_time` i64, `end_time` i64, `seconds_in_a_day` i64, `mint` pubkey, `total_token_supply[30]` u64, `sol_volumes[30]` u64. So 30 program days, each with a token supply and a running SOL volume.
- Reward vault: `global_incentive_token_account`, the ATA of (global_volume_accumulator, token_program, mint). Holds the incentive tokens.
- Per user: `UserVolumeAccumulator`, PDA seed `"user_volume_accumulator"` + user. Fields: `needs_claim`, `total_unclaimed_tokens`, `total_claimed_tokens`, `current_sol_volume`, `last_update_timestamp`, `has_total_claimed_tokens`, plus cashback fields (`cashback_earned`, `total_cashback_claimed`; a separate mechanism).
- Accrual: buys and sells pass the global and user accumulators; `init_user_volume_accumulator` creates the user account, `sync_user_volume_accumulator` rolls the user's volume into unclaimed tokens against the day's supply and volume (event carries claimed tokens before/after). The exact share formula is not in the IDL (program code is not in the repo); the P81 form, user share of the day's `sol_volumes[d]` times `total_token_supply[d]`, is a structural reading, not verified.
- Rate per period: set by the admin only, `admin_update_token_incentives(start_time, end_time, seconds_in_a_day, day_number, token_supply_per_day)`.
- Claim: `claim_token_incentives` moves tokens from the global incentive vault to the user ATA. `close_user_volume_accumulator` closes the user account (event reports unclaimed and claimed tokens).

## 2. Live state (public RPC getAccountInfo, api.mainnet-beta.solana.com; 2 calls of the 20 allowed)

| Program | PDA | Owner | Slot | Data |
| --- | --- | --- | --- | --- |
| pump `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` | `Hq2wp8uJ9jCPsYgNHex8RtqdvMPfVGoYwjvF1ATiwn2Y` | pump program | 462100944 | 600 bytes, discriminator `ca2af62b8ebe1eff` matches the IDL, 0 nonzero bytes after it |
| pump AMM `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA` | `C2aFPdENg4A2HQsmrd5rTw5TaYBX5Ku887cWjbFKtZpw` | pump AMM program | 462100945 | same: 0 nonzero bytes |

Decoded (both): `start_time` 0, `end_time` 0, `seconds_in_a_day` 0, `mint` = system program (all zero), all 30 `total_token_supply` entries 0, all 30 `sol_volumes` entries 0. Lamports 17,016,138 (pump) and 28,668,918 (AMM) are rent only. So no incentive window is configured and no PUMP mint is attached on either program, now.

Not measured: whether a programme ran earlier and was reset (a zeroed array would hide it); whether the on-chain program still accrues volume into these fields; the reward vault ATA (not derived, not read; with mint zero there is nothing to read). PUMP price was not needed (reward is 0 before any price). The P76 pool 2uF4X is PUMP/USDC and was not read here.

## 3. Arithmetic

Reward per SOL of volume r = 0 (supply 0). Cost of one atomic buy+sell at 1 SOL stake, fees from `tools/paper_curve_math.py` `PUMPSWAP_SOL_FEE_TIERS` (canonical, total of creator + protocol + LP, ppm per leg):

| Market cap tier (SOL) | per leg | two legs |
| --- | --- | --- |
| 0 | 1.2500% | 2.5000% |
| 420 | 1.2000% | 2.4000% |
| 4420 | 1.0000% | 2.0000% |
| 9820 | 0.9500% | 1.9000% |
| 98240 and up | 0.3000% | 0.6000% |

Plus priority fee: `PRIORITY_FEE_LAMPORTS` 1,000,000 = 0.001 SOL = 0.1000% of 1 SOL (the lab default; one transaction, so paid once). Tip: no constant in the repo for a tip on this path; not included (unknown, only adds cost). Impact not modelled. Cheapest floor in the table: 0.6000% + 0.1000% = 0.7000% of volume (70 bps), non-canonical flat pools 0.30% per leg give the same 0.60%.

Is there a tier where reward exceeds cost? No: 0 against a minimum of about 70 bps. Break-even would need `total_token_supply[d] x PUMP_in_SOL / sol_volumes[d]` of at least 0.0070 SOL per SOL of volume, i.e. tokens worth 0.70% of that day's venue SOL volume.

Volume and dilution, as a formula only (inputs are zero, so no number follows): if our daily volume v shares a day with other volume V and the day's reward is S (in SOL), our reward is S v / (V + v), net = S v / (V + v) - c v with c at least 0.0070. Net is positive only if S / (V + v) > c, i.e. S > c (V + v). So the required S grows with all other farmers' volume V. With S = 0 there is no volume at which it is positive.

Revive trigger: `total_token_supply` nonzero and `mint` set on either accumulator (a one-call recheck of the two PDAs above, or an `admin_update_token_incentives` transaction on either program).

## 4. Policy note

Self-trading a buy and sell in one transaction to collect a platform volume reward is wash-style volume. It may violate pump.fun's terms of service and could be treated as manipulation of platform metrics. The owner must decide before anything is ever tested live. Nothing here designs, builds or proposes a bot; the programme is inactive, so the question is not currently live.

## Receipts

- IDL: `tools/fixtures/boost_watch/idl_pump_amm.json` (instructions `admin_update_token_incentives`, `claim_token_incentives`, `init_/sync_/close_user_volume_accumulator`; account types above).
- PDAs derived with `solders` `find_program_address([b"global_volume_accumulator"], program)`.
- Read: two `getAccountInfo` calls (base64) to the public mainnet-beta endpoint, 2026-10-27T14:16Z.
