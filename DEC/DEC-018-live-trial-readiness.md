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

## Amendment 1 (2026-10-06): owner answers; size ladder; stops set by the manager

**Owner (2026-10-06, manager session):** "lets step up through 0.25 first. Incremental as we improve the system. I don't know about the stop levels, I'll let you decide on that."

**§3 decisions, as they now stand:**
1. **Keys on the host:** in place for one dedicated wallet on `mal-fast-0`, under the DEC-019 custody Helm set up. The key is root-only, reaches the executor only through systemd `LoadCredential`, and is never read by Claude. Helm's root lockdown comes before any bigger wallet (DEC-020 §7 item 7).
2. **Custody:** as DEC-019 (Helm-generated, root-owned 0400, never in git or MiScusi).
3. **Funding:** the owner's, per rung. There is no new funding before the 10-16 read (DEC-020 Option A).
4. **Send path:** as the probe uses it: Helius `sendTransaction`, 500,000 lamports priority per side, no tip.
5. **Stops:** delegated to the manager. They are set per rung, below.

**Size ladder.** §2's 0.5 SOL per entry is the **third** rung, not the first:
- **Rung 1:** the 0.05 SOL execution probe (DEC-019), live until 2026-10-12T00Z at the latest.
- **Rung 2:** 0.25 SOL per entry (DEC-020), after EXP-012's FINAL forward read PASSes and quant-proof agrees.
- **Rung 3:** 0.5 SOL per entry (§2), only after rung 2 has run its course without hitting a stop, and live−sim calibration at 0.25 is within the DEC-021 drift limit. Each rung's size and funding is a separate owner yes.

**Stops, set by the manager per the owner's delegation, all enforced in executor code:**
- **Rung 2 (0.25 SOL):**
  - **Already in DEC-020 §3:** loss cap 0.35 SOL realized on the step's own counter; 40 attempts; max 2 open; Helm watchdog alert at 0.25 SOL.
  - **No daily cap at this rung.** One stop costs about 0.075–0.10 SOL, so the 0.35 total cap almost always fires first.
  - **Added, new executor code before 10-16 (review plus security review):**
    - **Divergence stop (executor, `DEC020_STOPS`):** after 10 closed step trades, halt new buys if the mean `entry_vs_quote_bps` is below −200, or the mean `exit_vs_quote_bps` on landed sells is below −200. Both fields are (actual − quote) / quote, so a negative value is worse (fewer tokens than quoted on entry, less SOL than quoted on exit).
    - **Landing-fail stop (executor):** after 10 resolved buy attempts, halt new buys if more than 30% did not land (on-chain failure or expired unlanded).
    - **Live−sim residual (manager, not executor):** the manager's calibration (`tools/probe_sim_calibration.py`) runs every 5 step trades. If the mean live−sim P&L residual is below −0.0075 SOL per trade (3% of 0.25) after 10 closed trades, the manager places the STOP file under this amendment's owner-delegated authority and tells the owner and Helm the same hour.
    - The executor stops are latched in the dec020 state, survive restarts, and halt new buys only. Exits continue under every halt. Config may make them stricter, never looser.
- **Rung 3 (0.5 SOL):** §2's proposed stops: daily 0.75, total 1.5, plus the landing-fail and divergence stops at 30. They are re-checked against rung 2's measured costs before that rung is proposed.

**Why the manager chose these levels.**
- The total caps bound the money at risk.
- The two new stops cover the risk the caps don't: live trading quietly running worse than the simulator. Only real fills at size can show it.
- The 10-trade trigger is early enough to act within the 40-attempt rung, and late enough not to fire on a single bad fill.
