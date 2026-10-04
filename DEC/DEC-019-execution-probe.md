# DEC-019: Live execution probe (measurement only)

| Field | Value |
| --- | --- |
| **Status** | **Approved in principle by the owner (2026-10-04, manager session: "Execution probe is an interesting step. I like it.").** Live sends start only after every item in §6 is done. |
| **Decider** | Vaan (owner) |
| **Date** | 2026-10-04 |
| **Amends** | The paper-only fence and the "no trading keys on hosts" rule (CLAUDE.md, CONSTITUTION), **only** for the one probe wallet and executor described here, on `mal-fast-0`, for the duration of the probe. |
| **Does not amend** | The promotion gate. Probe trades are never a book and never count toward any gate, read or promotion. Nothing else in DEC-016/018. |

## 1. Purpose

EXP-012's edge rests on execution: its selected entries filled 98.9% of the time in the replay, against 28.0% unfiltered. Every number so far assumes our simulator's landing, entry price, exit price and fees are right, and no real transaction has been sent. The probe measures those assumptions with real money at the smallest useful size, before any trial depends on them.

## 2. What it measures (per attempt, logged next to the scorer's and the paper runner's simulated fill for the same mint)

1. **Landing.**
   - Landed or failed, with the failure reason.
   - Landed slot minus decision slot.
   - Time from decision to send to confirmation.
2. **Entry.** Tokens received against the simulated quote at slot+1, and against the paper runner's fill, in bps.
3. **Exit.** SOL received against the simulated exit for the same exit rule (tp50/sl30/30-minute cap), in bps.
4. **Fees.**
   - Actual base and priority fees.
   - Pool fees.
   - ATA and WSOL rent charged and refunded.
5. **Realized failure rate against the pressure model's** expected failures for those attempts.

## 3. Scope and hard limits (enforced in code and config, not by a person)

| Limit | Value |
| --- | --- |
| Signals | EXP-012 decisions from the fast-0 paper runner only (**ceiling**-ledger `enter`; manager decision 2026-10-04, replacing "shadow": the ceiling ledger already applies max_concurrent=3, so paper ceiling and probe positions align) |
| Venue | PumpSwap only (the migrate-direct route EXP-012 uses) |
| Size | **0.05 SOL** per entry (one tenth of trial size) |
| Priority | 500,000 lamports per side (the trial term; landing depends on the absolute fee) |
| Concurrent | at most **3** open positions |
| Attempts | at most **30** buy attempts, then the probe stops by itself |
| Loss cap | stop when realized loss reaches **0.25 SOL** |
| Slippage | the simulator's `DEFAULT_SLIPPAGE_CAP`, so we measure the same bound we model |
| Duration | at most 4 days from the first send |
| Kill switch | a stop file, `systemctl stop`, and the two automatic stops above |

## 4. Money

- **Deposit: 0.5 SOL** into a new dedicated wallet.
  - Up to 0.15 SOL is in open positions at once (3 × 0.05).
  - Up to about 0.01 SOL is temporarily in token and WSOL account rent, which is refunded when the accounts are closed after each sell.
  - The probe stops at 0.25 SOL realized loss, so at least about 0.24 SOL always remains.
- **Expected cost.** The priority fees total 0.001 SOL per round trip, about 0.03 SOL over 30 trades, plus pool fees and the net of price moves. The likely net is a small loss of about 0.02–0.10 SOL. The hard worst case is the 0.25 SOL cap.
- **Withdrawal.** At the end, or whenever the owner asks, the remaining balance goes to an address the owner names, using a withdraw script that Helm or the owner runs.

## 5. Custody (the manager never sees the key)

- **A dedicated system user, `mal-live`, on `mal-fast-0`.** Only the executor unit runs as it.
- **Key creation.** The key is generated **by Helm or the owner** with `scripts/mal-fast/make-probe-wallet.sh`, to be added with the executor. That script writes `/etc/mal-probe/probe-wallet.json`, root:root 0400 (see the note below), and prints only the public address.
- *Moved 2026-10-04 after security review: root-owned parent (`/var/lib/mal-live`, was `/var/lib/mal/live`).*
- *Key location, 2026-10-04 (owner and Helm, final): the key is `/etc/mal-probe/probe-wallet.json`, root:root 0400, in `/etc/mal-probe` (root 0700), delivered to the executor by systemd `LoadCredential` (`$CREDENTIALS_DIRECTORY/probe-wallet`); the executor never opens the path itself. Helm sets an auditd watch. State, logs and STOP/HALT stay in `/var/lib/mal-live`.*
- **The key never leaves that file.** It is never in git, logs, MiScusi, status files or any agent's output. The executor loads it in-process only.
- **The executor unit:**
  - is hardened like the runner's;
  - has write access only to `/var/lib/mal-live/`;
  - has no other secret;
  - has outbound network access only.

## 6. Before the first live send (all required)

1. The keyless transaction builder and simulator (PR in progress) are merged.
   - **Gate:** its simulated fills on real pools agree with `paper_curve_math`.
2. The signer/sender executor is merged, with every §3 limit coded and tested, after a `reviewer` pass **and a security review**. The executor:
   - signs with the probe key;
   - sends via Helius `sendTransaction` on the existing plan;
   - confirms by `getSignatureStatuses`;
   - closes accounts after each sell.
3. The probe has run in keyless dry-run on live runner signals for at least 6 hours, with 0 build errors.
4. The fast-0 paper runner is up after the 10-05T05:00Z kill review, with the tip-follower feed.
5. Helm or the owner has generated the key and given the public address, and the owner has funded it with 0.5 SOL.
6. The owner has named the withdraw address.

## 7. Reporting

- A live log on `mal-fast-0`, plus a daily summary for the owner (notebook and Console): attempts, landed, the bps differences, fees and realized P&L.
- At the end: a lab note with every number, reviewed by `quant-proof` before any sentence compares live to paper.
