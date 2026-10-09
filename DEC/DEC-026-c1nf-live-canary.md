# DEC-026: C1-NF live measurement canary (0.10 SOL, second wallet)

| Field | Value |
| --- | --- |
| **Status** | **DRAFT 2026-10-09, updated about 17:10Z with the owner's answers (the `OWNER_CANARY_CONFIRMED` line in section 1).** The choices this draft offered are answered (section 3); two defaults he was told apply unless he objects. The stake, fee, limits, funding and end date are his; the manager's terms are marked. It takes effect on merge, with quant-proof OK on its final head. **No live send** until every item in section 11 is done. Appendix A is draft text for a separate EXP-025 amendment, not an edit to EXP-025. |
| **Decider** | Vaan (owner) for the stake, fee, limits, funding and end date (answered). The Claude manager runs the rest. |
| **Date** | 2026-10-09 |
| **Builds on** | [DEC-025](DEC-025-c1nf-family.md) section 4 (the canary runs only under this DEC), [EXP-025](../EXP/EXP-025-c1nf-part1-prereg.md) sections 5.1, 5.3 and 10, [DEC-024](DEC-024-h5-live-canary.md) (the template, with Amendments 1 to 3), [DEC-019](DEC-019-execution-probe.md) (custody, executor), DEC-020, [DEC-021](DEC-021-champion-challenger.md) section 7, [EXP-022](../EXP/EXP-022-cap-pick-part1-prereg.md) section 9, [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md) Amendments 2 to 4 (the form of the declared observation) |
| **Amends** | The paper-only fence and the "no trading keys on hosts" rule (CLAUDE.md, CONSTITUTION), **only** for one executor profile (`c1nf`) on **one second wallet**, on `mal-fast-0`, for this canary. DEC-024 extended DEC-019's exception to the H5 canary and its wallet. This DEC adds a second wallet for a second profile and nothing else. This draft does not edit CLAUDE.md. |
| **Does not amend** | **The promotion gate.** DEC-014, DEC-016, DEC-021 sections 6 and 9, DEC-024 (its wallet, files, limits and override are separate), EXP-022, and EXP-024. **EXP-025's rule, windows, looks, alpha pair (0.005, 0.020), gate items and read tool.** Canary trades are never a book and never count toward any gate, read or promotion. |

**How this draft was written.** From repo docs, the PR bodies of #502, #503, #504 and #506, the `/data/mal/hunt-1008/c1nf-verify/VERIFY.md` summary lines that EXP-025 already copies, and `tools/h5_executor.py` constants. No host was contacted. The owner's 17:00Z answers were relayed by the coordinator, and the drafter did not open the notebook entry. At 17:10Z the branch was merged with origin/main at d6f217d, and `tools/h5_executor.py` was read for the stop test and the balance guard. No sealed block, forward-1002, forward-1002ev, walk-2, forward-paper or runner row, and no C1-NF or H5 outcome, was opened. The MiScusi notebook entries it cites (n_hZaavyDyNcJcZg and others) were **not opened** by the drafter; they are cited as the repo documents relay them. **Labels:** [measured] is copied from a cited file. [pinned] is a value this DEC fixes. [inferred] is arithmetic or reasoning from cited numbers. [exploration] marks a number from spent September blocks; it is not evidence of an edge. Nothing in this file says or implies that C1-NF is positive.

## 1. The owner's instruction

The only owner words in the repo for this canary are in [DEC-025](DEC-025-c1nf-family.md) (`OWNER_DECISION_CONFIRMED`, O3), as the manager recorded them on 2026-10-09:

> "I'm also down to do some testing with some small trades in parallel."

