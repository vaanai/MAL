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

- *2026-10-05: signals come from the runner's decision-time `intents.jsonl` (PR #307), not from `enter` rows written after the simulated latency. The live executor therefore acts on every ceiling-ledger migrate decision at decision time, including mints the paper runner later skips (kill switch, missed slippage at its simulated time, no price). Live fills are not a subset of paper fills. They are bounded by the executor's own limits, STOP/HALT and `already_bought`. The section 7 comparison joins live vs paper by mint and reports both the matched set and the live-only set.*

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

- *Wallet created 2026-10-04 by Helm (§6 item 5, first half):*
  - Public key `5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk`.
  - Installed from a root-owned clone at `d5085b48aa6ffa2eaf781b5ddae6481a7eca2cb3` (HEAD check ok, manifest verified). The four installed sha256s match the manager's manifest.
  - `mal-live` is uid 999 (nologin, no extra groups). `/var/lib/mal-live` is mal-live 0700. The key is root:root 0400 in a root:root 0700 dir. Helm checked that neither Claude nor `mal-live` can read it.
  - **Not funded.** Funding waits for a clean 6 h keyless dry run (§6 item 3). The live drop-in stays off until Helm checks its hashes against the deployed commit.

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

- *§6 status, 2026-10-05:*
  - Items 1–2: merged with reviewer and security reviews (#280, #286, #290, #293, #289, #291).
  - **Item 3: DONE.** 6 h keyless dry run on live runner signals, 0 build errors, 0 pre-signing failures. Jobs #130 (6 h mark, 10 buys / 10 sells) and #136 (through 14:37Z, 20 / 20). Evidence: `ARTIFACTS/lab/probe-dryrun-2026-10-05.md`.
  - Item 4: runner up on the tip follower (coverage 100.000%, job #131).
  - Items 5–6: Helm created the key and checked the hashes at `8a6849b`; Vaan funded 509,528,770 lamports; the withdraw address is held by Helm.
  - **Live since 2026-10-05T14:46:17Z.**
- *Timing note for §7:* the executor reads pool state about 250 ms after the paper runner's booked entry (p50: decision → seen 2,055 ms against applied 1,869 ms; seen → state 62.5 ms). The live-versus-paper comparison counts that gap against paper as latency, not slippage.

## 7. Reporting

- A live log on `mal-fast-0`, plus a daily summary for the owner (notebook and Console): attempts, landed, the bps differences, fees and realized P&L.
- At the end: a lab note with every number, reviewed by `quant-proof` before any sentence compares live to paper.

- *2026-10-05 (owner's PR checker):* the §7 lab note reports attempts **grouped by executor build sha, never pooled**. Group `8a6849b` (pre-#307, about 14–15 slots of latency) is in `ARTIFACTS/lab/probe-live-2026-10-05.md`. Later groups are keyed by the pinned `<sha>` in use when each fill row was made.

- *Note 2026-10-05 (base unit, key env file, kill file):* the pinned drop-in inherits `User=`, `Environment=`, `EnvironmentFile=` and hardening from `mal-probe-executor.service`, so while it is present `install-fast-forward-paper.sh` no longer installs that base unit (it re-evaluates the drop-in verdict, over `/etc`, `/run` and `/usr/lib`, right before the unit install; `none` means no drop-in sets `LoadCredential` or an `ExecStart`, and the key only arrives via `LoadCredential`). Only `install-probe-executor-pinned.sh` writes it: mandatory sha256 manifest, an allowlist check (`check-probe-base-unit.py`, in the manifest) that the unit equals the intended text, install before the `current` switch, rollback restores the previous unit. The key holder loads `/etc/mal-probe-rpc/helius.env` (`root:root` 0600 in a `root:root` 0700 dir), not the ubuntu-writable `/var/lib/mal/fast-listener/helius.env`; a root-run inline `ExecStartPre=+` checks both modes and the executor refuses live start (`startup_refused rpc_env`) if the dir is wrong. `Slice=mal-forward.slice` was removed from the unit so the key holder is not in a slice the paper installer writes; it falls back to `system.slice` and keeps its own `MemoryMax=1G`. The paper runner's KILL file is not a reliable stop for live: `runner_kill` in `intents.jsonl` covers only the race between the runner's risk check and the intent write (KILL present at decision time already suppresses the intent). `/var/lib/mal-live/STOP` is the real stop. Hardening added on re-review: the unit checker reads bytes and refuses anything outside TAB, LF and 0x20-0x7e (Python and systemd disagree on Unicode whitespace, lone CR, VT, FF); the root `ExecStartPre=+` runs under `env -i` with absolute paths (the EnvironmentFile applies to it too); live refuses without `HELIUS_API_KEY` in the environment (`rpc_key_missing`) and never falls back to an env file; the pinned installer pre-flights `/etc/mal-probe-rpc` (root:root 0700, `helius.env` root:root 0600) and rolls back on INT/TERM/HUP.

## Amendment 1 (PROPOSED 2026-10-05, awaiting owner approval)

**Status: PROPOSED. Not approved. Nothing here takes effect until the owner approves and the manager re-pins the executor.** The text above is not changed.

### Numbers

| Limit | Now | Proposed |
| --- | --- | --- |
| max_attempts (whole probe) | 30 | **90** (+60; the 15 attempts already used keep counting, state is not reset) |
| loss_cap_lamports (total realized loss, same semantics) | 250,000,000 | **350,000,000** |
| priority_lamports per side | 500,000 | **150,000** (the code clamp stays at 500,000 as a maximum; lower values are accepted) |
| max_days (counted from `first_attempt_ms` in state) | 4 | cap raised to 7, plus an absolute end instant **2026-10-12T00:00:00Z** (`end_ms` 1791763200000) |
| size_lamports, max_open, slippage_cap, STOP/HALT semantics | 50,000,000 / 3 / 0.15 / as is | **unchanged** |

End instant, why absolute: the first attempt was 2026-10-05T14:49Z (`first_attempt_ms` 1791211793036). A days count from that instant gives either 4 days (ends 10-09T14:49Z, likely before the attempts run out) or 7 days (ends 10-12T14:49Z, past the requested bound). An absolute instant ends the probe at the stated time. The max_days cap (7) stays as a second, independent stop; whichever fires first halts new buys. Config may lower `end_ms`, never raise it above the code constant. The stop reason is logged as `end_instant`.

### Reason

Build a25eb17 execution now tracks paper. Over 9 trades: sells were -11 to -16 bps against the quote on 7 of 9, and the stops were -0.3039 to -0.4338. The fee drag is 1,010,000 lamports per round trip (2 x 500,000 priority + 2 x 5,000 base), which is 2.02% at 0.05 SOL, comparable to the roughly 1.5% per trade exploration edge at k about 8. At 150,000 per side the round-trip drag is 310,000 lamports (0.62%). More attempts give each build group enough rows to read. This is an execution measurement, not an edge claim.

### Stop rule for the lower priority

If more than 20% of buys sent under 150,000 land more than 3 slots after the send-state slot, or any buy expires, the manager reverts priority to 500,000 by re-pin. Fill rows now carry `priority_lamports` (the configured per-side value) on buys and sells, next to the existing base/priority fee split, so landing time can be grouped by priority.

### Reporting and gate

- Attempts stay grouped by executor build sha and are also grouped by `priority_lamports`; never pooled.
- Probe trades still never count toward any promotion gate.
