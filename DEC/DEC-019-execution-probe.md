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
| Priority | 500,000 lamports per side (the trial term; landing depends on the absolute fee) *(superseded by Amendment 1: see below)* |
| Concurrent | at most **3** open positions |
| Attempts | at most **30** buy attempts, then the probe stops by itself *(superseded by Amendment 1: see below)* |
| Loss cap | stop when realized loss reaches **0.25 SOL** *(superseded by Amendment 1: see below)* |
| Slippage | the simulator's `DEFAULT_SLIPPAGE_CAP`, so we measure the same bound we model |
| Duration | at most 4 days from the first send *(superseded by Amendment 1: see below)* |
| Kill switch | a stop file, `systemctl stop`, and the two automatic stops above |

## 4. Money

- **Deposit: 0.5 SOL** into a new dedicated wallet.
  - Up to 0.15 SOL is in open positions at once (3 × 0.05).
  - Up to about 0.01 SOL is temporarily in token and WSOL account rent, which is refunded when the accounts are closed after each sell.
  - The probe stops at 0.25 SOL realized loss, so at least about 0.24 SOL always remains. *(superseded by Amendment 1: see below)*
- **Expected cost.** The priority fees total 0.001 SOL per round trip, about 0.03 SOL over 30 trades, plus pool fees and the net of price moves. The likely net is a small loss of about 0.02–0.10 SOL. The hard worst case is the 0.25 SOL cap. *(superseded by Amendment 1: see below)*
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

