# Review: pump / PumpSwap / fees redeploy, 2026-10-08T16:20Z

Reviewer: Claude subagent, 2026-10-09T07:30Z. Public RPC and GitHub only. No keys, no sealed data.

## Verdict

**The re-pin is safe for layouts.** The upgrade added multi-hop swaps and pump-coin quote mints. It changed no instruction, account list, event layout, fee config or BOOST parameter that H5, CAP-PICK or the executor uses.

**One material change came with it: synthetic migrations went from 0/61 graduations to 12/61, and the share is rising.** No mechanic breaks. Decidability is at risk through the `synthetic_share_high` halt (0.35).

## 1. What changed

- **Who deployed it.** All three upgrades: signer `6W6qsDbo…` via Squads (`SQDS4ep…`), the same signer as 10-02. Upgrade authority `7gZufwwA…` on all three.
- **Binaries vs the audit baseline** at slot 454,473,446 (`/data/mal/audit-1008/reports/work-md/g_october_structure_check/s04_program_strings.txt`, `s06_strings.txt`):
  - new in pump: `MultiHopCurveSwap`, `SetMaxCurveDepth`, errors 6098–6108 (old table ended at 6097);
  - new in PumpSwap: `MultiHopSwap`;
  - already present: v2/v3 trades, sweeps, BoostBuyAndBurn.
- **pump Global** went from 1,087 to 1,088 bytes. The first 1,087 bytes are unchanged (sha `6afa5f43…`). The new byte is `max_curve_depth = 1`.
- **Docs.** [8cda1fa](https://github.com/pump-fun/pump-public-docs/commit/8cda1fa30ea658b20909d8aedf002047119388d2) (10-07) documents monorepo #60/#62. [2293f9a](https://github.com/pump-fun/pump-public-docs/commit/2293f9a66c654e9fe82dc5e8f4618538f24bb35f) (10-08T18:32Z) adds #63 and says "pump_amm and pump_fees IDLs are unchanged."
- **No X or blog announcement** found.
- **On-chain IDL accounts are stale** (pump 47 instructions, PumpSwap 27). They agree on buy, sell and buy_exact_quote_in.

## 2. Layouts the lab uses

- **Executor.** PumpSwap `buy`, `sell` and `buy_exact_quote_in`:
  - discriminators, the 23/21/23 named accounts and the args are identical in the 09-29 IDL, the 10-08 IDL and on chain;
  - they match `tools/pumpswap_tx.py:79-81, 280-330, 355-365`;
  - the 3 trailing remaining accounts appear in no IDL. Only simulation covers them (5/5 dry runs OK on 10-09).
- **Decoder.** Offsets from the 10-08 IDL match `observe/trade_decode.py:179-217`: BuyEvent ix_name at 401, SellEvent V at 392, creator_fee_unclaimed at 433. PostCompleteBuy at `:237-255` matches too. Length guards are minimums.
- **Across the boundary** (4 blocks each: before, after, now):
  - event lengths are the same: BuyEvent 486 + ix_name, SellEvent 441, TradeEvent 382–397;
  - V and creator_fee_unclaimed decoded on every Buy/Sell event that decoded.
- **Q for H5.** The event's quote reserve equals the vault's pre-trade balance, waiting fees included. It matched preTokenBalances on 20/20 V<0 events and 14/14 v2 events. So quote + V is the docs' [effective reserve](https://github.com/pump-fun/pump-public-docs/blob/main/docs/VIRTUAL_QUOTE_RESERVES_FEE_ADJUSTMENT.md).

## 3. Fees

- **Configs unchanged.** FeeConfig (bonding and PumpSwap) and GlobalConfig sha256 equal the pre-upgrade pins (10-08T10:03Z). Tiers, bps, recipients and the boost flag did not change.
- **v2 fee retention and sweeps** shipped 10-02 ([SWEEP_FEES.md](https://github.com/pump-fun/pump-public-docs/blob/main/docs/instructions/SWEEP_FEES.md)). The recipients are fixed and unchanged.
- **v2 use began after the upgrade:** 0 of about 270 PumpSwap trades before, 5 of about 190 now.

## 4. BOOST

Last slice in seconds after migrate, median [min]:
- pre-upgrade: 332.5 [329], 341.5 [329], 339.5 [330], 346 [338]
- post-upgrade: 343 [329], 344 [329], 340.5 [329], 335 [324]

Budget was 17.586 SOL and 29–30 slices everywhere. InitBoost was on every non-mayhem WSOL graduation. **No change is attributable to the upgrade.**

## 5. Synthetic migration

- **Counts:** 0/61 before. After: 1/15, 3/14, 2/16, 6/16.
- **Which buys:** the latest synthetic completions were BuyExactQuoteInV3 (5) and BuyV3 (1). Before the upgrade, every completion was Buy, CreateV2+Buy or BuyV2.
- **Cause unclear:** v3 was already in the 10-02 binary, so this cannot be separated from SDK adoption.
- **Size (n=9):** pool part 0.02–1.82 SOL. Pools open 0–4.3% above the seed, inside CAP-PICK's 1.15 guard (`tools/cap_pick_score.py:39`).

## 6. Why the shadow and dry runs are not enough

They prove the layouts. They do not prove the population, the halts or the timing margins.

## Residual risks

1. **`synthetic_share_high` is close to firing.**
   - The monitor read 0.316; my same-window sample was 0.375. The limit is 0.35.
   - A halt ends EXP-024 per `EXP/EXP-024-h5-boostfloor-part1-prereg.md:328-331` and withdraws CAP-PICK per `EXP/EXP-022-cap-pick-part1-prereg.md:358`.
   - Manager decision needed before 10-10T00Z.
2. **`pins_changed` is itself an A3 halt before 10-10T00Z.** EXP-024:330 says "H5 is withdrawn." Manager and quant-proof must rule on whether a reviewed re-pin cures it.
3. **Population shift.** The rules' evidence had 0 synthetic pools.
   - **Not measured:** whether s0 (EXP-024:69) lags the migrate tx on synthetic pools. Any lag eats the 330 s margin.
4. **The BOOST margin is thin regardless of the upgrade.** The live halt "<335 s" (`PLAN.md:44`) would have fired on the 10-08T03:40Z window.
5. **Paper fills priced on one V per pool** keep the bias found in audit F2.
6. **The executor's remaining accounts** rest on simulation only. Re-simulate after any deploy.
7. **Not this upgrade:** the 200 ms slot step at epoch 1053 (about 10-09T14:40Z).

## Re-pin recommendation

- Re-pin the deploy slots.
- Also pin the program sha256 values (`8c8f6aae…`, `868a875e…`, `4757ce83…`). The `program_changed` rule is not evaluated today.

Scripts: the session scratchpad's `work/` directory (`prepost`, `boostwin`, `blocks`, `vaultconv`, `synsize`, `scan`, `idldiff`).
