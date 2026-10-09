# DEC-026: C1-NF live measurement canary (0.02 SOL, second wallet)

| Field | Value |
| --- | --- |
| **Status** | **DRAFT 2026-10-09, for the owner's review.** Every choice that is the owner's is tagged **[OWNER CHOOSES]** with a recommended default (section 3 lists them). It takes effect on merge, with quant-proof OK on its final head. **No live send** until every item in section 11 is done. Appendix A is draft text for a separate EXP-025 amendment, not an edit to EXP-025. |
| **Decider** | Vaan (owner) for the **[OWNER CHOOSES]** items. The Claude manager runs the rest. |
| **Date** | 2026-10-09 |
| **Builds on** | [DEC-025](DEC-025-c1nf-family.md) section 4 (the canary runs only under this DEC), [EXP-025](../EXP/EXP-025-c1nf-part1-prereg.md) sections 5.1, 5.3 and 10, [DEC-024](DEC-024-h5-live-canary.md) (the template, with Amendments 1 and 2), [DEC-019](DEC-019-execution-probe.md) (custody, executor), DEC-020, [DEC-021](DEC-021-champion-challenger.md) section 7, [EXP-022](../EXP/EXP-022-cap-pick-part1-prereg.md) section 9, [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md) Amendments 2 to 4 (the form of the declared observation) |
| **Amends** | The paper-only fence and the "no trading keys on hosts" rule (CLAUDE.md, CONSTITUTION), **only** for one executor profile (`c1nf`) on **one second wallet**, on `mal-fast-0`, for this canary. DEC-024 extended DEC-019's exception to the H5 canary and its wallet. This DEC adds a second wallet for a second profile and nothing else. This draft does not edit CLAUDE.md. |
| **Does not amend** | **The promotion gate.** DEC-014, DEC-016, DEC-021 sections 6 and 9, DEC-024 (its wallet, files, limits and override are separate), EXP-022, and EXP-024. **EXP-025's rule, windows, looks, alpha pair (0.005, 0.020), gate items and read tool.** Canary trades are never a book and never count toward any gate, read or promotion. |

**How this draft was written.** From repo docs, the PR bodies of #502, #503, #504 and #506, the `/data/mal/hunt-1008/c1nf-verify/VERIFY.md` summary lines that EXP-025 already copies, and `tools/h5_executor.py` constants. No host was contacted. No sealed block, forward-1002, forward-1002ev, walk-2, forward-paper or runner row, and no C1-NF or H5 outcome, was opened. The MiScusi notebook entries it cites (n_hZaavyDyNcJcZg and others) were **not opened** by the drafter; they are cited as the repo documents relay them. **Labels:** [measured] is copied from a cited file. [pinned] is a value this DEC fixes. [inferred] is arithmetic or reasoning from cited numbers. [exploration] marks a number from spent September blocks; it is not evidence of an edge. Nothing in this file says or implies that C1-NF is positive.

## 1. The owner's instruction

The only owner words in the repo for this canary are in [DEC-025](DEC-025-c1nf-family.md) (`OWNER_DECISION_CONFIRMED`, O3), as the manager recorded them on 2026-10-09:

> "I'm also down to do some testing with some small trades in parallel."

DEC-025 adds, in the manager's voice and not the owner's: "scope (0.02 SOL stakes, a separate wallet, DEC-026) is the manager's, not the owner's words". The task that produced this draft relays the O3 entry as "a small live C1-NF canary in parallel" (notebook n_hZaavyDyNcJcZg) and says it rides "the same tier ladder as H5". Whether the ladder's upper steps are inside "small" is **[OWNER CHOOSES]** O-2 below.

## 2. What it is and is not

