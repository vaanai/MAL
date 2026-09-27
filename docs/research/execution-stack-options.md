# Execution stack options

| | |
| --- | --- |
| **As-of** | 2026-09-25 (vendor pages; fees and slots change — re-read before funding) |
| **Status** | Paper-only. No trading keys on `mal-core-0` until the owner approves live trading. |

## One stack

Stay on the tape until the live bar in the [plan](plan.md) is met. When the owner approves a calibration run:

1. **Signer off the data host.** A new hot keypair lives on the owner’s machine. Oracle emits an unsigned intent; it never sees the secret. Fund at most **~1 SOL**. Sweep anything above the float to a wallet that key cannot spend.
2. **PumpPortal Local** (`POST /api/trade-local`), explicit `pool` `pump` or `pump-amm`, sign locally, send with a paid RPC and a small priority fee. No Lightning, no Jito bundles, no Jupiter on the curve.
3. **Paper fills from our own trade tape** using bonding-curve math, a latency offset, and the full fee stack below. Third-party paper apps are not the scoreboard.
4. **Caps in the signer,** not only in the strategy: 0.05 SOL per trade, daily loss halt, max concurrent positions, kill switch.

Turnkey or Privy is a later upgrade if the signer itself is untrusted. It is the wrong first bill: Turnkey pay-as-you-go is [25 free signatures/month, then $0.10 each](https://www.turnkey.com/pricing).

## 1. Wallet

| Option | What you actually hold | Fit |
| --- | --- | --- |
| **Plain keypair, capped float** | The Solana secret, on a machine that is not Oracle | **First live run.** Loss is bounded by what you deposit. |
| **PumpPortal Lightning API key** | An API key that *contains* the wallet secret, AES-256 encrypted. PumpPortal decrypts it to sign. Not stored on their servers at rest. | Treat as a hot key. A leaked API key drains the wallet. Do not put it on Oracle. Skip: 1% fee and worse custody than Local. [FAQ](https://pumpportal.fun/FAQ/) |
| **Turnkey / Privy server wallet** | An API credential. The Solana key stays in an enclave; policies can allowlist programs. | Right tool once a server must sign. Weak as a *size* cap: a pump buy is not a System `Transfer`, and Privy says transfer-size limits are simulated outside the enclave. [Turnkey](https://docs.turnkey.com/solutions/company-wallets/agentic-wallets), [Privy](https://docs.privy.io/controls/policies/overview) |
| **Hardware wallet** | Clicks on a device | Too slow and too manual for a bot. Fine as the sweep destination. |

**Minimal setup.** One fresh key. Max float ~1 SOL, trades of 0.05 SOL. Secret only on the owner’s signer. Oracle sends `{mint, side, size, max slippage, deadline}`. Signer refuses if the kill switch is off, the daily loss cap is hit, or the built transaction touches a program outside the allowlist (pump `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`, PumpSwap `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`, ATA, compute budget, and System only for rent). Owner moves profits out by hand. Even after live approval, do not put this key on the data host.

## 2. Execution

Edge is seconds versus humans and copy-traders ([plan](plan.md) parks Jito races). A Solana slot is ~400 ms. Landing in the next few slots is enough.

| Path | Extra fee | Landing | Use |
| --- | --- | --- | --- |
| **Local API** | **0.5%** per trade, before slippage, on top of curve fees | You add RPC send. Vendor says trades average **under 1 s**; their fastest recipe (Lightning, Eastern US, no `pool: auto`) is **as low as 1 block**. Phoenix is not that region; measure. | **Yes** |
| **Lightning API** | **1%** per trade | Same vendor path, fanned out to SWQoS, their nodes, and Jito relays | No. Fee and key custody |
| **Direct pump / PumpSwap instructions** | No portal fee | Your RPC only. Must track `buy_v2` / `sell_v2`, fee recipients, cashback accounts | Later, to drop the 0.5%, after Local fills are understood. [pump-public-docs](https://github.com/pump-fun/pump-public-docs) |
| **Jupiter** | Platform fee on the meta-aggregator path | Quote + build, then their landing or yours. [Swap docs](https://dev.jup.ag/docs/swap) | Curve coins are not an aggregator market. Optional price check after graduation only |
| **Jito bundle** | Tip; minimum **1,000 lamports**, more when the auction is hot. Public endpoint rate-limits | Atomic multi-tx. PumpPortal: most single swaps do not need this; Lightning already beats public Jito for one tx | No. Create-and-snipe bundles are the MEV game |

**Costs on a 0.05 SOL curve round trip** (planning numbers, before your own price impact):

- Bonding curve **1.25% each side** (creator 0.30% + protocol 0.95%). [Fees, updated 20 May 2026](https://pump.fun/docs/fees). Low-cap canonical PumpSwap is also ~1.25% and steps down only at much higher mcap.
- Portal Local **0.5% each side**. [PumpPortal fees](https://pumpportal.fun/fees/).
- Combined about **3.5%** round trip before impact. A tape mark of +2% is a loss.
- Priority fee: their examples use **0.00001–0.00005 SOL**. Two of those are ~0.2% of a 0.05 SOL trade. Raise only if your own landing log stalls.
- First buy of a mint locks **~0.002 SOL** rent in the token account (~4% of 0.05 SOL) until you close it. Close on exit or the float fragments.
- On-chain failure still burns the priority fee. A dropped tx (never landed) does not.

Lab fee label `global_95bps` is the **protocol** slice only. A paper book that charges 95 bps and not the creator 30 bps is light.

`pool: "auto"` can add up to ~100 ms on their side. Set `pump` or `pump-amm` from the tape. Trading API cap is 25 requests/s.

## 3. Honest paper

There is no pump.fun paper venue. PumpPortal has no historical API. [Pump.studio](https://pump.studio/blog/paper-trading) papers against live prices; their writeups have used a **1%** fee and a liquidity heuristic, which is not the 1.25% curve and not constant-product impact. [PumpFunData](https://pumpfundata.com/docs) parquet (virtual reserves per swap) is a reasonable replay source if we ever pay for it. Default is our own tape.

Fill rule, per decision at time T:

1. **Latency offset L.** Seed **1 s** until the signer logs a real send-to-land distribution. State is reserves *after* every print with time ≤ T+L. You do not get the price at T.
2. **Curve.** Fees come out of the input, then constant product on virtual reserves: tokens out ≈ `net_in × virtual_tokens / (virtual_quote + net_in)`, capped by real token reserves. Sells run the same math in reverse. Integer math is on-chain; this is the planning form. [Bonding curve](https://pump.fun/docs/bonding-curve).
3. **Your size walks the curve.** Concurrent prints inside the L window are adverse selection. If the post-L quote breaks the slippage cap, the fill is a **miss**, not a worse price.
4. **No partial fills.** AMM buys and sells are all-or-nothing.
5. **Fees.** 1.25% curve (or the PumpSwap tier) + 0.5% portal on each side + priority + rent float. Read `FeeConfig` when coding; the published table is the planning number.
6. **Failed tx.** Until we measure, score the book at 0%, 10%, and 30% fail, and charge the priority fee on the fails. Do not pick the rate that flatters the strategy.
7. **Graduation.** If the curve completes before the sell, a `pool: pump` sell reverts. The sim must switch to `pump-amm` or count a miss.

**What 0.05 SOL live teaches that paper cannot:** the landing-time histogram of *this* signer and RPC; the real fail reasons (slippage, curve already complete, blockhash expired, rent); whether sells land when everyone is exiting; how often a revert burns fees; how much rent strands. It does not prove a signal. Price impact at 0.05 SOL is small on a fresh curve (virtual quote starts in the tens of SOL — read the account, don’t hardcode) and large near completion, so log reserves on every attempt.

## 4. Risk controls (signer-enforced from the first live order)

- Per-trade cap **0.05 SOL**. No retry that increases size.
- Float cap **~1 SOL**. Daily realized-loss halt (suggest **0.2 SOL**), then the signer stops.
- Max concurrent positions (suggest **3**). One position per mint. Hard max hold aligned with the exit grid (ceiling **30 min** for the calibration run).
- Kill switch the Oracle process cannot override (flag on the signer). Default off.
- Program allowlist as in §1. Slippage cap for learning **5–15%**, not 50–99.
- Pre-trade: quote mint is SOL; curve not already complete when buying `pump`; mint and freeze authority null. Token-2022 transfer-fee or non-transferable extensions are a skip. Creator-balance dumps are a heuristic, not a guarantee — pump.fun already revokes mint and freeze on normal creates, so “honeypot” here is mostly a dump, a bundle, or a sell routed at the wrong pool.
- Log every attempt: decision time, send time, land slot, error, reserves before and after, fees paid.

## Sources

- [PumpPortal fees](https://pumpportal.fun/fees/) · [FAQ](https://pumpportal.fun/FAQ/) · [Lightning](https://pumpportal.fun/trading-api/) · [Local](https://pumpportal.fun/local-trading-api/trading-api/) · [Jito bundles](https://pumpportal.fun/local-trading-api/jito-bundles/)
- [pump.fun fees](https://pump.fun/docs/fees) · [bonding curve](https://pump.fun/docs/bonding-curve) · [pump-public-docs](https://github.com/pump-fun/pump-public-docs)
- [Jito send](https://docs.jito.wtf/lowlatencytxnsend/) · [Jupiter swap](https://dev.jup.ag/docs/swap) · [Turnkey pricing](https://www.turnkey.com/pricing) · [Privy policies](https://docs.privy.io/controls/policies/overview)