DEC-025 adds, in the manager's voice and not the owner's: "scope (0.02 SOL stakes, a separate wallet, DEC-026) is the manager's, not the owner's words" (the owner's 17:00Z answer below sets the stake at 0.10 SOL). The manager's brief for this draft relays the O3 entry as "a small live C1-NF canary in parallel" (notebook n_hZaavyDyNcJcZg) and says it rides "the same tier ladder as H5".

The owner answered the DEC-026 choices on 2026-10-09 at about 17:00Z (MiScusi notebook entry "OWNER ... C1-NF canary (DEC-026)", as the coordinator relayed it; the notebook id was not given to the drafter and the entry was not opened):

```
OWNER_CANARY_CONFIRMED: 2026-10-09 ~17:00Z (owner, answering the manager's DEC-026 questions). Chosen option, verbatim as relayed: "Fees drop to ~1% of each trade, so the canary can actually show whether the edge exists. H5's 20-trade trial already tests the shared order code (C1-NF's bot is built on H5's), so skipping a 0.02 step is low risk. Needs a ~0.5 SOL wallet. Limits: 2 open, daily stop 0.20 SOL, total stop 0.30 SOL."
```

- **Also answered, as relayed:** funding 0.5 SOL (O-1); keep going after a Look 1 FAIL until Look 2 (O-5); `end_ms` 2026-10-24T00:30Z (O-4). The owner's total capital across all tests is **1.29 SOL**: this wallet gets 0.5 SOL (confirmed in a split question) and H5 gets +0.5 at its T1 step. **No further owner capital is planned; scale-ups come from profits.**
- **Defaults the owner was told, applied unless he objects:** each step above the 0.10 SOL canary size needs a dated owner line (O-2); the 10-09 pre-window observation is declared (O-6, Appendix A item 1).
- **Not in the owner's words, so the manager's terms:** the 35% wallet cap on the total stop, 30 attempts a day, the balance guard, and every halt rule. They mirror DEC-024.
- **The option's premise is not relied on.** "H5's 20-trade trial already tests the shared order code" is the option's text. C1-NF's executor is a separate profile with its own review and security review (section 11 item 9). This DEC adds no precondition on the result of H5's trial unless the manager does.

## 2. What it is and is not

- **It is:** a live measurement of how the pinned C1-NF rule's picks land and exit on chain: landing time from the decision slot, failure and guard-revert rates, exit precision, and whether the picks that fill differ from the picks that do not (EXP-025 section 3, "Adverse fills": correlated fill failure is the main untested live risk).
- **It is not:** gate evidence, a book, a paper pass, or a claim that C1-NF is positive. No report, note, Console entry or message may say a canary result shows a profit or an edge.
- **It can show a fault or a large gap. It cannot prove an edge.** At 0.10 SOL the fixed cost is about 1% of the stake, close to the 0.25 SOL cell's 0.404% (section 6, "What the canary can and cannot show"). The noise is now the per-trade spread, not the fee. The stops also may end the run early (section 6).
- **It does not change the gate.** DEC-025 section 4 and EXP-025 section 5.1: "Scale-up beyond canary size needs a passed read or a further explicit owner override." This DEC records no such override (section 10).
- **It does not replace the read.** EXP-025 Look 1 (`[2026-10-10T00, 2026-10-17T00)`, alpha 0.005) and Look 2 (cumulative to 2026-10-24T00, alpha 0.020, only if Look 1 did not pass) stand and are read as written (section 8).
- **The picks are the live rule, not the read's rule.** The canary trades the shadow's picks, built from the tip tape and a live feature engine. The read uses the pinned October adapter on forward-1002ev and walk 2. Their decisions will differ (EXP-025 section 5.1: "The shadow is not the read"). The live engine also differs from the batch (#506: exact on the replay state, drift in the live state). A canary result is therefore about the live build, not about the read's book.

## 3. Owner decisions (answered 2026-10-09, about 17:00Z)

The choices this draft offered are answered. The `OWNER_CANARY_CONFIRMED` line in section 1 holds the owner's words. Two defaults he was told apply unless he objects.

| # | Decision | Answer |
| --- | --- | --- |
| O-1 | Funding of the second wallet | **0.5 SOL**, confirmed in a split question. The owner's total capital across all tests is 1.29 SOL: this wallet 0.5, and H5 gets +0.5 at its T1 step. **No further owner capital is planned; scale-ups come from profits** |
| O-2 | Steps above the 0.10 SOL canary size | **Default applied unless he objects:** each step needs a dated owner line (section 10) |
| O-3 | Stake and priority fee | **0.10 SOL per trade** with the rule's **505,000 lamports per send**, about 1% of stake per round trip. There is no 0.02 SOL tier |
| O-4 | Canary end | `end_ms` **2026-10-24T00:30Z**. Any extension is a dated owner line |
| O-5 | After a Look 1 FAIL or NOT_DECIDABLE | **Keep going until Look 2.** Stop new buys at the next 00:00Z after a Look 2 FAIL or NOT_DECIDABLE |
| O-6 | Observe outcomes of 10-09 (pre-window) decisions | **Default applied unless he objects:** declared in Appendix A item 1 |
| O-7 | Limits | The owner's: **2 open, daily stop 0.20 SOL, total stop 0.30 SOL**. The manager's terms, mirroring DEC-024: the 35% wallet cap on the total stop, 30 attempts a day, the balance guard (section 6) |

**One consequence the owner has not yet seen (section 6; flagged for him and for quant-proof, Q14).** The 35% cap makes the effective total stop **0.175 SOL**, not 0.30, at a 0.5 SOL wallet. The executor's exposure test then allows **one position at a time** until the canary is ahead by 0.025 SOL, and a **single loss of more than about 0.075 SOL stops new buys**. The floor is coherent (about 0.12 SOL at the hard worst case), but the run will probably be cut short by its first deep loss. This is not decided here.

The quant-proof questions are in section 14. None of them is the owner's to answer.

## 4. When it starts

- **After Appendix A is merged as an EXP-025 amendment**, or after the fallback in Appendix A applies. Appendix A item 1 must be merged before the shadow writes its first outcome for any decision made before 2026-10-10T00, and in any case before the first canary send.
- **After every item in section 11.** The build, the pinned model, the parity and md5 proofs, Helm's install and dry run, funding, and the watchdog test.
- **Never while a live-halt rule of section 7 is on.** That includes the A3 flags that apply to C1-NF (section 7, rule 6) and the open synthetic question (section 9.3).
- **No date bar.** DEC-024 barred sends before 2026-10-10T00Z and its Amendment 1 lifted the bar. Here none is needed: every pool decided before 2026-10-10T00 is outside every look's counted window, and Appendix A item 1 covers the observation. The first send cannot come before the build of section 11 is done, and on the PR states listed there that is not before 2026-10-10.
- **No 0.02 SOL step.** The first send is at 0.10 SOL (the owner's choice), so the dry run, the soak and the reviews of section 11 carry the weight that a small first tier would otherwise carry.
- **Two wallets, one host.** The H5 canary keeps its own wallet, files and unit. Nothing in this DEC reads, writes or restarts them.

## 5. Custody and files

Same custody design as DEC-024 and DEC-019. Paths are proposals for Helm to confirm; Helm owns the exact install.

| Item | Rule |
| --- | --- |
| **Wallet** | A **second wallet**, created by Helm, separate from the H5 wallet (`5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk`). Public address: `<HELM FILLS; public key only>`. The manager never sees the key (DEC-019 section 5). The withdraw address stays with Helm (DEC-019 section 6 item 6) |
| **Key** | `/etc/mal-c1nf-key/c1nf-wallet.json`, root:root 0400, in a root:root 0700 directory. It reaches the unit only through systemd `LoadCredential=c1nf-wallet:...`. No other unit has this credential; the H5 unit cannot read it and this unit cannot read H5's [proposed; Helm verifies with the same sandbox checks as the H5 runbook] |
| **User and sandbox** | `mal-live`, the same sandbox as the H5 unit: `ProtectHome=tmpfs`, `ProtectSystem=strict`, `TemporaryFileSystem=/var/lib/mal:ro`, `ReadWritePaths` limited to its own state dir, shadow feed bind-mounted read-only |
| **Pinned install** | A root-owned copy of the executor tree, `/usr/local/lib/mal-c1nf-exec/<sha>/`, with a manifest and a sha256 check made in the manager's own clone, as for H5 (`docs/runbooks/h5-executor.md`). A new sha is a new install and a new hash check |
| **State dir** | `/var/lib/mal-live/c1nf/` (mal-live, 0700): live state, counters, ledger (`pick_status`, fills, exits, wallet deltas), `STOP`, `HALT`. The executor tests the files exist and cannot create `LIVE_OK` |
| **`LIVE_OK`** | `/etc/mal-c1nf/LIVE_OK`, regular file, root:root, mode exactly 0644, parent `/etc/mal-c1nf` root:root 0755, no symlink. **The executor sends only while it exists.** Helm creates it. Removing it stops new buys like `STOP` |
| **`TIER`** | `/etc/mal-c1nf/TIER`, the same checks, content exactly `T1` (the canary, 0.10 SOL) or `T2` (inactive until the owner's line, section 10). Missing or invalid means T1, the lowest. Helm writes it only on the manager's written ask (section 10) |
| **`STOP`, `HALT`** | The executor's own `/var/lib/mal-live/c1nf/{STOP,HALT}` and the wallet-wide `/var/lib/mal-live/{STOP,HALT}` (#504 tested both). `STOP` is no new buys, open positions still exit on the timer. `HALT` freezes all. **The wallet-wide `STOP` is still in place for H5 until H5's Step 11** ([HANDOFF](../docs/HANDOFF.md) state 10-09 ~07:35Z). While it stays, this unit cannot send. Helm does not remove it for C1-NF's sake |
| **Auditd** | A watch on the new key file, as for the probe key |
| **Watchdog** | Its **own** timer and config, `mal-c1nf-watch` (Discord: stuck, structure halt, stop fired, restarts, wallet line, tier change), not H5's. The H5 watchdog stays H5's. The webhook goes to Helm over the private channel, never into a repo. Tested before `LIVE_OK` |
| **Credentials** | Helius env shared with the probe and H5 (`/etc/mal-probe-rpc/helius.env`, root:root 0600). **No Jito, no Sender, no LaserStream, no paid RPC: $0 extra** (owner plan 10-08). Public RPC is an exit-only fallback sender. Any Helius credits the canary or its feed use are reported in the daily note with the unit and the count |

## 6. Limits and tiers (code constants; config may only lower them)

| Limit | Value |
| --- | --- |
| Rule | Frozen C1-NF v1 as EXP-025 section 2 pins it: stage 1, the LightGBM model at prediction > 0.02, the cap `h_top1` <= 0.5 (NaN dropped) **before** the book, one position per mint (re-entry only if the decision time is at least the previous exit + 60 s), buy at decision slot + `round(1.3 s / s-per-slot)`, exit 300 s after landing |
| Venue | PumpSwap, the canonical PDA pool of a graduated mint in the rule's universe (V0 in [17.5e9, 17.7e9], first print within [-5 s, +120 s] of `complete`), WSOL quote |
| Model | One pinned model file, sha256 checked at start (section 11, "Pinned model"). A change of file or hash is a halt (section 7, rule 7) |
| Stake | **0.10 SOL** per entry (the owner's choice, section 1). No 0.02 SOL tier |
| Concurrent | At most **2** open positions (the owner's), **subject to the exposure test below** |
| Attempts | At most **30** buy attempts per UTC day, counting guard reverts (DEC-024 section 4's number; the exploration book had about 20 trades a day, 419 over 21 days [exploration, VERIFY]). Picks that arrive when 2 positions are open or the day's 30 are used are logged as skipped, with the reason |
| Daily stop | Realized loss of **0.20 SOL** in a UTC day (the owner's): no new buys until 00:00Z |
| Total stop | **0.30 SOL** is the owner's ceiling. It is **capped at 35% of the wallet balance measured when the tier started** (`TIER_WALLET_FRAC` = 0.35, `tools/h5_executor.py:157`, as DEC-024). **At a 0.5 SOL wallet the effective total stop is min(0.30, 0.35 x 0.5) = 0.175 SOL.** The owner's 0.30 binds only from a wallet of about 0.857 SOL (0.30 / 0.35), and the daily 0.20 is below the total only from about 0.571 SOL (0.20 / 0.35). No new buys after it until the owner restarts. The cap uses a tier-start realized baseline (the H5 G1 fix); the rebuilt executor must show the same |
| Exposure test | Inherited from H5 and **kept** (`tools/h5_executor.py:1139-1149`, with a test): before each buy the executor assumes every open or in-flight position and this stake are lost, and refuses if (tier realized loss + that exposure) reaches the total stop, or (day realized loss + that exposure) reaches the daily stop. #504 reused H5's limits and stops, so C1-NF's executor inherits it unless it overrides it |
| Priority | **505,000 lamports per send**, on the buy and the sell (the owner's answer; the cost EXP-025 section 6 pins for the deciding cell). About 1% of the stake per round trip. DEC-021 section 7 says a new priority setting needs a live calibration; this canary is it |
| Buy guard | On-chain `min_out` so the buy reverts if size / tokens out exceeds 1.15 x the **decision-time spot** (EXP-025 section 2.1). The pick must carry the decision-time `q_lamports` and `base_reserve`. **A pick without them gets no buy** (fail closed). #504 allowed a fallback measured at receipt; this DEC does not |
| Staleness | A pick more than 3 s of chain age old (slots since `SD_slot` times the measured s-per-slot, plus a 2x wall backstop) is refused. Config may lower the 3 s, never raise it (#504 design) |
| Sell | Full balance, then close the ATA. `min_out` at 0.85 of the quote; after 2 reverts, or past the plan's deadline, resend at 0.65 with higher priority. Never `min_out` 0. The plan is anchored on the landed slot (#504: sell at landing + 300 s + 0.55 s, escalate at +315 s, deadline at +370 s) |
| Out-of-rule entry | A buy that lands more than 5 s after `SD_slot` exits at once and latches `out_of_rule_entry` (halt, section 7 rule 3) |
| Balance guard | H5's `balance_need` (`tools/h5_executor.py:499-503`): stake + buy priority + base fee + 2,100,000 lamports of ATA rent + 1,000,000 per exit (open positions plus this one) + stakes in flight + a **wallet floor of 0.05 SOL** (`DEFAULT_WALLET_FLOOR_LAMPORTS`; config may raise it). For a first 0.10 SOL buy that is about **0.154 SOL**. This replaces DEC-024 section 4's older "stake + 0.02 SOL" (0.12 SOL here); the code constant is the stricter |
| Duration | `end_ms` **2026-10-24T00:30Z** (the owner's), a code constant in the reviewed live config. Then STOP. The end is the end of Look 2's counted window plus the last exits. Beyond it, Appendix A item 2 is the only cover, so an extension is the owner's dated line |
| Files | Section 5 |

**Tiers.** There is no 0.02 SOL tier. The `TIER` file names the C1-NF table below, not H5's (H5's T1 is 3 open, 40 a day, stops 0.40 and 0.60).

| Tier | Stake (SOL) | Max open | Attempts/day | Daily stop (SOL) | Total stop (SOL) |
| --- | --- | --- | --- | --- | --- |
| **T1 (the canary)** | 0.10 | 2 | 30 | 0.20 | 0.30 ceiling; **0.175 at a 0.5 SOL wallet** (35% cap) |
| T2 (inactive) | 0.30 | stated in the owner's line | stated in the owner's line | stated in the owner's line | stated in the owner's line; H5's T2 (`TIERS`, `tools/h5_executor.py:151-154`: 3 open, 40 a day, 1.20 and 1.80) is a starting proposal only |

- **T2 needs the owner's dated line** (section 10). It is not fundable from the 0.5 SOL wallet: a 0.30 stake exceeds the 0.175 total stop, so the exposure test refuses every 0.30 buy unless realized profit is at least 0.125 SOL. A 0.30 step is reachable only after profits lift the wallet to about 0.86 SOL [inferred].
- **T2's price-impact check is H5's, not C1-NF's.** `T2_IMPACT_OK` rests on a replay model for H5 triggers (`/data/mal/hunt-1008/h5-work/IMPACT.md`, per `tools/h5_executor.py:158-162`). C1-NF's pools are older and deeper (the stage-1 real quote is at least 20 SOL; the discovery picks' median real quote is 142.65 SOL, VERIFY section 6 as EXP-025 section 8 cites it [exploration]). The check is not transferable (Q8).

**Funding (O-1): 0.5 SOL** into the second Helm-held wallet. **No further owner capital is planned; scale-ups come from profits.** The owner's total capital across all tests is 1.29 SOL: this wallet 0.5, H5 +0.5 at its T1 step.

**Worst case and the wallet floor [inferred; arithmetic from the numbers above].**
1. **The owner's literal stops with no cap fail the floor.** 0.30 (total stop) + 2 x 0.10 (two open positions go to zero) + about 0.006 (four sends at 505,000 and two stranded ATA rents of 2,039,280) = **about 0.506 SOL**, more than the 0.5 SOL wallet. The wallet would be drained to nothing before the floor, and the exits could not pay their own fees. **The wallet floor is hit first.** This is why the 35% cap is applied.
2. **With the 35% cap (applied).** Total stop **0.175 SOL**. Hard worst case, not relying on the exposure test (the convention of DEC-024 Amendment 3): 0.175 + 2 x 0.10 + 0.0020 (four sends) + 0.0041 (two stranded rents) = **0.381 SOL lost, a floor of about 0.119 SOL**. (The brief's "about 0.385, about 0.11" allows roughly 0.004 more for exit retries; the executor reserves 1,000,000 lamports per exit, which gives 0.382 and 0.118. Read it as **about 0.38 lost, about 0.12 left**.)
3. **Against the guards.** The floor of 0.119 is above the wallet floor of 0.05 and covers the exits' fees. It is below DEC-024's old guard (stake + 0.02 = 0.12) and below the code's `balance_need` (about 0.154), so **no further buy can open at the floor**. The floor is coherent.
4. **With the exposure test (kept).** At every buy, realized loss + open exposure + this stake stays below the total stop, so the loss at any moment is about 0.175 plus stranded rent and exit fees, **about 0.18 SOL, a floor of about 0.32 SOL**. Item 2 is the conservative bound.
5. **The largest total stop whose hard worst case still clears the 0.05 floor** is 0.5 - 0.20 - 0.0061 - 0.05 = **0.244 SOL** (49% of the wallet). A cap looser than that is not coherent with 2 open positions at a 0.5 SOL wallet unless the exposure test is relied on.

**What the exposure test costs [inferred; Q14].** At stake 0.10 and total stop 0.175:
- A **first** buy needs tier realized above -0.075 (0.175 - 0.10) and day realized above -0.10 (0.20 - 0.10).
- A **second concurrent** buy needs tier realized above **+0.025** and day realized above 0. So **2 open is not reachable from the first send.** The canary runs one position at a time until it is ahead by 0.025 SOL.
- **A single loss of more than about 0.075 SOL (75% of the stake) ends new buys.** In exploration 22 of 419 trades (5.3%) lost at least 90% [VERIFY section 6]. If that rate held and trades were independent, the chance of at least one such loss in 50 trades is about 1 - 0.947^50 = **0.93**. Early wins worth more than the shortfall would carry one loss. So the run will probably be cut short by its first deep loss, before it has 50 trades.
- **Picks skipped for the open cap are the clustered ones** (hot minutes), a selection effect. They are logged as skipped, and the fill-selection rule (section 7 rule 1) does not count them.
- Options for the owner and quant-proof, **none chosen here**: (a) keep it as written, a very conservative canary; (b) a larger wallet, which is not planned; (c) a looser cap, which fails the floor without the exposure test (item 5); (d) drop the exposure test, which fails the floor at 0.30 (item 1). The default applied is (a): DEC-024's own 35% cap and test.

**What the canary can and cannot show [inferred arithmetic from EXP-025 section 6's numbers].**
- One send at 505,000 lamports is 0.505% of a 0.10 SOL stake. A round trip is **1.01%** (the owner's "about 1%"), against **0.404%** in the deciding cell at 0.25 SOL: **0.606 percentage points more** per trade. A stranded ATA rent of 2,039,280 lamports is 2.04% of the stake. (DEC-024 section 4 uses 1,513,840 lamports for H5's ATA. The two numbers differ; the canary uses whichever its executor actually pays. Q9.)
- The deciding cell's September means already include 0.404%. September's rent-inclusive means were flat +7.458% and pressure +6.640% [exploration, VERIFY section 5.2]. Minus 0.606 pp they are **+6.85% and +6.03%** at 0.10 SOL, **if** September's edge held and nothing else changed. That is arithmetic, not a forecast.
- The per-trade SD is about 0.34 to 0.37 of stake (EXP-025 section 3: 0.373 modelled, 0.342 implied by VERIFY's trade CI). The standard error of a mean is about **5.0 pp at 50 trades** and 3.6 pp at 100. An expected +6% against 5 pp is a z of about 1.2 to 1.4. **So it can show a large fault or a large gap, and a September-sized edge only loosely. It cannot tell a half-size edge (+3%) from zero at 50 trades.** That is better than at 0.02 SOL, as the owner's option says. It is not a test, and the exposure test above may end the run first.
- The fee burn alone over the first 50 filled trades: 50 x 1,010,000 lamports = **0.0505 SOL, 10% of the 0.5 SOL wallet** (the fee in lamports does not depend on the stake). At 30 fills a day it is 0.0303 SOL a day.
- The paper twin and the live-versus-twin comparison are priced **at 0.10 SOL and the canary's fee**, not scaled from the 0.25 SOL cell. The shadow's outcome variants (no fee, 55k, 505k, 505k + rent; #503) do not state a stake in the PR body. The builder confirms and, if needed, adds a variant at the canary's stake.

**What the stops do.** They are circuit breakers for a build or regime fault, not a P&L tool. A stop firing is not evidence about C1-NF.

## 7. Live-halt rules

Any of these halts new buys at once (`STOP`). Open positions exit on the timer unless the manager places `HALT`. A halt is never followed by a retune of the rule, the model, the cap or the threshold.

1. **Fill selection (from #504's design).** Every pick that reaches the executor is ledgered `pick_status` filled or unfilled. The monitor reads the shadow's `c1nf_outcome` for each, over the last 30 monitored picks with outcomes. If (mean outcome of the unfilled) - (mean outcome of the filled) exceeds **3 pp**, with at least **8 unfilled** and at least 1 filled, it latches `fill_selection_adverse`. After a clear, only outcomes that arrive later count. Book refusals (held, cooldown, duplicate, open cap) and refusals by a stop are not fill failures and are not monitored.
   - **Why it exists.** The edge sits in the right tail of hot entries. VERIFY section 6 [exploration]: if the best 10% of fills failed, the no-fail mean would fall to +1.855%; if the best 28.9%, to -5.116%.
   - **It is weaker than it reads (the builder's own flag, #504).** With a per-pick SD of about 34 pp and 8 unfilled against 22 filled, the standard error of the difference is about 14 pp, so a 3 pp line fires by chance in roughly 40% of evaluations once 8 are unfilled. Fewer than 8 unfilled of 30 cannot latch. **It is a fill-rate tripwire in practice.** Kept as designed (a false halt costs a restart; a missed one costs the measurement). Q3 asks quant-proof to size it.
   - It uses the shadow's outcomes, which are observed in real time under Appendix A.
2. **Divergence from the paper twin.** After at least **50 fills** (DEC-024 uses 100; C1-NF has about 20 picks a day, and the exposure test of section 6 may end the run first), the live-minus-twin per-trade CI90 upper bound is below **-1 pp** (live worse than its twin by more than 1 pp). The twin is the shadow's outcome for the same pick, **re-priced at the canary's stake, fee and landing slot** (section 6). A CAP-PICK pick is never traded and has no twin. Q4 asks quant-proof to size N and the line at C1-NF's variance.
3. **Landing latency.**
   - **Slow landing.** The rolling landing p50 (decision slot to landed slot, converted by the measured s-per-slot) above **1.9 s**, over the first 20 landed buys and then a rolling 20. 1.9 s is the pre-registered binding latency (EXP-025 section 7 item 9). The deciding latency is 1.3 s; the canary reports its p50 and p90 against it.
   - **Out-of-rule entry.** Any buy that lands more than 5 s after `SD_slot` (section 6).
   - The p50 line is a fidelity line, not an edge line. VERIFY's means at 3.0 s and 4.0 s are within about 0.2 pp of the 1.3 s mean without rent [exploration: flat +8.260 and +8.080 against +8.151]. Q5.
4. **A stuck position.** A position not closed by landing + 600 s: halt new buys and alert. Retries back off from 30 s to 10 min; after 10 attempts it is abandoned and Helm runs a root sell-and-close.
5. **Exit timing: report and alert, no halt.** H5 has a cliff at its exit; C1-NF does not. VERIFY's sell-lag legs at 2 s and 5 s sit within about 0.5 pp of the 0.55 s leg [exploration: flat +8.147 and +8.571 against +8.151]. The canary reports the exit-lag distribution and alerts when more than 10% of sells land later than landing + 305 s.
6. **Structure.** The A3 monitor's flags, and the other items, as follows. (Monitor code: `tools/pump_structure_monitor.py`, halt flags at lines 1365 to 1443, WATCH_RULES at line 147.)

   | Flag or item | Applies to C1-NF as | Why |
   | --- | --- | --- |
   | `pins_changed` | **Live halt** | A program redeploy can move the decoder, fee or V convention that the pricing and the guard rest on. The 2026-10-08T16:20Z redeploy is the example |
   | `program_changed` (a WARN in the monitor) | **Live halt** for the canary, as DEC-024 Amendment 2 item 4 | Same reason; the monitor does not halt on it, the canary does |
   | `synthetic_share_high` | **Live halt, pending the quant-proof ruling of section 9.3** | Unknown whether C1-NF's universe holds synthetic pools. Fail closed while the question is open. Whether it stays a halt after the ruling is the ruling's |
   | `boost_disabled`, `boost_share_low`, `boost_budget_or_slices_changed` | **Alert only; not a halt** [manager's term; Q6] | C1-NF is BOOST-independent per JUDGE-4 (EXP-025 section 9: "a BOOST switch-off kills H5 and CAP-PICK together, not necessarily C1-NF"). But BOOST shapes the first minutes of every pool, and the features read post-graduation flows and holder shares. A regime change is a feature-drift risk, so it is alerted, put in the daily note, and tied to the out-of-support report of EXP-025 section 8. If one persists two daily runs in a row, the manager reviews it before the next tier step |
   | `boost_last_slice_early` | **Alert only; not a halt** | The flag times the last BOOST slice against H5's 330 s exit. C1-NF's first decision is at graduation + 600 s and every C1-NF trade is after BOOST has ended [inferred from EXP-025 section 2.1 and the flag's text]. It has no bearing on C1-NF's exit |
   | `ms per slot` outside [150, 450] | **Live halt** | The landing slot and the sell plan are computed from it |
   | `docs_changed`, `usdc_boost_regime` (WATCH) | Report only | As in the monitor |
   | A V-range canonical pool with no PDA match | Counted and reported | EXP-025 refusal R2 is a read matter; the canary only trades pools it can match |

   Any A3 halt flag that applies, as the table says, also **bars the start** (section 4).
7. **Feed, model and ledger integrity: fail closed.**
   - The shadow's heartbeat older than 150 s: every pick is refused `feed_stale` (alert, no halt). A `c1nf_gap` record for the minute: no pick for it.
   - The model file or manifest hash differs from the one pinned in section 11: **halt**.
   - No as-of wallet snapshot for the decision day (the nightly rollup missed a day; #502's `--require-prev-day` makes that a failed job): no pick that day, and an alert.
   - Ledger coverage (share of the 5-minute buyers and sellers the ledger knows) is reported per day against the exploration picks (EXP-025 section 8). It alerts and never gates.
8. **Fill-rate alert (no halt).** More than 28.9% of attempts in a rolling 30 do not fill. 28.9% is the pressure leg's mean failure rate (EXP-025 section 6). The halting rule is rule 1.

**Stops that end the canary.**
- **The total stop** (effective 0.175 SOL at a 0.5 SOL wallet, section 6), or `end_ms` 2026-10-24T00:30Z.
- **A seal breach.**
- **EXP-025 Look 2 FAIL or NOT_DECIDABLE (O-5, answered).** New buys stop at the next 00:00Z after the Look 2 report, unless the owner extends for execution measurement only. After a Look 1 FAIL or NOT_DECIDABLE the canary **keeps going**, because Look 2 still runs and a Look 1 FAIL at alpha 0.005 is a thin read (EXP-025 section 3: Look 1 passes about 26% of the time if September holds). After a Look 1 PASS the read ends; the canary keeps measuring to `end_ms` and nothing else follows from it. A PASS leads to quant-proof, a paper twin, the owner's yes and the DEC-018 / 019 / 020 path (EXP-025 section 7), not to this canary scaling.
- **Not an end: BOOST off.** Unlike DEC-024 (`toggle_boost`), a BOOST switch-off does not end this canary by rule (rule 6 table).
- **Not an end: the CAP-PICK oracle.** From 2026-10-16T01 without a working oracle the executor refuses every buy in the seal window, which pauses the canary (section 9.1).

## 8. Relation to EXP-025

**What EXP-025 already says (merged in #501).** Section 5.1 declares, before any shadow or canary trade, that C1-NF's paper shadow and small live canary outcomes "for decisions inside the windows may be watched in real time", that "Look 1 and Look 2 are always run (Look 2 under its own condition) and reported as written", that neither is "skipped, delayed, re-scoped or re-thresholded because of anything the shadow or the canary shows", and that "Nothing the shadow or canary shows may change any EXP-025 parameter". It says the canary's trades "are real chain activity by one participant, appear in the tape as ordinary rows, and are not removed (that would edit chain truth). The read reports them." Section 4's seal says, for every other October hour, that no one computes a C1-NF label, fill, exit, P&L, mean, CI or day sign "except the declared observation in section 5".

**This DEC relies on that declaration and adds to it only through Appendix A.**
- **Look 1 and Look 2 are always read as written.** A canary halt (section 7), a stop (section 6) or a tier step (section 10) stops, changes or scales the canary only. It never stops, delays, re-scopes or re-thresholds either look, and never changes a parameter, window, alpha, gate item or refusal of EXP-025.
- **The canary's scale-up decision is a separate business decision, not EXP-025 evidence.**
- **Disclosure.** Concurrent observation lets canary results influence later choices, for example an owner tier step. That never changes the formal read, and each look's report says so (Appendix A item 5).
- **Who may read what in real time.** The owner, Helm, the manager, builders, the section 5 watchdog (its Discord alerts, including the daily and total stops and the wallet line) and the daily check may read the canary's ledger and the shadow's outcomes. **The one excluded reader is the EXP-025 read tool.** Its inputs stay the section 4 hours of EXP-025, and it never takes the canary's ledger, the shadow's output or the shadow's ledger (EXP-025 section 5.1).
- **The public wallet.** The wallet is public on chain, so anyone can compute its P&L. At 0.10 SOL against a stage-1 real quote of at least 20 SOL the canary is at most 0.5% of a pool's quote (EXP-025 section 5.1 gave 0.1% at 0.02 SOL). That is consistent with the declaration for decisions in the windows. A trade on a decision outside them is covered by Appendix A item 2.
- **The shadow is not the read.** Its decisions will differ from the read's (different V source, a tip-tape ledger, a live feature state, a possibly different model). The Look reports may record disagreement; nothing is reconciled.

**The gaps that need an EXP-025 amendment (Appendix A).**
1. Decisions before 2026-10-10T00 (October rows on 10-09, including graduations from 2026-10-09T00, which are training-label rows in EXP-025 section 4). Section 5.1 names "decisions inside the windows" only. A shadow that is live on 10-09 would compute outcomes on rows the section 4 seal still protects. **Time-critical:** merge before the shadow's first such outcome. As of this draft, #503's body says it has had no live run on fast-0, so none is known to exist (the manager verifies before merge).
2. Decisions at or after 2026-10-24T00, inside no window. The default `end_ms` makes this nearly moot; an extension would need it.
3. The **executor's** CAP-PICK skip. Section 5.3 binds the read and the shadow; the live executor is not named.
4. The observer list, the report disclosure text, and the rule that an EXP-025 amendment dated after the first canary or shadow outcome is not called outcome-blind.

**Why this cannot change a read.** The rule, data, analysis, pass bar and alpha pair are fixed by EXP-025 sections 0, 3 and 7. Appendix A changes none of them. It edits no pinned line, patch, hash or window.

**Why the amendment is dated before the Look 1 window's outcomes exist.** No outcome for any in-window decision can exist before 2026-10-10T00:00Z plus the 1.3 s landing plus the 300 s hold (the first possible outcome is about 2026-10-10T00:05Z), and none of the four gaps is time-critical for in-window decisions, because section 5.1 already covers them. Gap 1 is the only one that must beat a clock: the first outcome of a pre-window decision. **If Appendix A cannot merge before that outcome would be written, the shadow starts its outcome records at 2026-10-10T00 (a code guard) and gap 1 does not arise.** Gaps 2 to 4 then wait for the amendment without risk: no canary send is allowed before it merges (section 4).

## 9. Seals, pool overlap and the synthetic question

### 9.1 The CAP-PICK seal (EXP-022 section 9, EXP-025 section 5.3)

- From 2026-10-16T01 to the end of EXP-022's read, there is **no C1-NF live or paper trade on a CAP-PICK pick**, no C1-NF record joined to a pick, and no per-pool C1-NF P&L for a pick before each CAP-PICK look. A breach is recorded as compromised.
- **Mechanism.** The executor asks the boolean `pick_oracle(mint)` (PR #509, `claude/cap-pick-oracle`, open draft at 983b12c) for every mint in scope and skips any that answers True. It reads only the boolean, writes no CAP-PICK field into any C1-NF record, and joins none. **The oracle fails closed:** missing, stale for more than 60 s, erroring, non-boolean or undecided means no buy. Seal refusals are a count only, never recorded per mint.
- **Keying.** EXP-025 section 5.3 keys the exclusion on mints "whose canonical pool's first print is at or after 2026-10-16T01". #504 asked the oracle about **every** mint and refused any with no row in the picks file as undecided. That refuses more than the read excludes (an old mint decided after 10-16T01 is refused by the executor and counted by the read). It is fail-safe but not the same population. The rebuilt executor uses section 5.3's keying, or the difference is disclosed in each look's report. Builder item, section 11.
- **Dependency.** Until #509 is merged and wired in, the executor refuses every buy from 2026-10-16T01, and the shadow seals every pool.

### 9.2 Pool overlap with H5

- **The claim.** The scoping said C1-NF and H5 have 0 pool overlap. **I could not find its source.** Searched: EXP-025, DEC-025, DEC-023, DEC-024 (all amendments), `ARTIFACTS/exp025/`, `docs/HANDOFF.md`, JUDGE-4 and the C1-NF VERIFY (text search for "overlap", "same pool"). No file holds a measured overlap count between C1-NF and H5.
- **What the files do say.** EXP-025 section 1: "r4-b (laya-opt) and H5 (EXP-024) select overlapping hot post-graduation tokens." JUDGE-4.md:354: "r4-b and C1-NF select overlapping hot post-graduation tokens." Neither says C1-NF and H5 overlap or do not. (JUDGE-4.md:68, "same pools as CAP-PICK and H5", is about the two cells of another book; I did not establish which and do not use it.)
- **By construction [inferred from the two rules; not measured].**
  - The **universes overlap.** H5 trades PumpSwap non-mayhem WSOL pools in the V band in the first 300 s after `s0` (DEC-024 section 4). C1-NF's universe is every graduation with V0 in [17.5e9, 17.7e9] and a first print within [-5 s, +120 s] of `complete` (EXP-025 section 2.1). Same band, same first minutes. A pool-level overlap of 0 is not what the rules imply.
  - The **positions do not overlap in time.** H5 exits at `s0` + 330 s, which is at most graduation + 120 s + 330 s = 450 s for a C1-NF-universe pool. C1-NF's first decision is at graduation + 600 s. So no pool is held by both wallets at once, unless an H5 exit is stuck (DEC-024 section 5 rule 5 lets an H5 position stay open to `s0` + 600 s, which is graduation + 720 s).
  - **So "0 overlap" may mean no simultaneous positions.** If that is the claim, it is an arithmetic consequence of two timers, not a measurement, and it should be worded that way.
- **Consequences, [inferred] and small.**
  - A C1-NF trade comes after H5's window on the same pool, so it cannot change an H5 outcome on that pool.
  - The H5 wallet's sells land at about graduation + 325 to 450 s, inside the 300 s feature window `[graduation + 300, graduation + 600]` of C1-NF's first decisions on that pool (v5, new5, flows). At H5's 0.02 SOL (0.10 SOL at its T1 step) against a stage-1 v5 of at least 1 SOL that is at most 2% (10%) of v5. Both wallets' trades are ordinary tape rows and are not removed.
  - The two canaries are not independent evidence: the rules select overlapping hot tokens, so outcomes are probably positively correlated (EXP-025 section 1).
- **Asked of quant-proof (Q2):** a count-only pass on exploration tape, per day: pools in H5's universe, C1-NF picks, and their intersection, with no price, fill or P&L. Until it exists, no document says "0 overlap". It is a section 11 item.

### 9.3 Synthetic-migration pools: a quant-proof question, not decided here

- **H5's side (the facts).** DEC-024 Amendment 2 and EXP-024 Amendment 4: a synthetic migration is a graduation with a PostCompleteBuyEvent in the curve-completing transaction (`post_complete_buy_seen`, `tools/pump_structure_monitor.py:482-490`). The program-upgrade review found 0 of 61 graduations before the 2026-10-08T16:20Z redeploy and 12 of 61 after. `synthetic_share_high` read 6/19 = 0.316 at the daily run of 2026-10-09T07:11:06Z and 7/19 = 0.368 at one dry check at 07:38:11Z (halt line 0.35, `SYNTHETIC_MAX`). H5 now excludes synthetic and unclassifiable pools. H5's September evidence had 0 synthetic pools.
- **C1-NF's side (what is unknown).**
  - **No document mentions it.** EXP-025 and DEC-025 contain no occurrence of "synthetic" (text search). The rule's universe (EXP-025 section 2.1) has no restriction. **The read prices every universe pool, synthetic or not.**
  - **Is a synthetic pool in the universe?** The universe needs V0 in [17.5e9, 17.7e9] and a first PumpSwap print within [-5 s, +120 s] of `complete`. The review says a synthetic pool's first PumpSwap print may come later [unmeasured], so some may fall outside the window. How many stay inside is not known.
  - **What did C1-NF's September evidence contain?** The 0-of-61 count is a sample of recent graduations, not C1-NF's 419-trade September book. The classifier reads transaction logs of the completing transaction. Whether the exploration tape can classify September graduations at all is unknown (the tape rows have no transaction logs [inferred from the classifier's signature]). The September book may be unclassifiable, not clean.
  - **October.** After the redeploy about a fifth to over a third of graduations are synthetic on H5's samples. If they enter C1-NF's universe, they enter the read and the canary, and the model has never seen one.
- **Options, with no recommendation, for quant-proof to rule on.**
  - **(A)** The canary keeps the read's population, trades synthetic pools, and records the class as a structure field (count by class only). It is comparable with the read. Live money goes on a pool type no September row can vouch for.
  - **(B)** The canary skips synthetic and unclassifiable pools as H5 does. It is safer, but its population is no longer the read's. If the read should follow, EXP-025 needs its own population amendment (EXP-024 Amendment 4 is the model), which this DEC does not draft.
  - **(C)** Measure first: a count-only pass over October post-upgrade prints on how many universe pools are synthetic, before choosing A or B.
- **Status.** Open. A dated quant-proof ruling line goes under this heading before `LIVE_OK` (section 11, "Synthetic ruling"). Until then `synthetic_share_high` is a live halt for the canary (section 7, rule 6).

## 10. Scale-up (above the 0.10 SOL canary)

The canary starts at 0.10 SOL (section 1). There is no 0.02 SOL step. A step above 0.10 needs the manager's written ask and **all** of these:
- **The owner's dated line** (O-2, default applied): `OWNER_LADDER_CONFIRMED` below, with his words, stating the step's stake, open cap, stops and funding. Without it nothing above 0.10 is active. This follows DEC-025 section 4 and EXP-025 section 5.1: "Scale-up beyond canary size needs a passed read or a further explicit owner override." 0.30 SOL is three times the canary's size.
- **Enough closed trades at 0.10:** about 50 [manager's term; the owner's ladder is 25 to 50 trades a step, and the 0.02 step was skipped, so this step carries the order-code evidence]. Counting fills.
- **Live execution is consistent with the sim:** the landing p50 is at most 1.9 s, over at least 20 landed buys; the fill-selection rule has not latched; and, after at least 50 fills, the live-minus-twin CI90 upper bound is not below -1 pp (section 7 rules 1 to 3).
- **The paper twin is not negative:** the shadow's mean per trade for the canary's picks, priced at the canary's stake and fee, is not below zero.
- **EXP-025 Look 1, if it has been read, is not negative:** its deciding-cell flat and pressure means are not below zero. **Plainly: Look 1 cannot be read before about 2026-10-17T03Z (EXP-025 section 3), so this condition cannot bind a step made before then.**
- **The canary's own realized result, after fees, is not negative:** its mean per closed trade is not below zero. **Weak, by section 6's arithmetic.** If the rule worked exactly as September says, the expected mean at 0.10 SOL is about +6% against a standard error of about 5 pp at 50 trades; a half-size edge is about +3%. A zero-edge book still passes this check a large share of the time. The check guards against a gross fault. It is not a test.
- **No live-halt rule (section 7) and no stop that ends the canary is unresolved,** including the synthetic ruling (section 9.3) and the A3 flags that apply.
- **The step is funded from profits.** No further owner capital is planned. The 35% cap is set from the wallet at the step's start. At 0.5 SOL no 0.30 stake clears the exposure test (section 6): a 0.30 step is reachable only after profits lift the wallet to about 0.86 SOL [inferred]. **It also needs a C1-NF price-impact check** (Q8).

**These are necessary and not sufficient.** The twin is observed in real time (section 8), and the looks, if read, are read as written. **Neither clears the promotion gate.** CLAUDE.md says nothing goes live before a book clears the gate on both fail models. This DEC does not waive that for any size above the canary, and it records **no** override of the kind DEC-024 section 7 records for H5.

```
OWNER_LADDER_CONFIRMED: none. No step above 0.10 SOL is active. A dated line from the owner, with his words, goes here and nowhere else.
```

Size above step 2, or any step on a passed read, is governed by DEC-018, DEC-019, DEC-020, DEC-025 section 5 (at most 4 x 0.25 SOL open at a 1 SOL bankroll, after the owner's yes) and the owner.

**Mechanics of a step (as H5's runbook).** Helm edits `/etc/mal-c1nf/TIER` on the manager's written ask. No wind-down and no restart: nothing stops and open positions are untouched. The executor writes a `tier_change` ledger row within a few seconds of its next tick. The watchdog posts it. The daily check alerts if the file and the executor disagree for 15 minutes. A step down is the same edit.

## 11. What must happen before the first live send

Status is as of 2026-10-09, about 17:00Z, **from the PR list and PR bodies, not from the hosts**. Refreshed at 17:00Z: #502, #503, #506 and #509 are unchanged (last updated 01:52Z to 05:03Z), #504 is still closed, and main moved to d6f217d (DEC-024 Amendments 2 and 3, DEC-027, H5 synthetic-class work), which this branch has merged. Nothing was checked on `mal-fast-0` or `mal-core-0`.

| # | Item | Status |
| --- | --- | --- |
| 1 | **Appendix A merged** as an EXP-025 amendment (section 8) | Not started. Draft text below. Time-critical only for gap 1 |
| 2 | **The owner's answers** to the choices of section 3 | **Answered** 2026-10-09 about 17:00Z (O-1, O-3, O-4, O-5, O-7). O-2 and O-6 are defaults he was told, applied unless he objects. Relayed by the coordinator; the notebook entry was not opened by the drafter. **New flag for him:** the effective stops (section 6, Q14) |
| 3 | **Quant-proof OK on this DEC's final head**, and the stop table (section 12) | Not requested. Table not computed |
| 4 | **Synthetic ruling** (section 9.3), a dated quant-proof line | Open |
| 5 | **Pool-overlap count** (section 9.2, Q2), or every "0 overlap" removed from the documents | Open. Source of the claim not found |
| 6 | **Ledger** #502 (`claude/c1nf-ledger`) merged, with a review | OPEN draft at 53da60f. Its body reports 21 tests and byte-equal parity with the pinned ledger on 2026-09-20. No review recorded. Not run on fast-0; `duckdb==1.5.6` not checked there. The nightly rollup is not scheduled |
| 7 | **Features** #506 (`claude/c1nf-features`) merged, with a review | OPEN draft at 3ae9915. Exact mode bit-equal on two days; the **live mode drifts** (state features: median relative difference 2e-7 to 3e-5, p99 5e-4 to 1.7e-2, maximum 1.9, from the fee-model estimate; "the main open item"; no flag flipped on the two days tried). No review recorded |
| 8 | **Shadow** #503 (`claude/c1nf-shadow`) merged, with a review | OPEN draft at 2a96ce0, "not for merge until the missing items are closed": no pinned model, no parity, features not on main, no live run. 72 tests. **Two changes this DEC needs:** the pick record must carry the decision-time `q_lamports` and `base_reserve` (its `PICK_FIELDS` do not; section 6, buy guard), and outcome records start at 2026-10-10T00 unless Appendix A item 1 is in (section 8) |
| 9 | **Executor** (`c1nf` profile) merged, with a review and a **security review** (it holds a key) | **#504 is CLOSED, not merged**, at 2026-10-09T05:33:55Z, the same minute #484 merged; its base branch was `claude/h5-executor` (closed by that merge, inferred; no comment says so). The branch `claude/c1nf-executor` is still on origin at a98ff07, built on the pre-merge H5 head 9c5618b. It must be rebased onto main and reworked for the merged tier mechanism. Its limits (3 open, 40 a day, total 0.15) differ from section 6. Other gaps against this DEC: the buy guard fail-closed, the oracle keying (section 9.1), `end_ms`, priority per O-3, the file paths of section 5. No review of it is recorded |
| 10 | **Daily check and watchdog** for `c1nf` | Not written (H5's are `scripts/mal-fast/h5-daily-check.py` and the `h5-watch` units) |
| 11 | **Pinned installer and runbook** `docs/runbooks/c1nf-executor.md`, modelled on `docs/runbooks/h5-executor.md` | Not written |
| 12 | **Pinned model file** and its sha256 in a dated line | Not done (HANDOFF; #503 body). It is trained on the 36 exploration days only: no October label may be used while the EXP-025 seal runs (sections 4 and 11.3). EXP-025 P2's deterministic rebuild (due before 2026-10-16T01Z) is the natural source; its status is not known to the drafter. If the canary needs a model earlier, it is a separate documented model and its difference from the read's is disclosed (Q7) |
| 13 | **Parity task 3:** the shadow's picks against VERIFY's 419 | Not done (HANDOFF; #503 body). The bar is set by quant-proof **before** the run. Reference: VERIFY reproduced C1's frozen primary at 99.25% of its trades, Jaccard 98.53%, on a non-deterministic ledger (EXP-025 section 9). #506 shows the live mode is not bit-equal, so the bar must say what a live-mode miss is allowed to be (Q7) |
| 14 | **md5 decision-equivalence replay proof** (CLAUDE.md: every runner change carries one before deploy) | Not done. Three lists, md5 on each pair, on exploration day **2026-09-20** (EXP-025's E0 day; 34 C1-NF trades in VERIFY's September book): (a) the shadow's picks against the frozen scorer's, in the mode where they must be equal; (b) a shadow restarted mid-day (the 26 h bootstrap) against an uninterrupted run; (c) the executor's accepted and skipped intents, with reasons, against the pick list. Quant-proof reviews the replay, as for H5 (DEC-024 Amendment 1 item 3) |
| 15 | **Keyless shadow soak** of at least 24 h on live tip-tape triggers, 0 build errors, on the reviewed head, as a MiScusi job on fast-0 | Not started. A shorter soak is a dated owner decision, as DEC-024 Amendment 1 was; it is not assumed |
| 16 | **Keyless executor dry run** on the real feed: at least 5 complete simulated round trips with 0 simulate errors | Not started |
| 17 | **Reviews:** `reviewer` on each PR; the security review of item 9; quant-proof on item 14 | None recorded |
| 18 | **Memory and disk on fast-0:** `systemctl show user-1002.slice -p MemoryCurrent` before each start; the shadow's bootstrap, the rollup (reserve about 8 GB, #502) and H5's shadow and dry runs fit together; one heavy job at a time | Not measured. The 2026-09-29 OOM and reboot is the reason |
| 19 | **A3 monitor:** no flag that section 7 rule 6 makes a halt shows a halt at the time `LIVE_OK` is created | At 07:11Z `pins_changed` was on (the 10-08 redeploy). #517 (re-pin) is merged. DEC-024 Amendment 2 item 3 records the H5 install verified at 16:52Z. The official A3 run (job #449) is set for 19:23Z and was still ahead at 17:00Z. Not known now |
| 20 | **CAP-PICK pick oracle** #509 reviewed and wired into the shadow and the executor | OPEN draft at 983b12c. Needed from 2026-10-16T01 only. Before it, the executor trades; from it, with no oracle, it refuses every buy |
| 21 | **Helm:** creates the second wallet and gives the public address; pinned root-owned install with the manifest and sha256 check; auditd watch; the keyless dry run; the watchdog test message seen by the manager | Not done. The wallet does not exist |
| 22 | **Funding:** 0.5 SOL (O-1), finalized, with the transaction recorded | Decided by the owner. Not sent as far as the drafter knows. The wallet does not exist yet |
| 23 | **Limits as code constants with tests:** stops fire, config only lowers them, a restart cannot reset them, `TIER` missing or invalid means T1, and **the exposure test is kept at stake 0.10, total stop 0.175 and 2 open** (section 6) | Not done (the executor is not rebuilt) |

`LIVE_OK` and the go to Helm are separate acts, made by Helm and the manager after items 1 to 23.

## 12. Stop-probability table: to be computed by quant-proof

Not computed here. The H5 table (DEC-024 Amendment 1 item 4) is the form. **Inputs quant-proof needs, with the sources in the repo:**
- **Limits:** stake 0.10 SOL; daily stop 0.20; total stop 0.30 clipped to 35% of the funded wallet (**0.175 at 0.5 SOL**); at most 30 attempts a day; at most 2 open; the run from the first send to `end_ms` 2026-10-24T00:30Z.
- **Stop rule:** the executor's exposure test (section 6), not realized-only. With it a first buy needs tier realized above -0.075, and a second concurrent buy needs tier realized above +0.025 and day realized above 0. Please compute it with this test and with a realized-only variant, and say how often the first deep loss (at least 75% of the stake) ends the run before 50 trades.
- **Pick rate:** about 20 a day in September (419 trades over 21 days [exploration]); EXP-025 section 3 also uses 12 a day and 20/12 with the oracle exclusion. October is unmeasured. The share of picks the executor lets through (staleness, guard inputs, the open cap, clusters) is unknown.
- **Per-trade distribution:** EXP-025 section 3's four-part mixture calibrated to VERIFY (win rate 66.8%; 22 of 419 lose at least 90%; median +10.17%; p95 +63.17%; no-fail mean +9.625%; top-3 average about +165%; top-10 about +122%; p1 -99.29%, p5 -97.81%), or the 419 September trades drawn with replacement. Both are exploration.
- **Fail legs:** flat 15%, and pressure with intercept refit to a mean failure rate of 0.289. Plus the **adverse-fill stress**: the no-fail mean if the best 10% of fills fail (+1.855%) and the best 28.9% (-5.116%) [VERIFY section 6; not in the power table].
- **Fixed-cost term at 0.10 SOL:** two sends at 505,000 are 1.01%, of which 0.404% is already inside the 0.25 SOL means, so **-0.606 pp per fill**. One send (0.505%) for each guard-reverted attempt. Stranded ATA rent (2.04% of stake) for a stuck position. The alternative priority (55,000: round trip 0.11%) only if the owner reopens O-3.
- **Day effect:** SD 0.03 of stake as in EXP-025 section 3's day-effect row.
- **Not modelled in H5's table and needed here:** the 2-open cap with clustered picks; the pressure leg; the ladder's step checks (section 10); live fills worse than paper; stuck positions.
- **Outputs wanted:** P(total stop) and P(daily stop) over the whole run; total P&L p5 / p50 / p95; **P(first-50 P&L > 0)**; **P(each ladder check passes at 50)** per scenario, so the owner sees how little "not negative" separates; the distribution of trades before the stop fires; and P(the fill-selection rule latches) under **no** selection effect, against #504's roughly 40% claim.
- 10,000 simulations, seed 1, reproduced by a second run, as for H5.

| Assumed edge per trade (gross, 0.25 SOL cell) | Net of the 0.10 SOL fixed-cost term | P(total stop) | Total P&L p5 / p50 / p95 (SOL) | P(first-50 P&L > 0) | P(ladder check passes at 50) |
| --- | --- | --- | --- | --- | --- |
| September holds: flat +7.458%, pressure +6.640% | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof |
| Half of it | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof |
| A quarter | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof |
| Zero | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof |
| -3% | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof |
| Best 10% of fills fail | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof |
| Best 28.9% of fills fail | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof |

A stop firing is not evidence about C1-NF (section 6).

## 13. Helm's steps

A runbook, `docs/runbooks/c1nf-executor.md`, modelled on `docs/runbooks/h5-executor.md` (Who does what, What is where, Helm's steps with expected output, Wind-down, tier steps, Never), is a builder item (section 11, item 11). Until it is written and merged, these are the steps, in order. Helm owns the commands.

1. **Do not touch H5.** Its unit, wallet, key, `/etc/mal-h5`, `/var/lib/mal-live/h5`, watchdog and `TIER` are left alone. Do not remove the wallet-wide `/var/lib/mal-live/STOP` for C1-NF's sake.
2. **Create the second keypair** (never printed, never in a repo). Give the manager the **public** address only. Keep the withdraw address with Helm (DEC-019 section 6 item 6). Confirm the address differs from the H5 wallet.
3. **Install the key** at `/etc/mal-c1nf-key/c1nf-wallet.json` (root:root 0400, directory 0700). Add the auditd watch.
4. **Pinned install** of the executor tree for the sha the manager names in a PR comment, after item 9 of section 11 is merged: manifest made in the manager's own clone, sha256 check against the installed tree, `/usr/local/lib/mal-c1nf-exec/<sha>/`. Base unit keyless. The live drop-in (the only thing that hands over the key, via `LoadCredential=c1nf-wallet`) is written after the keyless dry run and before the go. `LIVE_OK` stays the gate.
5. **Create `/etc/mal-c1nf` and `/var/lib/mal-live/c1nf`** with the owners and modes of section 5. Bind the shadow feed read-only into the unit.
6. **Keyless dry run** on the real feed. Report: minutes run, restarts, any startup refusal, simulated round trips, simulate errors. Expect `live_ok=live_ok_missing` in `--status`.
7. **Watchdog:** install `mal-c1nf-watch`, send the test message, tell the manager to look in Discord.
8. **Wait for the owner's funding** (0.5 SOL, O-1) and confirm the finalized balance and signature.
9. **On the manager's written go** (after items 1 to 23 of section 11): write `/etc/mal-c1nf/TIER` = `T1`, create `LIVE_OK` (root:root 0644), check `--status` shows `live_ok=valid`, start the unit.
10. **After that:** remove `LIVE_OK` on any halt the manager or owner asks for. Edit `TIER` only on the manager's written ask (section 10). Run the root sell-and-close tool if a position is ever abandoned. Report any restart.
11. **Never:** `ufw`/firewall, `sshd`, cloudflared or Cloudflare changes (CLAUDE.md). No key or webhook goes into a repo file or a PR. The withdraw address stays with Helm.

## 14. Open for the owner, and questions for quant-proof

**Owner.** O-1 to O-7 are answered (section 3). Please read these two sentences once:
- **The canary can show a fault or a large gap, not prove an edge.** At 0.10 SOL and 505,000 lamports a send the fixed cost is about 1% of every stake and 10% of the wallet over the first 50 trades, and the result's noise is about 5 pp at 50 trades (section 6). That is a better test than 0.02 SOL gave, and it is still not a test.
- **The 35% cap and the exposure test will probably end the run early.** At a 0.5 SOL wallet the effective total stop is 0.175 SOL, not 0.30, one position runs at a time until you are ahead by 0.025 SOL, and one loss of more than about 0.075 SOL stops new buys. The floor is coherent (about 0.12 SOL left at the hard worst case). If you want the run to survive a first deep loss, it needs a larger wallet or a different rule (section 6). The default applied is DEC-024's own.
- **Its live picks are not the read's picks.** A good canary week does not move the formal read, and a bad one does not either (section 8).

**Quant-proof questions** (none is the owner's to answer).
- **Q1.** Synthetic pools in C1-NF's universe and in its September evidence; options A, B, C (section 9.3).
- **Q2.** Source of the "0 overlap" claim; the count-only overlap pass with H5 (section 9.2).
- **Q3.** Power of the fill-selection rule (3 pp, at least 8 unfilled of 30) at C1-NF's variance; whether to keep it as a rate tripwire (section 7 rule 1).
- **Q4.** N and the line for the divergence rule at C1-NF's paired variance (section 7 rule 2).
- **Q5.** Whether 1.9 s is the right landing halt given the book's flat response to latency in exploration (section 7 rule 3).
- **Q6.** Whether any of `boost_disabled`, `boost_share_low`, `boost_budget_or_slices_changed` should halt C1-NF rather than alert (section 7 rule 6).
- **Q7.** The pinned model (exploration-only; P2 or a separate canary model) and the bar for parity task 3, including what a live-mode miss may be (section 11 items 12 and 13).
- **Q8.** A price-impact check at 0.30 SOL for C1-NF's pools, before T2 (section 6).
- **Q9.** The rent number: 2,039,280 lamports (EXP-025 section 6) against 1,513,840 (DEC-024 section 4).
- **Q10.** The twin: priced at the canary's stake and fee, and how often the ladder's "not negative" checks pass under no edge (sections 6 and 10).
- **Q11.** Appendix A: scope, timing, and that gap 1 is the only clock-bound one (section 8).
- **Q12.** The stop-probability table (section 12).
- **Q13.** The oracle keying difference between #504 (every mint) and EXP-025 section 5.3 (pools first printing at or after 2026-10-16T01), and whether the executor may be stricter than the read (section 9.1).
- **Q14.** Stop coherence at a 0.5 SOL wallet (section 6): the effective total stop 0.175 (35% cap) against the owner's 0.30; the exposure test (one open until +0.025; the first deep loss ends buys, probability about 0.93 over 50 trades if the exploration loss rate holds); and the options, including the largest total stop whose hard worst case clears the 0.05 floor (0.244). Which setting keeps the floor coherent and still lets the canary measure.

## Appendix A. Draft EXP-025 Amendment 2 (not applied; to be merged as its own PR into `EXP/EXP-025-c1nf-part1-prereg.md`)

**This is proposed text.** It edits nothing in EXP-025 today. The sentences in quotation marks are proposed wording in the form of [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md) Amendments 2 and 3. The owner has not seen them. The drafter did not open the notebook entries it names.

### Amendment 2 (`<MERGE INSTANT, UTC, from date -u at merge>`; before the shadow writes its first outcome for a decision before 2026-10-10T00, before any canary send, and before any outcome of a decision in Look 1's window exists): declared observation for pre-window and post-window decisions, the executor's CAP-PICK skip, observers and report disclosure

**Outcome-blindness statement (to be confirmed at merge).** Outcome-blind for every decision with T at or after 2026-10-10T00, the start of Look 1's counted window. At the merge instant, no C1-NF shadow or canary outcome (label, fill, exit, P&L, mean, CI or day sign) of any October decision had been computed or seen, from any source. To write it, no row, report or scratch file of forward-1002, forward-1002ev, walk 2, a forward-paper book or a runner was opened, and no key was used. **If any shadow or canary outcome of an October decision existed at the merge instant, this sentence is replaced by a plain statement of what was seen and when, as EXP-024 Amendment 3 did for H5's shadow.**

**The gap it closes.** Section 5.1 declares the real-time observation for "decisions inside the windows". Four things are not in it: decisions before 2026-10-10T00, which section 4's seal still protects (October rows before the window, including the training-label rows of graduations from 2026-10-09T00); decisions at or after 2026-10-24T00; the live executor's CAP-PICK skip (section 5.3 names the read and the shadow); and a list of observers with the report disclosure.

1. **Pre-window decisions.** "By a manager decision derived from the owner's recorded decision O3 (DEC-025) and the default the owner was told on 2026-10-09 (DEC-026 O-6, applied unless he objects), the paper shadow's and the live canary's outcomes for decisions with T in `[<MERGE INSTANT>, 2026-10-10T00)` are observed in real time. This is declared before any such outcome exists." The read's own labels for those rows (pass A inside the locked job) stay sealed. The read never takes the shadow's output as training input. **[If the owner objects to the O-6 default, this item is deleted and the shadow starts its outcome records at 2026-10-10T00.]**
2. **Post-window decisions.** "The same outcomes for decisions with T at or after 2026-10-24T00, up to the canary's `end_ms`, are observed in real time. No look reads them." This is not a CAP-PICK exception (item 3).
3. **The executor's CAP-PICK skip.** Section 5.3's exclusion and fail-closed oracle apply also to the live executor of DEC-026: from 2026-10-16T01 to the end of EXP-022's read it trades no mint the oracle answers True for, writes no CAP-PICK field into any record and joins no record to a pick. A missing, stale, erroring, non-boolean or undecided oracle means no buy. If the executor refuses a wider set than section 5.3's keying (DEC-026 section 9.1), each look's report says so.
4. **Observers.** The owner, Helm, the manager, builders, the DEC-026 watchdog (its Discord alerts, including the stops and the wallet line) and the daily check may read the canary's ledger and the shadow's outcomes in real time. The read tool may not: its inputs stay section 4's hours, it never takes the canary's ledger or the shadow's output, and its refusal set is not loosened. Observing them opens no tape row.
5. **Report disclosure.** The Look 1 report and the Look 2 report each state that the shadow's and canary's outcomes were observed in real time for decisions in the windows (section 5.1), for decisions before 2026-10-10T00 (item 1, if kept) and at or after 2026-10-24T00 (item 2); that this can have influenced later choices, for example an owner tier step; and that **any EXP-025 amendment dated after the first such outcome was made with outcomes in view and is listed in the report, not called outcome-blind.** The disclosure does not change the verdict.
6. **Unchanged.** The rule block, the five pinned lines, the patches and hashes, the windows, the alpha pair (0.005, 0.020), items 1 to 9 of section 7, P2 to P7, R1 to R14, section 11.5 and k = 1. Section 5.1's sentences stand: Look 1 and Look 2 are always read as written and never skipped, delayed, re-scoped or re-thresholded because of anything the shadow or canary shows; nothing they show may change any EXP-025 parameter; a canary halt, stop or tier step stops or changes the canary only.
7. **Spending.** Nothing is spent. No look is refused, withdrawn or not run because of this amendment.
8. **Enforcement in the tools.** The shadow withholds outcome-bearing records for decisions before 2026-10-10T00 unless started with a declaring flag (the builder names it, as `--h5-look2-observed EXP-024-Am2` was named for H5). The daily check alerts when that flag is missing while `LIVE_OK` exists, because without outcomes the canary has no twin and DEC-026 section 7 rule 2 cannot fire.
9. **Provenance.** A manager-side draft by the DEC-026 drafter, derived from DEC-025 section 4 and EXP-025 section 5.1 (the owner's words are the O3 sentence and the `OWNER_CANARY_CONFIRMED` line of DEC-026). The owner may revoke it. A revocation applies from its recorded instant, restores section 4's seal for decisions after that instant, and does not undo the disclosure for outcomes already observed.

**Fallback if it cannot merge in time.** Item 1 is the only clock-bound item. If the amendment is not merged before the shadow would write its first pre-window outcome, the shadow starts its outcome records at 2026-10-10T00, and items 2 to 5 wait. No canary send is allowed before the amendment merges (DEC-026 section 4), so waiting costs nothing in the seal.

## Sources

- [DEC-025](DEC-025-c1nf-family.md) (sections 4 and 5, `OWNER_DECISION_CONFIRMED`), [EXP-025](../EXP/EXP-025-c1nf-part1-prereg.md) (sections 0, 2, 3, 4, 5, 6, 7, 9, 10, 11).
- [DEC-024](DEC-024-h5-live-canary.md) with Amendments 1 to 3, [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md) Amendments 2 to 4, `docs/runbooks/h5-executor.md`, `tools/h5_executor.py` (`TIERS`, `TIER_WALLET_FRAC`, lines 147 to 163), `tools/pump_structure_monitor.py` (halt flags, WATCH_RULES, `SYNTHETIC_MAX`, `post_complete_buy_seen`).
- [DEC-019](DEC-019-execution-probe.md), [DEC-020](DEC-020-size-step-proposal.md), [DEC-021](DEC-021-champion-challenger.md) section 7, [DEC-018](DEC-018-live-trial-readiness.md), [EXP-022](../EXP/EXP-022-cap-pick-part1-prereg.md) section 9.
- PR bodies: #502 (ledger, 53da60f), #503 (shadow, 2a96ce0), #504 (executor, closed, a98ff07), #506 (features, 3ae9915), #509 (pick oracle, 983b12c), #517, #518.
- `/data/mal/hunt-1008/c1nf-verify/VERIFY.md` sections 5.2 and 6 (as EXP-025 copies them) and `/data/mal/hunt-1008/JUDGE-4.md` (text search only). Exploration; not evidence of an edge.
- `docs/HANDOFF.md`, state 10-09 ~07:35Z (ladder table, C1-NF open items, A3 state).
