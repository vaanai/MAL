# DEC-018: Live-trial readiness for EXP-012 (proposal)

| Field | Value |
| --- | --- |
| **Status** | **Proposed.** Nothing here trades. No key is created or stored by this DEC. It lists what must exist before a first live order, so a pass on ~2026-10-16 does not wait weeks on plumbing. |
| **Decider** | Vaan (owner). The manager prepares. |
| **Date** | 2026-10-04 |
| **Does not amend** | The promotion gate; DEC-016 and its Amendments 1–3, including (c); the paper-only fence, until the owner amends it in writing (see §3). |

## 1. When a live trial may start (all of these, in order)

1. EXP-012's FINAL forward read, about 2026-10-16T02Z, is a **PASS** under both fail models (DEC-016 Amendment 1), and `quant-proof` agrees.
2. DEC-016 Amendment 3 (a), the latency/size re-score at the measured fast-0 k(p50)/k(p90) and the owner's trial terms, **supports live**.
3. DEC-016 Amendment 3 (b), runner-vs-scorer rows 0–6, **all hold**.
4. The owner says yes, in writing, to a trial with the terms in §2.

If any step fails, there is no live trial, and the result is reported the same day.

## 2. Trial terms (owner-named 2026-10-03, #262) and proposed limits

- **Fixed:**
  - 0.5 SOL per entry;
  - at most 3 positions at once;
  - priority 500,000 lamports per side;
  - no tip.
- **Proposed bankroll:** 2.0 SOL in a dedicated wallet. That covers 3 × 0.5 SOL open, plus fees and ATA rent, plus a buffer.
- **Proposed stops**, enforced by the executor and not by a person:
  - **daily loss cap:** 0.75 SOL realized. Trading pauses until 00:00Z.
  - **total stop:** realized loss of 1.5 SOL since the trial started. Trading halts until the owner restarts it.
  - **landing-fail stop** (DEC-016 Amendment 3 (c)): after 30 live attempts, halt if realized failures exceed the pressure model's expected failures on those attempts by more than 10 pp.
  - **divergence stop:** after 30 fills, halt if the mean realized entry price is more than 2% worse than the scorer's simulated entry for the same mints.
- **Review.** Daily, with the same gate arithmetic as paper. Scale-up is a separate owner decision after at least 100 live trades.

## 3. Owner decisions needed (only the owner can make these)

1. **Amend the hard rule** "no trading keys on the hosts" (CLAUDE.md, CONSTITUTION) for one dedicated trial wallet on `mal-fast-0` only.
2. **Custody.** Who generates the key, and where it lives. Proposed: generated on `mal-fast-0` by the owner or Helm, in a root-owned file readable only by the executor unit's user (mode 0400), never in git, never printed, never in MiScusi.
3. **Funding.** The owner funds the wallet with the bankroll and can withdraw at any time.
4. **Send path.** Proposed: Helius `sendTransaction` on the existing plan, with the 500,000-lamport priority fee and no Jito tip, matching the trial terms the re-score uses.
5. **The proposed stops in §2** (bankroll, daily cap, total stop).

## 4. What the manager builds now (paper-safe, no keys)

1. **Live executor, keyless dry-run mode.** It builds the exact buy and sell transactions the runner would send for each EXP-012 decision, and runs Solana `simulateTransaction` (sig-verify off) against the live chain. It records success, compute units, the simulated fill against the scorer's simulated fill, and errors. No key is needed, and nothing is sent.
   - This proves instruction building, slippage limits and account handling on real pools before any money exists in a wallet.
   - It runs on `mal-fast-0` next to the paper runner, with its own slice and limits.
2. **Kill switch:**
   - a stop file and a systemd stop;
   - the §2 stops, wired into the executor;
   - a test for each.
3. **Live fill log.** Every attempt and fill gets slot, pre-fee spot, fees and outcome, next to the scorer's simulated fill for that mint. This is the DEC-016 Amendment 3 (c) record.
4. **A one-page runbook:** start, stop, withdraw, and what to do on each stop.

Items 1–4 are reviewed (`reviewer`, with a security review for anything that later touches keys) and merged before 2026-10-16. The live mode itself (signing and sending) is written only after the owner's §3 decisions, behind a flag that defaults to off.

## 5. Honest expectations

The replay read (n = 451) showed +0.0205 SOL/trade under the pressure model. The forward read decides whether that holds. The trial size and the 3-concurrent cap may lower trade count and per-trade net (the re-score measures both). Real landing failures are measured for the first time live. The trial exists to find out whether paper matches reality cheaply, not to make the owner's target in week one.