- *Note 2026-10-06 (manager8; reporting only, no change to the running probe).*
  - **Daily-cap gating.** The signal source (the ceiling-ledger `enter`, line 35) was pre-registered. Its consequence was not written down: the ceiling ledger's `daily_loss_cap` (daily_loss_sol 0.2) also gates every live intent. It fired at 2026-10-06T08:13–08:15Z, and the probe has been idle since. It resumes at the 00:00Z runner restart.
  - **Hour-of-day bias.** Because of this gating, the faa3192 attempts were drawn mostly from the hours before the cap fired, about 00–08Z. The §7 lab note must report the hour-of-day distribution of attempts and flag this sampling bias.
  - **Mid-probe changes.** None are made (DEC-021 §7).
  - **Fee split for faa3192.** 28 closed round trips, net −14,007,586 lamports (job #264).
    - Tx fees are 28 × 1,010,000 = 28,280,000 lamports: 2 × 505,000 per round trip, 2.02% of 0.05 SOL.
    - So the result before tx fees is +14,272,414 lamports. Fixed tx fees, not price, keep this build below zero.
    - *2026-10-07, supersedes the line above.* That line held at the 10-06 cut (28 trips, +14,272,414 before tx fees). On the final data it does not: faa3192, 33 trips, realized −56,887,764; tx fees 33 × 1,010,000 = 33,330,000; before tx fees −23,557,764, still negative. The 5 trips after the 28 realized −42,880,178 (−37,830,178 before fees). Fixed tx fees do not explain the faa3192 loss: before tx fees it is −23,557,764. That remainder includes about 40.0M of estimated pool fee (33 × 1,212,195), which scales with stake like price does, so a bigger stake does not by itself fix it. The sign turns on 5 trips (4 stops). See [probe-final-2026-10-07.md](../ARTIFACTS/lab/probe-final-2026-10-07.md).
    - At the DEC-020 0.25 SOL step the same fee is 0.40% of size, and at 0.5 SOL it is 0.20%.
    - An extra ~1.51M lamports debited per buy (an auditor's chain reconciliation) is being decomposed: refundable rent versus cost. The result goes in the §7 note.
  - **Stale signals.** The executor's `max_signal_age_s` is 120 (`scripts/mal-fast/probe-executor-live.json`). A signal decided 6.2 s after migration (8XQy9ebn, 21:35:50Z) was traded, for −30.6%. Not changed mid-probe. The trial config (DEC-020 package) must set a tight stale-signal and maximum-k guard, sized from the measured k distribution (p50 5, p90 6, max 15 on faa3192; job #285), and keep it under its own review.
- *Note 2026-10-05 (base unit, key env file, kill file):* the pinned drop-in inherits `User=`, `Environment=`, `EnvironmentFile=` and hardening from `mal-probe-executor.service`, so while it is present `install-fast-forward-paper.sh` no longer installs that base unit (it re-evaluates the drop-in verdict, over `/etc`, `/run` and `/usr/lib`, right before the unit install; `none` means no drop-in sets `LoadCredential` or an `ExecStart`, and the key only arrives via `LoadCredential`). Only `install-probe-executor-pinned.sh` writes it: mandatory sha256 manifest, an allowlist check (`check-probe-base-unit.py`, in the manifest) that the unit equals the intended text, install before the `current` switch, rollback restores the previous unit. The key holder loads `/etc/mal-probe-rpc/helius.env` (`root:root` 0600 in a `root:root` 0700 dir), not the ubuntu-writable `/var/lib/mal/fast-listener/helius.env`; a root-run inline `ExecStartPre=+` checks both modes and the executor refuses live start (`startup_refused rpc_env`) if the dir is wrong. `Slice=mal-forward.slice` was removed from the unit so the key holder is not in a slice the paper installer writes; it falls back to `system.slice` and keeps its own `MemoryMax=1G`. The paper runner's KILL file is not a reliable stop for live: `runner_kill` in `intents.jsonl` covers only the race between the runner's risk check and the intent write (KILL present at decision time already suppresses the intent). `/var/lib/mal-live/STOP` is the real stop. Hardening added on re-review: the unit checker reads bytes and refuses anything outside TAB, LF and 0x20-0x7e (Python and systemd disagree on Unicode whitespace, lone CR, VT, FF); the root `ExecStartPre=+` runs under `env -i` with absolute paths (the EnvironmentFile applies to it too); live refuses without `HELIUS_API_KEY` in the environment (`rpc_key_missing`) and never falls back to an env file; the pinned installer pre-flights `/etc/mal-probe-rpc` (root:root 0700, `helius.env` root:root 0600) and rolls back on INT/TERM/HUP.

- *§7 closing, 2026-10-07.* The owner stopped the probe early; Helm placed STOP at 2026-10-07T01:10:50Z. Final: 62/90 live attempts, 61 closed round trips plus one failed buy, realized −0.210755 SOL, 0 open, last build `faa3192`. The closing lab note is [probe-final-2026-10-07.md](../ARTIFACTS/lab/probe-final-2026-10-07.md): per-build P&L (never pooled), fee split (round trip 4.44% of stake at 0.05 SOL, projected 2.79% at 0.25 and 2.59% at 0.5; rent is refundable, net 0), exit lag in slots (p50 2 for stops, p90 2, max 2 on the fixed builds), and what the next live build must change. Execution measurement, not edge evidence; `quant-proof` has not reviewed it.

## Amendment 1 (APPROVED by the owner 2026-10-05 (~20Z))

**Status: APPROVED by the owner 2026-10-05 (~20Z). It takes effect when the manager re-pins the executor at the merge sha.** The text above is not changed.

### Numbers

| Limit | Before | Amendment 1 |
| --- | --- | --- |
| max_attempts (whole probe) | 30 | **90** (+60; the 15 attempts already used keep counting, state is not reset) |
| loss_cap_lamports (total realized loss, same semantics) | 250,000,000 | **350,000,000** |
| priority_lamports per side | 500,000 | **150,000** (the code clamp stays at 500,000 as a maximum; lower values are accepted) |
| max_days (counted from `first_attempt_ms` in state) | 4 | cap raised to 7, plus an absolute end instant **2026-10-12T00:00:00Z** (`end_ms` 1791763200000) |
| size_lamports, max_open, slippage_cap, STOP/HALT semantics | 50,000,000 / 3 / 0.15 / as is | **unchanged** |

End instant, why absolute: the first attempt was 2026-10-05T14:49Z (`first_attempt_ms` 1791211793036). A days count from that instant gives either 4 days (ends 10-09T14:49Z, likely before the attempts run out) or 7 days (ends 10-12T14:49Z, past the requested bound). An absolute instant ends the probe at the stated time. The max_days cap (7) stays as a second, independent stop; whichever fires first halts new buys. Config may lower `end_ms`, never raise it above the code constant. The stop reason is logged as `end_instant`.

### Reason

Build a25eb17 execution now tracks paper. Over 9 trades: sells were -11 to -16 bps against the quote on 7 of 9, and the stops were -0.3039 to -0.4338. The fee drag is 1,010,000 lamports per round trip (2 x 500,000 priority + 2 x 5,000 base), which is 2.02% at 0.05 SOL, comparable to the roughly 1.5% per trade exploration edge at k about 8. At 150,000 per side the round-trip drag is 310,000 lamports (0.62%). More attempts give each build group enough rows to read. This is an execution measurement, not an edge claim.

### Worst case

The loss cap is checked on realized loss at buy time, so up to 3 open positions (about 156M lamports at cost: 3 x (50,000,000 + fees and rent)) can add to it before they close. The wallet balance guard (size + 0.02 SOL) is the final bound.

### Stop rule for the lower priority

The manager's hourly monitor and a per-build report evaluate this rule. The dry-run config (`probe-executor.json`) intentionally keeps priority at 500,000.

If more than 20% of buys sent under 150,000 land more than 3 slots after the send-state slot, or any buy expires, the manager reverts priority to 500,000 by re-pin. Fill rows now carry `priority_lamports` (the configured per-side value) on buys and sells, next to the existing base/priority fee split, so landing time can be grouped by priority.

### Reporting and gate

- Attempts stay grouped by executor build sha and are also grouped by `priority_lamports`; never pooled.
- Probe trades still never count toward any promotion gate.

## Amendment 1a (2026-10-05 ~20:30Z): loss cap stays 0.25 SOL, priority stays 500k; record and wallet floor

**This replaces two Amendment 1 values before Amendment 1 was ever installed.** Helm did not run the installer for `f87eb48`. Amendment 1's attempts (90), absolute end instant (2026-10-12T00:00:00Z) and max_days (7) stand. The code maxima are unchanged: loss cap max 350,000,000, priority max 500,000. The live config sets:

| Limit | Amendment 1 (never installed) | Amendment 1a (live config) |
| --- | --- | --- |
| loss_cap_lamports | 350,000,000 | **250,000,000** (the original cap) |
| priority_lamports per side | 150,000 | **500,000** (the original value) |

**Decision.** The owner left the cap to the manager. The manager chose (b): 0.25 SOL and 500k. An external review of #321 prompted this. The reasons follow.

**Probe state when the decision was made** (job #176, 2026-10-05T20:16Z, pinned `--status` plus a getBalance of the wallet):
- attempts 20/30, realized −0.091914 SOL, 1 open (open exposure 0.050000 SOL at cost), STOP present (Helm), executor on `a25eb17`;
- wallet balance 365,595,877 lamports. The deposit was 509,528,770.
- By build: `8a6849b` had 6 trades and −100,536,289 lamports; `a25eb17` had 13 closed trades and +8,622,236 lamports.
- The probe was **not** near the 0.25 SOL cap. Keeping 0.25 still leaves 0.158 SOL of realized-loss room.

**Wallet floor** (this corrects §4, which ignored open positions):
- Hard worst case: deposit − cap − 3 open positions going to zero, i.e. 0.5095 − 0.25 − 3 × 0.052 ≈ **0.10 SOL** left.
- A more realistic bad case, with the open positions stopping near −40%: about **0.20 SOL** left.
- At the 0.35 cap the same cases leave about 0.004 SOL and 0.10 SOL. That is why the cap stays at 0.25.

**Why priority stays at 500k:**
- The 2.02% fee drag is an artifact of the 0.05 SOL probe size. At the 0.5 SOL trial size the same fee is about 0.2%.
- EXP-012's edge depends on landing fast, so the probe keeps the landing conditions a trial would use.
- Baseline at 500k (job #176, a25eb17): **14/14 buys landed 1–3 slots after the state read, 0 more than 3, 0 expiries**. The 8a6849b buys all landed 2 slots after.
- P&L net of the priority fee is reported as arithmetic from the logged fee split. The fee itself is not changed.
- The Amendment 1 stop rule for 150k no longer applies.

**Monitoring.** The manager's hourly monitor is session-bound. The hard limits (attempts, loss cap, end instant, STOP/HALT) are enforced in code and do not depend on it. A durable root timer that alerts Discord on probe thresholds is requested from Helm.

## Mark source (2026-10-05)

- The position mark (the reference for the tp +0.5 and sl -0.3 returns) is the **post-buy spot from our own buy transaction**: `(quote_vault_post + V) / base_vault_post`, read from the pool vaults' `meta.postTokenBalances` of the landed buy tx, V-priced, in `spot_sol_per_ui` units. This is the paper rule's post-buy spot with no post-landing drift. If either vault balance is missing or unparseable the mark falls back to the effective fill price (`net_in / tokens`), flagged `mark_source: "fill_price"` (normal case `"buy_tx_post"`). The mark is final at position creation; the send-state mark is kept as `mark_send` and `mark_shift_bps` is logged on the buy and sell rows. Positions persisted by older builds (no `mark_source`) behave as before; dry run is unchanged.
- Starts in build: **`faa319227eee420319eed06e85774f64dd2273b1`**, live since 2026-10-05T23:09:56Z (Helm). The #331 merge sha `64d4a97` was never installed by itself; `faa3192` = #331 + #332 (log-only drift). The #330 rule "a first reading that already shows a crash fires the stop-loss" is intentionally absent: the mark is final when the position is created, so a crash after landing is measured against the true post-buy price and the normal sl fires at -30% on the first poll.
- `a0bea86` (#330, mark from the first exit snapshot after landing) was **never installed**: that method folds up to about 5 s of post-landing drift into the mark (a 25% drop in the gap resets the mark, and sl then fires only near -47.5% against our fill). It is kept in `tools/probe_sim_calibration.py` as the labelled `live_snapshot_*` variant for comparison only.
- The §7 build groups separate the **send-state-mark builds** (`8a6849b`, `a25eb17`, `7004b16`) from this build. Do not pool them: their tp/sl fired against a mark that could differ from the real post-buy state by hundreds to thousands of bps (e.g. mint 7fX2pvgh, +4,197.82 bps more tokens than quoted). A calibration replay (`tools/probe_sim_calibration.py`, schema_version 3) must run before the re-pin.

## Entry drift logging (2026-10-05)

The study (exploration only, job #181, `tools/exp012_entry_veto.py`, PR #328) proposed rule `drift_gt_25`: skip the entry if the pool's spot at the last print with slot <= mig+3 is more than 25% above the price at the migration slot. It looked small and was best-of-N, an UPPER BOUND, with the rule family written after the live pattern was seen.

Quant-proof verdict: **FAIL as an active skip.**

- 40 of its 46 vetoes (of 881 entries) were simulated misses (the sim's 15% SLIPPAGE_CAP against the migration price in `tools/latency_curve.py` `_try_buy`), not avoided losses.
- Filled-only, the paired pressure gain is +3.9786e-05 SOL per mint, CI90 [5.74e-06, 8.623e-05], and rests on 6 trades with 3/9 days positive.

Decision: **no skip.** The skip code path was removed entirely, not kept behind a key (the executor holds a wallet key; the smaller diff wins). `entry_veto_drift_max` is not a config key and is ignored if present. Instead drift is LOGGED on every live and dry entry for a re-run on live entries: `drift_vs_seed` = V-priced spot of the send snapshot / P_mig - 1, with `snap_slot` and `pool_slot`, on the buy row (and on the existing skip rows that follow the snapshot). It is null when V is missing or more than 1% off the seed V.

P_mig is a constant seed price (about 4.1078e-07 SOL per token: vault 67.4058 SOL + V 17.5845 SOL over 206.9M tokens). The study used the first migration-slot print instead; on the fast-pool tape (2026-09-19T04..11Z, 215 migrated mints with a known V) the two agree within 1% for 77.7% of mints, so they differ for about 22%.

Starts in the next build. It changes no decision, so trades are not a separate section 7 group on account of it.


## Amendment 2 (2026-10-16; merged as db4af22, see status): fee-arm execution probe, 500,000 vs 155,000 priority, hard 0.15 SOL cap

**Status update 2026-10-16: QP OK at 8f4f4a91833d2615907197d2cf496ff88d195fb7 (PR #662 comment 6220282777); Reviewer OK (comment 6220337804); merged as db4af222ebfb639b341dd6d883dbbff84cd90db7. Owner go: Owner (chat, 2026-10-16): "I gave helm the go to go live after the dry run." Helm installs at db4af22 with Gordon's corrected steps below.**

*Original status paragraph, kept as written before the two OKs above (superseded by the status update):* Nothing runs before BOTH (a) `quant-proof` OK on the exact head sha (pending; re-pinned after each commit) and (b) the owner's OK, which is recorded: the owner relayed Helm's yes on 2026-10-16 at about 15:35Z ("worth a yes ... make sure the 0.15 SOL is a hard limit in the code, not just a goal"), recorded by manager11 (MiScusi question q_Ih-oH8wmvw9D2w, closed; notebook n_D_JV-tCOR9R-Nw). Scope: 0.15 SOL net wallet-loss cap, window ending 2026-10-22T00:00Z, no live-gate or book effect. The lab note must not report P&L of the probe's intents. Helm installs only after the quant-proof OK on the final head is recorded in the PR. Code: `tools/probe_executor.py` and `tools/probe_live.py`, profile `feearm` (config `limits_profile: "feearm"`); tests `tools/test_probe_feearm.py`. Amendments 1 and 1a and every earlier section stand.

### Purpose
An execution measurement, not a strategy. It asks one question: does a 155,000-lamport priority land as fast as the 500,000 the live sends have always paid? It makes **no P&L claim and no edge claim**, its trades never count toward any gate, and it is never a book. Basis: `/data/mal/hunt-1008/push-1016/hunt2/cost-floor/REPORT.md`. 124 of 124 live sends paid 505,000 and 122 landed; no landing rate exists at any other fee; Amendment 1's 150,000 was never installed.

### Arms and randomisation
| Arm | Priority per side | Base fee | Fee per send | Round trip |
| --- | --- | --- | --- | --- |
| control | 500,000 | 5,000 | 505,000 | 1,010,000 |
| test | 155,000 | 5,000 | 160,000 | 320,000 |
| low (only with `third_arm: true`, default **false**) | 15,000 | 5,000 | 20,000 | 40,000 |

- Per mint, by **blocked randomisation** on the buy-attempt index: attempts are cut into blocks (2 arms, or 3 with the flag) and each block is an independent shuffle seeded by `random.Random("feearm:<seed>:<block>")`. The split is exact after every full block, deterministic in (seed, index), and not a function of the mint, time or pool.
- The seed is explicit in the config (`arm_seed`), stored in the state file, and the executor refuses to start if it changes. The arm is written to the state file (write-ahead) and to an `arm_assigned` ledger row **before the first send**. The same arm is used for that mint's sell(s).
- A refused buy (cap, STOP, stale signal) does not advance the index, so a cap refusal cannot select which arm gets traded.
- The arm table is code (`FEEARM_ARMS`). Config keys `arms`, `fee_arms`, and any `priority_lamports` other than 500,000 are refused.

### Endpoints
Every ledger row of the profile carries `fee_arm`, `arm_priority_lamports`, `arm_index`, `arm_seed` (null arm on skip rows, which are not attempts). Per send (buy and sell rows): `outcome` = landed, failed (on chain, with `fail_class`) or dropped (expired, never landed); `landed_slot` and `slots_between` (landed slot minus the pool-state read slot of the decision, the quantity in the cost-floor report); `ms_decision_to_send`, `ms_send_to_confirm`; `fee_lamports`, `base_fee_lamports`, `priority_fee_lamports` as actually paid.

### Pre-declared analysis (before any row is read): two non-inferiority tests
The question is whether 155,000 lands **as well and as fast** as 500,000, so it is a non-inferiority question. Both tests are fixed here, with their margins, before any send. Test arm = 155,000; control arm = 500,000; the low arm (if ever enabled) is descriptive only.
- **Test A, landing.** Unit = every send of the arm, buys and sells together (a rebroadcast of one signed tx counts once). Landed = on chain and successful; a send that failed on chain or expired unlanded is not landed and stays in the denominator. Statistic = landed share(test) minus landed share(control), one-sided 95% **lower bound** by the Newcombe-Wilson score method (Wilson interval per arm, z = 1.645). A holds if the lower bound is **> -5 percentage points**. About 116 sends per arm are needed (see Money for what the cap buys).
- **Test B, lag.** Unit = every buy that landed, `slots_between` (landed slot minus the pool-state read slot of the decision). Statistic = mean(test) minus mean(control), 1,000 bootstrap draws (arms resampled separately), seed 1, one-sided 95% **upper bound** (95th percentile of the draws). B holds if the upper bound is **< +0.5 slot**. Control reference: mean 2.23 slots, sd about 0.69 (n = 61); at 58 buys per arm the standard error is about 0.13 and the bound about +0.21 if the arms are equal (about 99% power).
- **PASS** = A holds and B holds. **FAIL** = the point estimate of A is at or below -5 pp, or the point estimate of B is at or above +0.5 slot. **NOT_DECIDABLE** = anything else (for example a point estimate inside the margin with a bound that is not). NOT_DECIDABLE is reported with the observed counts, both bounds and the point estimates; no decision, including a move to 155,000, rests on it.
- **Secondary only:** two-sided Fisher exact on the share of buys landed at slot +1 (`slots_between == 1`; control was 4 of 61 = 6.6%, too small a share to detect a drop at any affordable n, so it is not the primary); share failed or dropped per arm; the slot-lag distribution. No secondary changes the verdict.
- Reported by executor build sha, never pooled with any earlier probe, H5 or DEC-020 row, with the hour-of-day distribution of attempts. No extension past the cap, the end instant or the attempt cap.

**What changes the floor.** Only a PASS on both tests moves the simulator send-cost floor to 160,000 lamports (155,000 priority + 5,000 base); any other result (FAIL or NOT_DECIDABLE) keeps 505,000. Wording for a PASS in the lab note: "At 155,000 priority, X/N sends landed vs Y/M at 500,000 (lower bound Z pp, margin -5); buy lag A vs B slots (upper bound C, margin +0.5). From DEC-019 Amendment 2, the simulator send cost is 160,000 lamports, and the measured lag shift is added. This is a cost input, not a P&L result. No book changes status, and no recomputed history counts as out-of-sample." The lab note must not report P&L of the probe's intents (`exp012_migrate_tp50_sl30`).

### Limits (enforced in code; config may lower, never raise)
| Limit | Value | Where |
| --- | --- | --- |
| **Hard net wallet-loss cap** | **150,000,000 lamports (0.15 SOL)** | `FEEARM_SPEND_CAP_LAMPORTS`, clamp in `Limits` |
| Stake per buy | 5,000,000 lamports (0.005 SOL) | `FEEARM_MAX` |
| Buy attempts | 150 | `FEEARM_MAX` |
| Transactions sent (buys + sells; a rebroadcast of one signed tx counts once) | 300 (soft, see The cap) | `FEEARM_MAX`, checked at buy admission |
| Max open | 2 | `FEEARM_MAX` |
| Sell attempts reserved per position | 3 (config may lower) | `FEEARM_SELL_ATTEMPTS` |
| Slippage | `DEFAULT_SLIPPAGE_CAP` 0.15 | unchanged |
| Absolute end | 2026-10-22T00:00:00Z (`FEEARM_END_MS` 1792627200000); max_days 5 | code constant, config may lower |
| Stop | wallet-wide `/var/lib/mal-live/STOP` (no new buys) and `HALT` (freeze); the profile refuses other paths | `check_profile_files` |

**The cap (a net wallet-loss cap, not a total-spend cap).** Before every new buy, `committed cost + worst case of this buy` must be at most 150,000,000, else the buy is skipped with `limit:spend_cap` and nothing is signed. The first send of a buy re-checks and refuses (alert `spend_cap_send_blocked`) if the cap would be crossed.
- committed cost = realized loss (net realized P&L if negative, floor 0; it already holds the base and priority fees, pool fees and price moves of every closed trip and landed failed send, so no fee is counted twice) + for each open position its `buy_cost_lamports` (stake, buy fee and ATA rent, all treated as lost and the rent as stranded) + the sell fees of its remaining reserved sell attempts + for each in-flight buy its full worst case.
- worst case of one buy = stake + buy fee + 2,500,000 rent reserve + 3 x sell fee. Control: 5,000,000 + 505,000 + 2,500,000 + 1,515,000 = **9,520,000**. Test: 5,000,000 + 160,000 + 2,500,000 + 480,000 = **8,140,000**.
- Sells are never refused (that would strand a position); their fees are inside the reservation. The cost is the net realized loss, floored at 0 overall: a later win offsets an earlier loss and frees headroom (wallet accounting, not "profits are ignored").
- **The 300-send limit is soft.** It reserves one sell per open position at buy admission, but up to 3 sell attempts are allowed per position and sells are never refused, so total sends can pass 300 when sells fail. The binding limit is the net wallet-loss cap, which does reserve all 3 sell fees.
- When the buy meta is only estimated (no usable meta for 60 s), `_fallback_buy_meta` books at least the 2,500,000 rent reserve in the position's `buy_cost_lamports`, so the cap cannot be under-counted by the missing ATA rent (tested with two such positions).
- Separate guard, not counted: the executor refuses buys below size + 0.02 SOL of balance.

### Money
- **Stake 0.005 SOL** (QP's optional smaller stake, chosen so the per-arm count rises; the reviewer's 0.002 SOL is not used, so the buy-meta token delta and slippage checks stay meaningful). Landing does not depend on size; fees (0.32 to 1.01 M per trip) dominate. The selector was drafted as EXP-012 ceiling-ledger migrate intents with `max_entry_k_slots` 8 and `max_signal_age_s` 5; the entry-k guard was then set off for the feearm profile (see "Selector change (2026-10-16, before any buy)" below). Only the fee arms differ between arms.
- **Expected cost per trip.** Price and pool loss is the probe's measured mean at 0.05 SOL (6.9% of stake mean loss less the 2.02% fee share = 4.89%, probe-final), scaled to 0.005 SOL = 244,500. Control 1,010,000 + 244,500 = 1,254,500; test 320,000 + 244,500 = 564,500. One control + one test pair = **1,819,000**.
- Pairs the cap pays for: 150,000,000 / 1,819,000 = 82.5; with the last buy's 9.52M reservation held back, **about 75 to 77 per arm**. Sends are about 2 per trip, so about 150 per arm, above the 116 per arm Test A needs; the 300-send limit binds at about the same point (it is soft, see Limits). The price/pool term comes from 61 trips at a different stake and could be worse; the cap, not the estimate, is the limit. **Worst case is 0.15 SOL by construction.**
- Rent is refundable (net 0 on 61 of 61 trips) and is reserved only so the cap holds if a sell never lands.

### Wallet and overlap with the stopped canary
- The wallet is shared with the stopped H5 canary. Balance about 0.28 SOL (manager figure; Helm reads the public balance at install). The probe spends at most 0.15 SOL, so **at least about 0.13 SOL remains**. The floor includes open exposure: the cap already holds two open positions at worst case (2 x 9.52M = 19.04M at most), so wallet >= start balance - 0.15 SOL at every instant.
- H5 has been stopped since 2026-10-14T07:14:07Z and may not resume before 2026-10-23; the probe's absolute end is 2026-10-22T00:00:00Z. Code guards: the feearm start refuses unless the dec019, dec020 and H5 (`/var/lib/mal-live/h5/live/state-live.json`; corrected in the follow-up PR, see Known defect at install) live states show 0 open and 0 pending; H5's own precheck loops over `PROFILES` and so refuses while a feearm position is open; the dec019 and dec020 profiles also refuse while one is open. H5's unit has `Conflicts=` with `mal-probe-executor`.
- Corrected 2026-10-16 (Helm/Gordon). H5's own STOP file (`/var/lib/mal-live/h5/STOP`) blocks H5 buys, but the H5 unit is running and enabled at boot. So Helm stops AND disables the H5 unit before the probe goes live, and leaves H5's `STOP`, `LIVE_OK` and `TIER` untouched. There is NO wallet-wide `/var/lib/mal-live/STOP` at present, so there is nothing to remove. The probe honours that path, so during the test the kill switch is CREATING `/var/lib/mal-live/STOP`, done by Helm. The manager's read-only watch only reports and recommends; the manager takes no STOP, HALT or unit action. At the end Helm restores H5's unit state as it was (enabled, still STOPped) only if the owner or manager says so.
- **Selector change (2026-10-16, before any buy).** The manager checked the live feearm probe (db4af222, live since about 18:20Z) at 18:40Z with read-only jobs #1072 and #1073. None of the 1,388 rows in `/var/lib/mal/paper/fast-forward-paper/intents.jsonl` has a `migration_slot`; they carry `complete_slot`, `complete_ts`, `decision_t_ms`, `written_ms` and `lead_ms`. With `max_entry_k_slots` 8, `entry_k_refusal()` refuses every signal (`refused: entry_k unknown migration_slot=None`); in the 1 h dry run 3 of 3 candidates were refused this way, with 0 arm draws. The entry-k guard cannot run on this runner's intents, so it is set off (`max_entry_k_slots: null`) for the feearm profile only. Staleness stays bounded by the 5 s signal-age guard (`max_signal_age_s` 5, about 12 slots). The change touches no arm, cap, stop or analysis rule. 0 buys had been made when it was found (`--status` at 18:20:13Z: attempts 0/150). It takes effect only when Helm installs the new sha, which the manager names and the owner OKs; until then the running db4af222 install makes no buys and spends nothing.
- **Known defect at install:** the db4af22 build checks the wrong H5 path (`/var/lib/mal-live/h5/state-live.json`), so its "H5 has nothing open" precheck always passes. Helm checks H5 open=0 by hand before start (`/var/lib/mal-live/h5/live/state-live.json`). The running probe is db4af222 (17-file manifest, no new manifest) and stays on it for the whole window ending 2026-10-22T00:00Z. The fix (path corrected; the precheck also refuses when the H5 directory exists but its live state file is missing) is in a follow-up PR for a later, separately reviewed version. Helm switches to it only if the manager names its sha and the owner (Vaan) OKs it. It is not a change to this run.

### Install (Helm, only after the two OKs)
1. `--status` on the dec019 config: `open=0 pending=0`; same for dec020 and H5. Check H5 open=0 by hand in `/var/lib/mal-live/h5/live/state-live.json` (the db4af22 precheck does not). Confirm `/var/lib/mal-live/h5/STOP` exists; stop AND disable `mal-h5-executor` (leave H5's `STOP`, `LIVE_OK`, `TIER` untouched).
2. Fresh root-owned clone at the reviewed head sha; a 17-line manifest (8 modules, 7 EXTRA, base unit, checker); `sudo scripts/mal-fast/install-probe-executor-pinned.sh <sha> <manifest>` (probe-executor.md section 2b).
3. Keyless check: `--status` on `/usr/local/lib/mal-probe-exec/current/probe-executor-live-feearm.json`. Expect `limits_profile=feearm size_sol=0.005`, `precheck=ok`, and a `feearm committed_cost_lamports=0 spend_cap_lamports=150000000` line (the state file does not exist yet, so the feearm line appears after the first start).
4. Pinned keyless dry run (probe-executor.md 2b) of at least 1 hour with 0 build errors. The dry run assigns arms and writes `feearm` rows to a scratch dir; it does not enforce the cap.
5. Compare the dry run's arm sequence with `feearm_pick(20261016, i)` (seed in the config) and read the public balance.
6. Install `mal-probe-executor-live-pinned-feearm.conf` from the verified sha dir as `live.conf`, `daemon-reload`, start. There is no wallet-wide `STOP` to remove; creating `/var/lib/mal-live/STOP` is the kill switch. Expect `mode=LIVE` and `profile='feearm'`.
7. H5's daily check and watchdog alert on a probe drop-in, a credential or an active probe unit (`probe_live_dropin`, `probe_has_key`): Helm expects and acknowledges these for the window. Nothing else of H5 changes.

### Stop rule
- Automatic (code): the net wallet-loss cap (`spend_cap` in code); 150 buy attempts; 300 sends; realized loss 0.15 SOL; the end instant; STOP and HALT. Each halts new buys; exits continue.
- Manual: the manager's read-only watch (`tools/feearm_watch.py`) only reports and recommends, with a message to the owner; **Helm creates `/var/lib/mal-live/STOP`** (the manager takes no STOP, HALT or unit action) if more than 3 of the first 20 test-arm buys expire unlanded, on any `unsafe_tx_refused`, `sell_stuck` or `sell_abandoned` alert, or if the wallet balance differs from start balance minus realized minus open cost by more than 0.005 SOL.
- At the end: STOP in place, `--status` flat, unit stopped and its drop-in moved; H5's unit state restored (enabled, still STOPped) only if the owner or manager says so. A lab note with per-arm counts is reviewed by `quant-proof` before any sentence compares arms.

### What this does not settle
One fee against one fee on a small sample. It says nothing about edge, pool-fee loss, or whether 155,000 is the cheapest safe fee. The venue fee (1.25% per side) is untouched.