- **It is:** a live measurement of how the pinned C1-NF rule's picks land and exit on chain: landing time from the decision slot, failure and guard-revert rates, exit precision, and whether the picks that fill differ from the picks that do not (EXP-025 section 3, "Adverse fills": correlated fill failure is the main untested live risk).
- **It is not:** gate evidence, a book, a paper pass, or a claim that C1-NF is positive. No report, note, Console entry or message may say a canary result shows a profit or an edge.
- **It cannot test the edge, and its size makes that worse.** The deciding cell is 0.25 SOL at 505,000 lamports per send. At 0.02 SOL the same two sends are a larger share of the stake (section 6, "Trial-size effect").
- **It does not change the gate.** DEC-025 section 4 and EXP-025 section 5.1: "Scale-up beyond canary size needs a passed read or a further explicit owner override." This DEC records no such override (section 10).
- **It does not replace the read.** EXP-025 Look 1 (`[2026-10-10T00, 2026-10-17T00)`, alpha 0.005) and Look 2 (cumulative to 2026-10-24T00, alpha 0.020, only if Look 1 did not pass) stand and are read as written (section 8).
- **The picks are the live rule, not the read's rule.** The canary trades the shadow's picks, built from the tip tape and a live feature engine. The read uses the pinned October adapter on forward-1002ev and walk 2. Their decisions will differ (EXP-025 section 5.1: "The shadow is not the read"). The live engine also differs from the batch (#506: exact on the replay state, drift in the live state). A canary result is therefore about the live build, not about the read's book.

## 3. Owner choices at a glance

Each is explained where it appears. The default is the manager-side recommendation, not a decision.

| # | **[OWNER CHOOSES]** | Recommended default | Where |
| --- | --- | --- | --- |
| O-1 | Funding of the second wallet | **0.25 SOL**, nothing more until the T1 step is asked for | section 6 |
| O-2 | Is the ladder's T1 (0.10) and T2 (0.30) inside O3's "small"? | **No.** T0 only is canary size. Each step up needs a dated `OWNER_LADDER_CONFIRMED` line in this file (DEC-025 section 4 wording) | section 10 |
| O-3 | Priority fee per send | **505,000 lamports on the buy and the sell**, the rule's priced cost. At 0.02 SOL the round trip is 5.05% of the stake | section 6 |
| O-4 | Canary end | **`end_ms` 2026-10-24T00:30Z**, the end of Look 2's counted window. Extension is a dated owner line | section 6 |
| O-5 | After a Look 1 FAIL or NOT_DECIDABLE | **Keep going**, because Look 2 still runs. **Stop new buys at the next 00:00Z after a Look 2 FAIL or NOT_DECIDABLE** (EXP-025 section 7: a FAIL closes C1-NF) | section 7 |
| O-6 | Observe shadow and canary outcomes for decisions before 2026-10-10T00 (the 10-09 October rows) | **Declare it** (Appendix A item 1), as EXP-024 Amendment 3 did for H5. The alternative is to keep those outcomes sealed and start the shadow's outcome records at 2026-10-10T00 | section 8, Appendix A |
| O-7 | The T0 limits (stake 0.02, 2 open, 30 a day, stops 0.08 and 0.12, 35% wallet cap) | **As written**, mirroring DEC-024 and `tools/h5_executor.py:151-157` | section 6 |

The quant-proof questions are in section 14. None of them is the owner's to answer.

## 4. When it starts

- **After Appendix A is merged as an EXP-025 amendment**, or after the fallback in Appendix A applies. Appendix A item 1 must be merged before the shadow writes its first outcome for any decision made before 2026-10-10T00, and in any case before the first canary send.
- **After every item in section 11.** The build, the pinned model, the parity and md5 proofs, Helm's install and dry run, funding, and the watchdog test.
- **Never while a live-halt rule of section 7 is on.** That includes the A3 flags that apply to C1-NF (section 7, rule 6) and the open synthetic question (section 9.3).
- **No date bar.** DEC-024 barred sends before 2026-10-10T00Z and its Amendment 1 lifted the bar. Here none is needed: every pool decided before 2026-10-10T00 is outside every look's counted window, and Appendix A item 1 covers the observation. The ladder is slow enough that the first send will probably be after the build is done, which is not before 2026-10-10 on the PR states in section 11.
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
| **`TIER`** | `/etc/mal-c1nf/TIER`, the same checks, content exactly `T0`, `T1` or `T2`. Missing or invalid means T0. Helm writes it only on the manager's written ask (section 10) |
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
| Stake, open, trades, stops | By tier, next table. The executor reads `/etc/mal-c1nf/TIER` before every buy. Config may only lower a tier's values |
| Attempts | The tier's trades per UTC day, **counting guard reverts**. Picks that arrive when the open cap or the day's count is used are logged as skipped, with the reason |
| Total stop | The tier's total stop, **also capped at 35% of the wallet balance measured when the tier started** (`TIER_WALLET_FRAC` = 0.35, `tools/h5_executor.py:157`). The cap uses a tier-start realized baseline (the H5 G1 fix); the rebuilt executor must show the same |
| Priority | **[OWNER CHOOSES] O-3.** Default 505,000 lamports per send, on the buy and the sell, the cost EXP-025 section 6 pins for the deciding cell. DEC-021 section 7 says a new priority setting needs a live calibration; this canary is it for 505,000. The alternative is 55,000 (H5's value): cheaper, but then the landing time measured is for a send the rule did not price |
| Buy guard | On-chain `min_out` so the buy reverts if size / tokens out exceeds 1.15 x the **decision-time spot** (EXP-025 section 2.1). The pick must carry the decision-time `q_lamports` and `base_reserve`. **A pick without them gets no buy** (fail closed). #504 allowed a fallback measured at receipt; this DEC does not |
| Staleness | A pick more than 3 s of chain age old (slots since `SD_slot` times the measured s-per-slot, plus a 2x wall backstop) is refused. Config may lower the 3 s, never raise it (#504 design) |
| Sell | Full balance, then close the ATA. `min_out` at 0.85 of the quote; after 2 reverts, or past the plan's deadline, resend at 0.65 with higher priority. Never `min_out` 0. The plan is anchored on the landed slot (#504: sell at landing + 300 s + 0.55 s, escalate at +315 s, deadline at +370 s) |
| Out-of-rule entry | A buy that lands more than 5 s after `SD_slot` exits at once and latches `out_of_rule_entry` (halt, section 7 rule 3) |
| Balance guard | Stake + 0.02 SOL (the probe's constant) |
| Duration | **[OWNER CHOOSES] O-4.** Default: `end_ms` 2026-10-24T00:30Z, a code constant in the reviewed live config. Then STOP. The end is the end of Look 2's counted window plus the last exits. Beyond it, Appendix A item 2 is the only cover, so an extension is the owner's dated line |
| Files | Section 5 |

**The ladder (the owner's, 2026-10-09; MiScusi notebook n_xaHk-8t27C8qbw as DEC-024 section 6 cites it; I did not open it).** The numbers mirror H5's `TIERS` (`tools/h5_executor.py:151-154`) and the HANDOFF table.

| Tier | Stake (SOL) | Max open | Trades/day | Daily stop (SOL) | Total stop (SOL) | Steps up after |
| --- | --- | --- | --- | --- | --- | --- |
| **T0** | 0.02 | 2 | 30 | 0.08 | 0.12 | about 50 closed trades |
| T1 | 0.10 | 3 | 40 | 0.40 | 0.60 | about 25 closed trades |
| T2 | 0.30 | 3 | 40 | 1.20 | 1.80 | onward; 25 to 50 trades per step |

- **No skipped steps.** The ladder ends at T2 (0.30 SOL). A tier above it needs a new reviewed code change and a new DEC.
- **T1 and T2 are written here but inactive** until the owner's line in section 10 (**O-2**). Only T0 is canary size.
- **The trades-per-day cap is above the book's rate.** The exploration book had about 20 trades a day (419 trades over 21 days [exploration, VERIFY]) against a cap of 30. The 30 a day and 2 open are H5's values. Whether 2 open binds when picks cluster is read off the shadow's skipped count, not guessed.
- **T2's price-impact check is H5's, not C1-NF's.** `T2_IMPACT_OK` rests on a replay model for H5 triggers (`/data/mal/hunt-1008/h5-work/IMPACT.md`, per `tools/h5_executor.py:158-162`). C1-NF's pools are older and deeper (the stage-1 real quote is at least 20 SOL; VERIFY's pick median is 142.65 SOL [exploration]). The check is not transferable. T2 needs its own (section 14, Q8).

**Funding (O-1).** Default **0.25 SOL**, nothing more before the T1 step. At T1 and T2 the stakes and open caps need far more than that (T2: 3 x 0.30), and the 35% cap on the total stop would clip the table's stops on a small wallet. Each step's funding is a separate owner decision when the manager asks for the step.

**Wallet floor, hard worst case [inferred; T0].** With 0.25 SOL funded: 0.25 - 0.0875 (total stop, clipped by the 35% cap: min(0.12, 0.35 x 0.25)) - 0.04 (two open positions go to zero) - about 0.004 (two ATA rents of 2,039,280 lamports stranded) - about 0.002 (four sends at 505,000) = **about 0.116 SOL**. The realized-loss stops count closed trades only, so the open-exposure term is not optional.

**Trial-size effect [inferred arithmetic from EXP-025 section 6's numbers].**
- One send at 505,000 lamports is 2.525% of a 0.02 SOL stake. A round trip is **5.05%**. At 0.10 SOL it is 1.01%, at 0.30 SOL 0.337%, and at the deciding cell's 0.25 SOL **0.404%**.
- The deciding cell's September means already include 0.404%. At 0.02 SOL the same two sends cost **4.646 percentage points more**. September's rent-inclusive means were flat +7.458% and pressure +6.640% [exploration, VERIFY section 5.2]. Minus 4.646 pp they are **+2.8% and +2.0%** at 0.02 SOL, **if** September's edge held and nothing else changed. That is arithmetic, not a forecast.
- The per-trade SD is about 0.34 to 0.37 of stake (EXP-025 section 3: 0.373 modelled, 0.342 implied by VERIFY's trade CI). At 50 trades the standard error of a mean is about 5 pp. **A +2% expected mean against a 5 pp standard error means the canary's own P&L cannot tell a working rule from a failing one.** The ladder's "not negative" checks (section 10) are close to a coin flip. Quant-proof computes how close (section 12).
- The fee burn alone at T0: 50 filled trades x 1,010,000 lamports = **0.0505 SOL, 20% of 0.25 SOL**, before any price move. At 30 fills a day it is 0.0303 SOL a day.
- A stranded position keeps its ATA rent: 2,039,280 lamports is **10.2%** of a 0.02 SOL stake. (DEC-024 section 4 uses 1,513,840 lamports for H5's ATA. The two numbers differ; the canary uses whichever its executor actually pays. Q9.)
- The paper twin and the live-versus-twin comparison are priced **at 0.02 SOL and the canary's fee**, not scaled from the 0.25 SOL cell. The shadow's outcome variants (no fee, 55k, 505k, 505k + rent; #503) do not state a stake in the PR body. The builder confirms and, if needed, adds a variant at the canary's stake.

**What the stops do.** They are circuit breakers for a build or regime fault, not a P&L tool. The daily stop is 4 stakes and the total stop 6 stakes at T0. A stop firing is not evidence about C1-NF.

## 7. Live-halt rules

Any of these halts new buys at once (`STOP`). Open positions exit on the timer unless the manager places `HALT`. A halt is never followed by a retune of the rule, the model, the cap or the threshold.

1. **Fill selection (from #504's design).** Every pick that reaches the executor is ledgered `pick_status` filled or unfilled. The monitor reads the shadow's `c1nf_outcome` for each, over the last 30 monitored picks with outcomes. If (mean outcome of the unfilled) - (mean outcome of the filled) exceeds **3 pp**, with at least **8 unfilled** and at least 1 filled, it latches `fill_selection_adverse`. After a clear, only outcomes that arrive later count. Book refusals (held, cooldown, duplicate, open cap) and refusals by a stop are not fill failures and are not monitored.
   - **Why it exists.** The edge sits in the right tail of hot entries. VERIFY section 6 [exploration]: if the best 10% of fills failed, the no-fail mean would fall to +1.855%; if the best 28.9%, to -5.116%.
   - **It is weaker than it reads (the builder's own flag, #504).** With a per-pick SD of about 34 pp and 8 unfilled against 22 filled, the standard error of the difference is about 14 pp, so a 3 pp line fires by chance in roughly 40% of evaluations once 8 are unfilled. Fewer than 8 unfilled of 30 cannot latch. **It is a fill-rate tripwire in practice.** Kept as designed (a false halt costs a restart; a missed one costs the measurement). Q3 asks quant-proof to size it.
   - It uses the shadow's outcomes, which are observed in real time under Appendix A.
2. **Divergence from the paper twin.** After at least **50 fills** (the end of T0; DEC-024 uses 100, but C1-NF has about 20 picks a day), the live-minus-twin per-trade CI90 upper bound is below **-1 pp** (live worse than its twin by more than 1 pp). The twin is the shadow's outcome for the same pick, **re-priced at the canary's stake, fee and landing slot** (section 6). A CAP-PICK pick is never traded and has no twin. Q4 asks quant-proof to size N and the line at C1-NF's variance.
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
- **The total stop**, or `end_ms` (section 6).
- **A seal breach.**
- **EXP-025 Look 2 FAIL or NOT_DECIDABLE (O-5).** New buys stop at the next 00:00Z after the Look 2 report, unless the owner extends for execution measurement only. After a Look 1 FAIL or NOT_DECIDABLE the canary **keeps going**, because Look 2 still runs and a Look 1 FAIL at alpha 0.005 is a thin read (EXP-025 section 3: Look 1 passes about 26% of the time if September holds). After a Look 1 PASS the read ends; the canary keeps measuring to `end_ms` and nothing else follows from it. A PASS leads to quant-proof, a paper twin, the owner's yes and the DEC-018 / 019 / 020 path (EXP-025 section 7), not to this canary scaling.
- **Not an end: BOOST off.** Unlike DEC-024 (`toggle_boost`), a BOOST switch-off does not end this canary by rule (rule 6 table).
- **Not an end: the CAP-PICK oracle.** From 2026-10-16T01 without a working oracle the executor refuses every buy in the seal window, which pauses the canary (section 9.1).

## 8. Relation to EXP-025

**What EXP-025 already says (merged in #501).** Section 5.1 declares, before any shadow or canary trade, that C1-NF's paper shadow and small live canary outcomes "for decisions inside the windows may be watched in real time", that "Look 1 and Look 2 are always run (Look 2 under its own condition) and reported as written", that neither is "skipped, delayed, re-scoped or re-thresholded because of anything the shadow or the canary shows", and that "Nothing the shadow or canary shows may change any EXP-025 parameter". It says the canary's trades "are real chain activity by one participant, appear in the tape as ordinary rows, and are not removed (that would edit chain truth). The read reports them." Section 4's seal says, for every other October hour, that no one computes a C1-NF label, fill, exit, P&L, mean, CI or day sign "except the declared observation in section 5".

**This DEC relies on that declaration and adds to it only through Appendix A.**
- **Look 1 and Look 2 are always read as written.** A canary halt (section 7), a stop (section 6) or a tier step (section 10) stops, changes or scales the canary only. It never stops, delays, re-scopes or re-thresholds either look, and never changes a parameter, window, alpha, gate item or refusal of EXP-025.
- **The canary's scale-up decision is a separate business decision, not EXP-025 evidence.**
- **Disclosure.** Concurrent observation lets canary results influence later choices, for example an owner tier step. That never changes the formal read, and each look's report says so (Appendix A item 5).
- **Who may read what in real time.** The owner, Helm, the manager, builders, the section 5 watchdog (its Discord alerts, including the daily and total stops and the wallet line) and the daily check may read the canary's ledger and the shadow's outcomes. **The one excluded reader is the EXP-025 read tool.** Its inputs stay the section 4 hours of EXP-025, and it never takes the canary's ledger, the shadow's output or the shadow's ledger (EXP-025 section 5.1).
- **The public wallet.** The wallet is public on chain, so anyone can compute its P&L. That is consistent with the declaration for decisions in the windows. A trade on a decision outside them is covered by Appendix A item 2.
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
  - The H5 wallet's sells land at about graduation + 325 to 450 s, inside the 300 s feature window `[graduation + 300, graduation + 600]` of C1-NF's first decisions on that pool (v5, new5, flows). At 0.02 SOL against a stage-1 v5 of at least 1 SOL that is at most 2% of v5. Both wallets' trades are ordinary tape rows and are not removed.
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
