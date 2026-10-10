# DEC-026: C1-NF live measurement canary (0.05 SOL, second wallet)

| Field | Value |
| --- | --- |
| **Status** | **DRAFT 2026-10-09, updated about 17:50Z** for quant-proof's NOT OK on e570954 and the owner's 17:30Z decisions (stake 0.05 SOL, synthetic pools kept in). The owner's words are in the `OWNER_CANARY_CONFIRMED` line and its addendum in section 1. Two earlier defaults still apply unless he objects. The manager's terms are marked. It takes effect on merge, with quant-proof OK on its final head. **No live send** until every item in section 11 is done. Appendix A is draft text for a separate EXP-025 amendment, not an edit to EXP-025. **[Note 2026-10-10, QP-1010 (b1) item 1.]** #522 merged this file at 2026-10-09T18:00:11Z with the DRAFT label above. No quant-proof OK on a final head is recorded in this file, so the second condition of "takes effect on merge, with quant-proof OK on its final head" is not met. QP-1010 calls this ambiguous; read as written, the DEC is **not yet in effect**. The OK, its head and its instant go in section 11 item 3 when given. No live send before section 11 is done, either way. |
| **Decider** | Vaan (owner) for the stake, fee, limits, funding, end date and synthetic pools (answered). The Claude manager runs the rest. |
| **Date** | 2026-10-09 |
| **Builds on** | [DEC-025](DEC-025-c1nf-family.md) section 4 (the canary runs only under this DEC), [EXP-025](../EXP/EXP-025-c1nf-part1-prereg.md) sections 5.1, 5.3 and 10, [DEC-024](DEC-024-h5-live-canary.md) (the template, with Amendments 1 to 3), [DEC-019](DEC-019-execution-probe.md) (custody, executor), DEC-020, [DEC-021](DEC-021-champion-challenger.md) section 7, [EXP-022](../EXP/EXP-022-cap-pick-part1-prereg.md) section 9, [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md) Amendments 2 to 4 (the form of the declared observation) |
| **Amends** | The paper-only fence and the "no trading keys on hosts" rule (CLAUDE.md, CONSTITUTION), **only** for one executor profile (`c1nf`) on **one second wallet**, on `mal-fast-0`, for this canary. DEC-024 extended DEC-019's exception to the H5 canary and its wallet. This DEC adds a second wallet for a second profile and nothing else. This draft does not edit CLAUDE.md. |
| **Does not amend** | **The promotion gate.** DEC-014, DEC-016, DEC-021 sections 6 and 9, DEC-024 (its wallet, files, limits and override are separate), EXP-022, and EXP-024. **EXP-025's rule, windows, looks, alpha pair (0.005, 0.020), gate items and read tool.** Canary trades are never a book and never count toward any gate, read or promotion. |

**How this draft was written.** From repo docs, the PR bodies of #502, #503, #504 and #506, the `/data/mal/hunt-1008/c1nf-verify/VERIFY.md` summary lines that EXP-025 already copies, and `tools/h5_executor.py` constants. No host was contacted. The owner's 17:00Z answers were relayed by the coordinator, and the drafter did not open the notebook entry. At 17:10Z the branch was merged with origin/main at d6f217d, and `tools/h5_executor.py` was read for the stop test and the balance guard. No sealed block, forward-1002, forward-1002ev, walk-2, forward-paper or runner row, and no C1-NF or H5 outcome, was opened. The MiScusi notebook entries it cites (n_hZaavyDyNcJcZg and others) were **not opened** by the drafter; they are cited as the repo documents relay them. **Labels:** [measured] is copied from a cited file. [pinned] is a value this DEC fixes. [inferred] is arithmetic or reasoning from cited numbers. [exploration] marks a number from spent September blocks; it is not evidence of an edge. Nothing in this file says or implies that C1-NF is positive.

## 1. The owner's instruction

The only owner words in the repo for this canary are in [DEC-025](DEC-025-c1nf-family.md) (`OWNER_DECISION_CONFIRMED`, O3), as the manager recorded them on 2026-10-09:

> "I'm also down to do some testing with some small trades in parallel."

DEC-025 adds, in the manager's voice and not the owner's: "scope (0.02 SOL stakes, a separate wallet, DEC-026) is the manager's, not the owner's words" (the owner's answers below set the stake, finally at 0.05 SOL). The manager's brief for this draft relays the O3 entry as "a small live C1-NF canary in parallel" (notebook n_hZaavyDyNcJcZg) and says it rides "the same tier ladder as H5".

The owner answered the DEC-026 choices on 2026-10-09 at about 17:00Z (MiScusi notebook entry "OWNER ... C1-NF canary (DEC-026)", as the coordinator relayed it; the notebook id was not given to the drafter and the entry was not opened):

```
OWNER_CANARY_CONFIRMED: 2026-10-09 ~17:00Z (owner, answering the manager's DEC-026 questions). Chosen option, verbatim as relayed: "Fees drop to ~1% of each trade, so the canary can actually show whether the edge exists. H5's 20-trade trial already tests the shared order code (C1-NF's bot is built on H5's), so skipping a 0.02 step is low risk. Needs a ~0.5 SOL wallet. Limits: 2 open, daily stop 0.20 SOL, total stop 0.30 SOL."
```

- **Also answered, as relayed:** funding 0.5 SOL (O-1); keep going after a Look 1 FAIL until Look 2 (O-5); `end_ms` 2026-10-24T00:30Z (O-4). The owner's total capital across all tests is **1.29 SOL**: this wallet gets 0.5 SOL (confirmed in a split question) and H5 gets +0.5 at its T1 step. **No further owner capital is planned; scale-ups come from profits.**
- **Defaults the owner was told, applied unless he objects:** each step above the canary's stake needs a dated owner line (O-2); the 10-09 pre-window observation is declared (O-6, Appendix A item 1). The shadow's binding guard (outcome records start at 2026-10-10T00, section 11 item 8) means no pre-window outcome exists, so Appendix A item 1 is held in reserve.
- **Not in the owner's words, so the manager's terms:** the **35% wallet cap on the total stop** (mirrored from DEC-024; the option text the owner chose names only "total stop 0.30 SOL"), 30 attempts a day, the balance guard, and every halt rule.
- **The option's premise is not relied on.** "H5's 20-trade trial already tests the shared order code" is the option's text. C1-NF's executor is a separate profile with its own review and security review (section 11 item 9). This DEC adds no precondition on the result of H5's trial unless the manager does.

The owner then changed the stake. The coordinator relayed, at about 17:30Z, his answer to the manager's follow-up on the stop problem (section 6) and his decision on synthetic pools, in one notebook entry ("OWNER ... C1-NF keeps synthetic pools ... stake 0.05"; again not opened by the drafter):

```
OWNER_CANARY_CONFIRMED (addendum): 2026-10-09 ~17:30Z (owner, answering the manager's follow-up). Chosen option, verbatim as relayed: "Chance it stops early: ~6% if September's edge holds, ~22% if there's no edge. Fees are 2% of each trade instead of 1%, costing about 0.9 points per trade. Two trades can be open from the start, no code change, and the worst case leaves ~0.22 SOL. Can step up to 0.10 from profits."
```

- **[Note 2026-10-10, QP-1010 (b1) item 2.]** The option's "~6%" and "~22%" are P(a stop **before 50 fills**) under a realized barrier at 0.125 SOL, not the chance over the run. Over the run, at zero gross edge, under the executor's exposure test, P(total stop) is **0.57 / 0.55 by day 7 and 0.66 / 0.64 by `end_ms` for a 10-15 start** (flat / pressure; section 12.1). The owner's quoted words above stay as his words.
- **This addendum sets the stake at 0.05 SOL** and supersedes the 0.10 SOL of the line above. Its other limits stand: 2 open, daily stop 0.20 SOL, total stop 0.30 SOL as the owner's ceiling, the 505,000-lamport fee, 0.5 SOL funding.
- **"No code change":** the code ceiling stays 0.10 SOL; the reviewed live config lowers the stake to 0.05 (config may only lower).
- **"Can step up to 0.10 from profits":** a step needs the profits and a dated owner line (section 10).
- **Same entry, synthetic pools:** the owner chose to keep them **in** C1-NF's universe (EXP-025 Amendment 2, PR #529). The canary and the shadow trade the same population as the read (section 9.3).

## 2. What it is and is not

- **It is:** a live measurement of how the pinned C1-NF rule's picks land and exit on chain: landing time from the decision slot, failure and guard-revert rates, exit precision, and whether the picks that fill differ from the picks that do not (EXP-025 section 3, "Adverse fills": correlated fill failure is the main untested live risk).
- **It is not:** gate evidence, a book, a paper pass, or a claim that C1-NF is positive. No report, note, Console entry or message may say a canary result shows a profit or an edge.
- **It can show a fault or a large gap. It cannot prove an edge.** At 0.05 SOL the fixed cost is 2.02% of the stake, 1.6 pp above the 0.25 SOL cell's 0.404% (section 6, "What the canary can and cannot show"). The noise is the per-trade spread. The stops may end the run early: quant-proof's simulation puts that at about 6% by 50 fills if September's edge holds and about 22% at zero edge (section 6). **[Note 2026-10-10, QP-1010 (b1) item 2.]** Both figures are **before 50 fills**. Over the run, at zero gross edge, under the executor's exposure test, P(total stop) is **0.57 / 0.55 by day 7 and 0.66 / 0.64 by `end_ms` for a 10-15 start** (flat / pressure; section 12.1); if September's edge holds, about 0.06.
- **It does not change the gate.** DEC-025 section 4 and EXP-025 section 5.1: "Scale-up beyond canary size needs a passed read or a further explicit owner override." This DEC records no such override (section 10).
- **It does not replace the read.** EXP-025 Look 1 (`[2026-10-10T00, 2026-10-17T00)`, alpha 0.005) and Look 2 (cumulative to 2026-10-24T00, alpha 0.020, only if Look 1 did not pass) stand and are read as written (section 8).
- **The picks are the live rule, not the read's rule.** The canary trades the shadow's picks, built from the tip tape and a live feature engine. The read uses the pinned October adapter on forward-1002ev and walk 2. Their decisions will differ (EXP-025 section 5.1: "The shadow is not the read"). The live engine also differs from the batch (#506: exact on the replay state, drift in the live state). A canary result is therefore about the live build, not about the read's book.

## 3. Owner decisions (answered 2026-10-09, 17:00Z and 17:30Z)

The choices this draft offered are answered. Section 1 holds the owner's words. Two defaults he was told apply unless he objects.

| # | Decision | Answer |
| --- | --- | --- |
| O-1 | Funding of the second wallet | **0.5 SOL**, confirmed in a split question. The owner's total capital across all tests is 1.29 SOL: this wallet 0.5, and H5 gets +0.5 at its T1 step. **No further owner capital is planned; scale-ups come from profits** |
| O-2 | Steps above the canary's stake | **Default applied unless he objects:** each step needs a dated owner line. A step to 0.10 SOL also needs profits (the owner's option text: "Can step up to 0.10 from profits") (section 10) |
| O-3 | Stake and priority fee | **0.05 SOL per trade** (17:30Z; the 17:00Z answer was 0.10) with the rule's **505,000 lamports per send**, 2.02% of the stake per round trip. No 0.02 SOL tier. The code ceiling stays 0.10; config lowers it to 0.05; no code change |
| O-4 | Canary end | `end_ms` **2026-10-24T00:30Z**. Any extension is a dated owner line |
| O-5 | After a Look 1 FAIL or NOT_DECIDABLE | **Keep going until Look 2.** Stop new buys at the next 00:00Z after a Look 2 FAIL or NOT_DECIDABLE |
| O-6 | Observe outcomes of 10-09 (pre-window) decisions | **Default applied unless he objects:** declared in Appendix A item 1, held in reserve because the shadow's guard starts outcome records at 2026-10-10T00 |
| O-7 | Limits | The owner's: **2 open, daily stop 0.20 SOL, total stop 0.30 SOL** (a ceiling). The manager's terms, mirrored from DEC-024: **the 35% wallet cap on the total stop** (the owner's option text named only "total stop 0.30 SOL"), 30 attempts a day, the balance guard (section 6) |
| O-8 | Synthetic-migration pools | **Kept in** C1-NF's universe (EXP-025 Amendment 2, #529). The canary and the shadow trade all universe pools (section 9.3) |

**The stop problem is settled by the 17:30Z choice (section 6).** At a 0.5 SOL wallet the 35% cap makes the effective total stop **0.175 SOL**, not 0.30. At a 0.10 stake that stopped 31% / 29% of runs before 50 fills even if September held (63% / 60% at zero edge). At 0.05 SOL it is 6% / 5% and 22% / 20%, with a worst-case floor of about 0.219 SOL [quant-proof's simulation, relayed; exploration]. **[Note 2026-10-10, QP-1010 (b1) item 2.]** Every stop figure in this paragraph is **before 50 fills**. Over the run at 0.05 SOL, at zero gross edge, under the executor's exposure test, P(total stop) is **0.57 / 0.55 by day 7 and 0.66 / 0.64 by `end_ms` for a 10-15 start** (flat / pressure; section 12.1).

The quant-proof questions are in section 14. None of them is the owner's to answer.

## 4. When it starts

- **After EXP-025 Amendment 2 (#529, synthetic pools) is merged**, and **after Appendix A is merged as the next EXP-025 amendment.** Appendix A must merge before the first send. It need not merge before 2026-10-10T00:00Z, because the shadow's guard (outcome records start at 2026-10-10T00) is binding (section 8). **[Note 2026-10-10, QP-1010 (b1) item 3.]** #529 merged at 2026-10-09T17:53:58Z (8c38ee6), before 2026-10-10T00Z. Appendix A is **not** merged: EXP-025 at main has Amendments 1 and 2 only, so Appendix A is Amendment 3. It is open as #548. It still must merge before the first send.
- **After every item in section 11.** The build, the pinned model, the parity and md5 proofs, Helm's install and dry run, funding, and the watchdog test.
- **Never while a live-halt rule of section 7 is on.** That includes the A3 flags that apply to C1-NF (section 7, rule 6).
- **No date bar.** DEC-024 barred sends before 2026-10-10T00Z and its Amendment 1 lifted the bar. Here none is needed: every pool decided before 2026-10-10T00 is outside every look's counted window, and Appendix A item 1 covers the observation. The first send cannot come before the build of section 11 is done, and on the PR states listed there that is not before 2026-10-10.
- **No 0.02 SOL step.** The first send is at 0.05 SOL (the owner's choice), so the dry run, the soak and the reviews of section 11 carry the weight that a small first tier would otherwise carry.
- **Two wallets, one host.** The H5 canary keeps its own wallet, files and unit. Nothing in this DEC reads, writes or restarts them.

## 5. Custody and files

Same custody design as DEC-024 and DEC-019. Paths are proposals for Helm to confirm; Helm owns the exact install.

| Item | Rule |
| --- | --- |
| **Wallet** | A **second wallet**, created by Helm, separate from the H5 wallet (`5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk`). Public address: `<HELM FILLS; public key only>`. The manager never sees the key (DEC-019 section 5). The withdraw address stays with Helm (DEC-019 section 6 item 6) |
| **Key** | `/etc/mal-c1nf-key/c1nf-wallet.json`, root:root 0400, in a root:root 0700 directory. It reaches the unit only through systemd `LoadCredential=c1nf-wallet:...`. No other unit has this credential; the H5 unit cannot read it and this unit cannot read H5's [proposed; Helm verifies with the same sandbox checks as the H5 runbook. Since the note of 2026-10-10 (F2) this holds by separate Unix users, and runbook Steps 6 and 11 check it in both directions] |
| **User and sandbox** | `mal-c1nf`, its own system user (was `mal-live`; changed by the note of 2026-10-10, F2), with the same sandbox as the H5 unit: `ProtectHome=tmpfs`, `ProtectSystem=strict`, `TemporaryFileSystem=/var/lib/mal:ro`, `ReadWritePaths` limited to its own state dir, shadow feed bind-mounted read-only |
| **Pinned install** | A root-owned copy of the executor tree, `/usr/local/lib/mal-c1nf-exec/<sha>/`, with a manifest and a sha256 check made in the manager's own clone, as for H5 (`docs/runbooks/h5-executor.md`). A new sha is a new install and a new hash check |
| **State dir** | `/var/lib/mal-live/c1nf/` (mal-c1nf, 0700; the note of 2026-10-10): live state, counters, ledger (`pick_status`, fills, exits, wallet deltas), `STOP`, `HALT`. The executor tests the files exist and cannot create `LIVE_OK` |
| **`LIVE_OK`** | `/etc/mal-c1nf/LIVE_OK`, regular file, root:root, mode exactly 0644, parent `/etc/mal-c1nf` root:root 0755, no symlink. **The executor sends only while it exists.** Helm creates it. Removing it stops new buys like `STOP` |
| **`TIER`** | `/etc/mal-c1nf/TIER`, the same checks, content exactly `T1` (the canary tier: code ceiling 0.10 SOL, stake lowered to 0.05 by config) or `T2` (inactive until the owner's line, section 10). Missing or invalid means T1, the lowest. Helm writes it only on the manager's written ask (section 10) |
| **`STOP`, `HALT`** | The executor's own `/var/lib/mal-live/c1nf/{STOP,HALT}` and the wallet-wide `/var/lib/mal-live/{STOP,HALT}` (#504 tested both). `STOP` is no new buys, open positions still exit on the timer. `HALT` freezes all. **The wallet-wide `STOP` is still in place for H5 until H5's Step 11** ([HANDOFF](../docs/HANDOFF.md) state 10-09 ~07:35Z). While it stays, this unit cannot send. Helm does not remove it for C1-NF's sake **[Note 2026-10-10, QP-1010 (b1) item 4.]** Likely stale: H5 is LIVE at T0 since 2026-10-09T19:29:53Z ([HANDOFF](../docs/HANDOFF.md) line 64), which needs the wallet-wide `STOP` gone. **Not verified on the host** in this note; the manager checks `/var/lib/mal-live/STOP` before the go. The old sentence's rule stands: Helm does not remove it for C1-NF's sake. |
| **Auditd** | A watch on the new key file, as for the probe key |
| **Watchdog** | Its **own** timer and config, `mal-c1nf-watch` (Discord: stuck, structure halt, stop fired, restarts, wallet line, tier change), not H5's. The H5 watchdog stays H5's. The webhook goes to Helm over the private channel, never into a repo. Tested before `LIVE_OK` |
| **Credentials** | Helius env shared with the probe and H5 (`/etc/mal-probe-rpc/helius.env`, root:root 0600). **No Jito, no Sender, no LaserStream, no paid RPC: $0 extra** (owner plan 10-08). Public RPC is an exit-only fallback sender. Any Helius credits the canary or its feed use are reported in the daily note with the unit and the count |

## 6. Limits and tiers (code constants; config may only lower them)

| Limit | Value |
| --- | --- |
| Rule | Frozen C1-NF v1 as EXP-025 section 2 pins it: stage 1, the LightGBM model at prediction > 0.02, the cap `h_top1` <= 0.5 (NaN dropped) **before** the book, one position per mint (re-entry only if the decision time is at least the previous exit + 60 s), buy at decision slot + `round(1.3 s / s-per-slot)`, exit 300 s after landing |
| Venue | PumpSwap, the canonical PDA pool of a graduated mint in the rule's universe (V0 in [17.5e9, 17.7e9], first print within [-5 s, +120 s] of `complete`), WSOL quote. **Synthetic-migration pools are in the universe** (section 9.3) |
| Model | One pinned model file, sha256 checked at start (section 11, "Pinned model"). A change of file or hash is a halt (section 7, rule 7) |
| Stake | **0.05 SOL** per entry (the owner's 17:30Z choice). The **code ceiling is 0.10 SOL**; the reviewed live config lowers it to 0.05 (config may only lower; no code change). No 0.02 SOL tier |
| Concurrent | At most **2** open positions (the owner's). At stake 0.05 both are reachable from the first send (exposure test below) |
| Attempts | At most **30** buy attempts per UTC day, counting guard reverts (DEC-024 section 4's number; the exploration book had about 20 trades a day, 419 over 21 days [exploration, VERIFY]). Picks that arrive when 2 positions are open or the day's 30 are used are logged as skipped, with the reason |
| Daily stop | Realized loss of **0.20 SOL** in a UTC day (the owner's): no new buys until 00:00Z |
| Total stop | **0.30 SOL is the owner's ceiling** (his option text names only "total stop 0.30 SOL"). **The 35% cap is the manager's term**, mirrored from DEC-024: the total stop is capped at 35% of the wallet balance measured when the tier started (`TIER_WALLET_FRAC` = 0.35, `tools/h5_executor.py:173`). **At a 0.5 SOL wallet the effective total stop is min(0.30, 0.35 x 0.5) = 0.175 SOL.** The owner's 0.30 binds only from a wallet of about 0.857 SOL (0.30 / 0.35). The daily 0.20 is above the effective total stop, so from a fresh wallet the total stop binds first. No new buys after it until the owner restarts. The cap uses a tier-start realized baseline (the H5 G1 fix); the rebuilt executor must show the same |
| Exposure test | Inherited from H5 and **kept** (`tools/h5_executor.py:1134-1149`, with a test): before each buy the executor assumes every open or in-flight position and this stake are lost, and refuses if (tier realized loss + that exposure) reaches the total stop, or (day realized loss + that exposure) reaches the daily stop. #504 reused H5's limits and stops, so C1-NF's executor inherits it unless it overrides it **[Note 2026-10-10, QP-1010 (b1) item 5.]** Stale. #530 (merged 2026-10-10T08:59:53Z) runs on main's H5 core, and the test is `h5_executor._budget_stop` (`tools/h5_executor.py:1118`; the exposure lines are 1129-1139 at b17dc0b, 1139-1149 at QP-1010's read of 528f0ca), with a second, run-cumulative check against the highest total stop of any tier entered in the run (`_run_total_stop_lamports`). Moved line references in this file, at b17dc0b: `TIER_WALLET_FRAC` :180 (cited :173); `DEFAULT_WALLET_FLOOR_LAMPORTS` line 188 (cited 181); `TIERS` :174 (cited :167-170); `T2_IMPACT_OK` :185 (cited :178); `balance_need` :506-510 (cited 499-503). |
| Priority | **505,000 lamports per send**, on the buy and the sell (the owner's answer; the cost EXP-025 section 6 pins for the deciding cell). **2.02% of a 0.05 SOL stake per round trip.** DEC-021 section 7 says a new priority setting needs a live calibration; this canary is it. **Escalated and emergency sends: 1,010,000 lamports** (Amendment 1 item A) |
| Buy guard | On-chain `min_out` so the buy reverts if size / tokens out exceeds 1.15 x the **decision-time spot** (EXP-025 section 2.1). The pick must carry the decision-time `q_lamports` and `base_reserve`. **A pick without them gets no buy** (fail closed). #504 allowed a fallback measured at receipt; this DEC does not |
| Staleness | A pick more than 3 s of chain age old (slots since `SD_slot` times the measured s-per-slot, plus a 2x wall backstop) is refused. Config may lower the 3 s, never raise it (#504 design) |
| Sell | Full balance, then close the ATA. `min_out` at 0.85 of the quote; after 2 reverts, or past the plan's deadline, resend at 0.65 with higher priority. Never `min_out` 0. The plan is anchored on the landed slot (#504: sell at landing + 300 s + 0.55 s, escalate at +315 s, deadline at +370 s) |
| Out-of-rule entry | A buy that lands more than 5 s after `SD_slot` exits at once and latches `out_of_rule_entry` (halt, section 7 rule 3) |
| Balance guard | H5's `balance_need` (`tools/h5_executor.py:499-503`): stake + buy priority + base fee + 2,100,000 lamports of ATA rent + **1,015,000 per exit (one escalated send + base fee)** (open positions plus this one) + stakes in flight + a **wallet floor of 0.05 SOL** (`DEFAULT_WALLET_FLOOR_LAMPORTS`, line 181; config may raise it). **A first 0.05 SOL buy needs 103,625,000 lamports, about 0.104 SOL** (Amendment 1 item D). This replaces DEC-024 section 4's older "stake + 0.02 SOL" (0.07 SOL here); the code constant is the stricter **[Note 2026-10-10, QP-1010 (b1) item 6.]** QP-1010 read "1,000,000 per exit" at 528f0ca. Amendment 1 item D (#544, merged 2026-10-10T09:36:10Z) had already replaced it with the 1,015,000 above (#530's `c1nf_exit_reserve`). The first-buy need is still about 0.104 SOL. |
| Duration | `end_ms` **2026-10-24T00:30Z** (the owner's), a code constant in the reviewed live config. Then STOP. The end is the end of Look 2's counted window plus the last exits. Beyond it, Appendix A item 2 is the only cover, so an extension is the owner's dated line |
| Backstops | `max_attempts` **450** and `max_days` **15** (code constants), backstops only: neither binds before `end_ms`, the daily attempt cap or the total stop (Amendment 1 items F and G) |
| Files | Section 5 |

**[Note 2026-10-10, QP-1010 (b1) item 7.]** **Limits in the code that this table did not state** (`tools/c1nf_executor.py` at b17dc0b; recorded, none changed). Escalated sells at 1,010,000 lamports (2 x 505,000; :189), `max_attempts` 450 per tier and `max_days` 15 (:188) are now in the Priority and Backstops rows (Amendment 1 items A, F and G). Not stated before:
- `C1NF_END_MAX_MS` = 2026-10-24T00:30:00Z (:112), a **hard clamp**: a config `end_ms` may be earlier, never later; an extension is a code change.
- `PICK_WINDOW_START_MS` = 2026-10-10T00:00Z (:100): a decision before it is never acted on.
- `GAP_LOOKBACK_S` = 60 (:102): a `c1nf_gap` whose slot range overlaps [SD_slot - 60 s, SD_slot] refuses the pick.

**Tiers.** There is no 0.02 SOL tier. The `TIER` file names the C1-NF table below, not H5's (H5's T1 is 3 open, 40 a day, stops 0.40 and 0.60).

| Tier | Stake (SOL) | Max open | Attempts/day | Daily stop (SOL) | Total stop (SOL) |
| --- | --- | --- | --- | --- | --- |
| **T1 (the canary)** | **0.05** (code ceiling 0.10, lowered by config) | 2 | 30 | 0.20 | 0.30 ceiling; **0.175 at a 0.5 SOL wallet** (35% cap) |
| T1 at 0.10 (a step) | 0.10 | 2 | 30 | 0.20 | same ceiling; the 35% cap rises with the wallet. Needs profits and the owner's dated line (section 10) |
| T2 (inactive) | 0.30 | stated in the owner's line | stated in the owner's line | stated in the owner's line | stated in the owner's line; H5's T2 (`TIERS`, `tools/h5_executor.py:167-170`: 3 open, 40 a day, 1.20 and 1.80) is a starting proposal only |

- **A step to 0.10 changes the stop odds.** At the same 0.5 SOL wallet and total stop 0.175, a 0.10 stake stops 31% / 29% of runs before 50 fills if September held (option (a) below). The 35% cap gives a 0.244 stop only from a wallet of about 0.70 SOL (0.244 / 0.35) [inferred; quant-proof simulated the 0.5 SOL wallet only]. So the step is for when profits have lifted the wallet.
- **T2 needs the owner's dated line** (section 10). It is not fundable from the 0.5 SOL wallet: a 0.30 stake exceeds the 0.175 total stop, so the exposure test refuses every 0.30 buy unless realized profit is at least 0.125 SOL. A 0.30 step is reachable only after profits lift the wallet to about 0.86 SOL [inferred].
- **T2's price-impact check is H5's, not C1-NF's.** `T2_IMPACT_OK` (`tools/h5_executor.py:178`) rests on a replay model for H5 triggers (`/data/mal/hunt-1008/h5-work/IMPACT.md`). C1-NF's pools are older and deeper (the stage-1 real quote is at least 20 SOL; the discovery picks' median real quote is 142.65 SOL, VERIFY section 6 as EXP-025 section 8 cites it [exploration]). The check is not transferable (Q8).

**Funding (O-1): 0.5 SOL** into the second Helm-held wallet. **No further owner capital is planned; scale-ups come from profits.** The owner's total capital across all tests is 1.29 SOL: this wallet 0.5, H5 +0.5 at its T1 step.

**Stop options at a 0.5 SOL wallet: quant-proof's simulation (the owner chose (c)).** Relayed by the coordinator at 17:30Z; the drafter did not re-run it. **Scope:** 10,000 runs at seed 1 (seed 2 within 0.01), on the exploration 419-trade book (it reproduces +8.151% flat and +7.220% pressure), with the stop barrier at realized loss of (total stop - stake). Cells are P(a stop before 50 fills), flat / pressure. [exploration; not evidence of an edge]

| Option | Sept as is | Half edge | Zero edge | P(stop before 30 fills), zero edge, flat | Worst-case floor (SOL) |
| --- | --- | --- | --- | --- | --- |
| (a) stake 0.10, stop 0.175 | 0.31 / 0.29 | 0.43 / 0.40 | 0.63 / 0.60 | 0.53 | 0.119 |
| (b) stake 0.10, stop 0.244, 2 open | 0.14 / 0.12 | 0.24 / 0.21 | 0.42 / 0.39 | 0.31 | 0.050 |
| **(c) stake 0.05, stop 0.175 (chosen)** | **0.06 / 0.05** | **0.11 / 0.09** | **0.22 / 0.20** | **0.12** | **0.219** |
| (d) stake 0.10, 1 open, stop 0.244 | same as (b) | same as (b) | same as (b) | 0.31 | 0.153 |

- A day-block bootstrap gives similar numbers: (a) at "Sept as is" is 0.36 / 0.31.
- **The zero-edge column is the honest planning case.** The cap was chosen after the read, the cell was the best of about 24, and about 40 hunts have read September (EXP-025 section 9).
- The floors are the hard worst case (below). (b)'s floor is the 0.05 SOL wallet floor exactly, so it leaves no margin.
- This replaces the drafter's earlier back-of-envelope "0.93 chance of a deep loss in 50 trades" and the words "probably cut short". That estimate ignored wins offsetting losses and the barrier. It is withdrawn.
- **[Note 2026-10-10, QP-1010 (b1) item 2.]** The 'Sept as is', 'Half edge' and 'Zero edge' cells of this table are P(a stop **before 50 fills**) (the next column is before 30 fills), under a realized barrier at (total stop - stake), not the chance over the run. For option (c) over the run, at zero gross edge, under the executor's exposure test, P(total stop) is **0.57 / 0.55 by day 7 and 0.66 / 0.64 by `end_ms` for a 10-15 start** (flat / pressure; section 12.1). QP-1010's own rerun of this column for (c) under the executor's test gives 0.051 / 0.052, 0.119 / 0.124 and 0.297 / 0.307 (section 12.1, with the reasons the zero cell differs).

**Worst case and the wallet floor [inferred; arithmetic from the numbers above].**
1. **Hard worst case at the chosen stake, with the cap.** Total stop **0.175 SOL**, not relying on the exposure test (the convention of DEC-024 Amendment 3): 0.175 + 2 x 0.05 (two open positions go to zero) + 0.0020 (four sends at 505,000) + 0.0041 (two stranded ATA rents of 2,039,280) = **0.281 SOL lost, a floor of about 0.219 SOL**. The owner's option text says "~0.22 SOL".
2. **Against the guards and the wallet floor.** 0.219 is above the wallet floor of 0.05 and above the code's `balance_need` for a 0.05 buy (about 0.104) and DEC-024's old guard (0.07). The total stop has fired by then, so no further buy opens. The floor is coherent and the guards are not the binding constraint.
3. **With the exposure test kept.** At every buy, realized loss + open exposure + this stake stays below the total stop, so the loss at any moment is about 0.175 plus stranded rent and exit fees: **about 0.18 SOL, a floor of about 0.32 SOL**. Item 1 is the conservative bound.
4. **For comparison, at stake 0.10:** option (a) has a floor of 0.119; the owner's literal 0.30 total stop with no cap would lose 0.30 + 2 x 0.10 + 0.006 = about 0.506, more than the wallet, so the floor would be hit first. That is why the cap is applied. The largest total stop whose hard worst case clears the 0.05 floor at stake 0.10 is 0.5 - 0.20 - 0.0061 - 0.05 = 0.244 SOL, which is option (b).

**What the exposure test does at stake 0.05 [inferred].** With total stop 0.175: a first buy needs tier realized above -0.125 (0.175 - 0.05) and day realized above -0.15; a **second concurrent** buy needs tier realized above -0.075 and day realized above -0.10. So **2 open is reachable from the first send**, as the owner's option says. No single loss can trip the stop, because a loss is at most the stake plus fees (about 0.0505). It takes net realized losses of about 0.125, which is at least three near-total losses (or more, if wins intervene). **Picks skipped for the open cap are the clustered ones** (hot minutes), a selection effect. They are logged as skipped, and the fill-selection rule (section 7 rule 1) does not count them.

**What the canary can and cannot show [inferred arithmetic from EXP-025 section 6's numbers].**
- One send at 505,000 lamports is 1.01% of a 0.05 SOL stake. A round trip is **2.02%**, against **0.404%** in the deciding cell at 0.25 SOL: **1.616 percentage points more** per trade (a per-fill difference applied to per-attempt means; conservative, about 1.49 pp on the flat leg). The owner's option text says the fee "costs about 0.9 points per trade" against 0.10 SOL; Per filled round trip the difference is 1.01 pp (2 × 505,000 lamports against 0.10 and 0.05 SOL). Per attempt on the flat leg it is about 0.93 pp: a fill pays two sends and the 15% of failed attempts pay one, so 1.85 × 505,000 lamports. Including the 5,000-lamport base fee it is 0.94 pp. Quant-proof's +7.58% and +6.64% are flat-leg means per attempt at 505,000 plus the base fee, with no rent and the ATA closed. A stranded ATA rent of 2,039,280 lamports is 4.08% of the stake. (DEC-024 section 4 uses 1,513,840 lamports for H5's ATA. The two numbers differ; the canary uses whichever its executor actually pays. Q9.)
- The deciding cell's September means already include 0.404%. September's rent-inclusive means were flat +7.458% and pressure +6.640% [exploration, VERIFY section 5.2]. Minus 1.616 pp they are **+5.84% and +5.02%** at 0.05 SOL, **if** September's edge held and nothing else changed. That is arithmetic, not a forecast.
- The per-trade SD is about 0.34 to 0.37 of stake (EXP-025 section 3: 0.373 modelled, 0.342 implied by VERIFY's trade CI). The standard error of a mean is about **5.0 pp at 50 trades** and 3.6 pp at 100. An expected +5% against 5 pp is a z of about 1.0 to 1.2. **So it can show a large fault or a large gap, and a September-sized edge only loosely. It cannot tell a half-size edge (about +2%) from zero at 50 trades.**
- The fee burn alone over the first 50 filled trades: 50 x 1,010,000 lamports = **0.0505 SOL, 10% of the 0.5 SOL wallet and 29% of the 0.175 SOL total stop** (the fee in lamports does not depend on the stake). At 30 fills a day it is 0.0303 SOL a day.
- The paper twin and the live-versus-twin comparison are priced **at 0.05 SOL and the canary's fee**, not scaled from the 0.25 SOL cell. The shadow's outcome variants (no fee, 55k, 505k, 505k + rent; #503) do not state a stake in the PR body. The builder confirms and, if needed, adds a variant at the canary's stake. **[Note 2026-10-10, QP-1010 (b1) item 8.]** Stale: #503 has a tested 0.05 SOL `canary` twin that guards on the pick's state (its body at 9a80398; QP-1010 read 50eeaa1). #503 is still an open draft, not merged.

**What the stops do.** They are circuit breakers for a build or regime fault, not a P&L tool. A stop firing is not evidence about C1-NF.

## 7. Live-halt rules

Any of these halts new buys at once (`STOP`). Open positions exit on the timer unless the manager places `HALT`. A halt is never followed by a retune of the rule, the model, the cap or the threshold.

1. **Fill selection (from #504's design).** Every pick that reaches the executor is ledgered `pick_status` filled or unfilled. The monitor reads the shadow's `c1nf_outcome` for each, over the last 30 monitored picks with outcomes. If (mean outcome of the unfilled) - (mean outcome of the filled) exceeds **3 pp**, with at least **8 unfilled** and at least 1 filled, it latches `fill_selection_adverse`. After a clear, only outcomes that arrive later count. Book refusals (held, cooldown, duplicate, open cap) and refusals by a stop are not fill failures and are not monitored.
   - **Why it exists.** The edge sits in the right tail of hot entries. VERIFY section 6 [exploration]: if the best 10% of fills failed, the no-fail mean would fall to +1.855%; if the best 28.9%, to -5.116%.
   - **It is weaker than it reads (the builder's own flag, #504).** With a per-pick SD of about 34 pp and 8 unfilled against 22 filled, the standard error of the difference is about 14 pp, so a 3 pp line fires by chance in roughly 40% of evaluations once 8 are unfilled. Fewer than 8 unfilled of 30 cannot latch. **It is a fill-rate tripwire in practice.** Kept as designed (a false halt costs a restart; a missed one costs the measurement). Q3 asks quant-proof to size it.
   - It uses the shadow's outcomes, which are observed in real time under Appendix A.
2. **Divergence from the paper twin.** After at least **50 fills** (**50 is the manager's term**; DEC-024 uses 100; C1-NF has about 20 picks a day, and the stop may end the run first, section 6), the live-minus-twin per-trade CI90 upper bound is below **-1 pp** (live worse than its twin by more than 1 pp). The twin is the shadow's outcome for the same pick, **re-priced at the canary's stake, fee and landing slot** (section 6). A CAP-PICK pick is never traded and has no twin. Q4 asks quant-proof to size N and the line at C1-NF's variance.
3. **Landing latency.**
   - **Slow landing.** The rolling landing p50 (decision slot to landed slot, converted by the measured s-per-slot) above **1.9 s**, over the first 20 landed buys and then a rolling 20. 1.9 s is the pre-registered binding latency (EXP-025 section 7 item 9). The deciding latency is 1.3 s; the canary reports its p50 and p90 against it.
   - **Out-of-rule entry.** Any buy that lands more than 5 s after `SD_slot` (section 6).
   - The p50 line is a fidelity line, not an edge line. VERIFY's means at 3.0 s and 4.0 s are within about 0.2 pp of the 1.3 s mean without rent [exploration: flat +8.260 and +8.080 against +8.151]. Q5.
4. **A stuck position.** A position not closed by landing + 600 s: halt new buys and alert. Retries follow H5's ladder with no C1-NF override: a resend on each failure, up to `SELL_MAX_ATTEMPTS` = 10; then the position is abandoned and Helm runs the root sell-and-close (Amendment 1 item E, which replaced "Retries back off from 30 s to 10 min").
5. **Exit timing: report and alert, no halt.** H5 has a cliff at its exit; C1-NF does not. VERIFY's sell-lag legs at 2 s and 5 s sit within about 0.5 pp of the 0.55 s leg [exploration: flat +8.147 and +8.571 against +8.151]. The canary reports the exit-lag distribution and alerts when more than 10% of sells land later than landing + 305 s.
6. **Structure.** The A3 monitor's flags, and the other items, as follows. (Monitor code: `tools/pump_structure_monitor.py`, halt flags at lines 1365 to 1443, WATCH_RULES at line 147.)

   | Flag or item | Applies to C1-NF as | Why |
   | --- | --- | --- |
   | `pins_changed` | **Live halt** | A program redeploy can move the decoder, fee or V convention that the pricing and the guard rest on. The 2026-10-08T16:20Z redeploy is the example |
   | `program_changed` (a WARN in the monitor) | **Live halt** for the canary, as DEC-024 Amendment 2 item 4 | Same reason; the monitor does not halt on it, the canary does |
   | `synthetic_share_high` | **Recorded and alerted only; not a C1-NF halt** | The owner kept synthetic pools in C1-NF's universe (EXP-025 Amendment 2, section 9.3). The flag stays a halt for H5; for C1-NF the monitor's reading goes to the daily note and an alert above its 0.35 line, and nothing stops. The owner's decision, not a quant-proof ruling |
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

**[Note 2026-10-10, QP-1010 (b1) item 9.]** Rules 1, 3, 4, 5 and 7 are no longer only "#504's design". #530 implements them (`tools/c1nf_executor.py` at b17dc0b): `FILL_SEL_*` 3 pp with at least 8 unfilled of the last 30 with outcomes (:169-172); landing p50 above 1.9 s over the first 20 landed buys, then a rolling 20 (:164-166); `stuck_position` at landing + 600 s (:153); late sells above 10% of the last 20 landed, once at least 10 have landed (:157-159); the heartbeat at 150 s (:101). Rule 1 still needs #503's outcomes, and #503 is not merged.

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
- **The public wallet.** The wallet is public on chain, so anyone can compute its P&L. At 0.05 SOL against a stage-1 real quote of at least 20 SOL the canary is at most about 0.25% of a pool's quote (EXP-025 section 5.1 and section 12 state 0.1% for 0.02 SOL stakes; Appendix A item 4 extends the declaration to 0.05). That is consistent with the declaration for decisions in the windows. A trade on a decision outside them is covered by Appendix A item 2.
- **The shadow is not the read.** Its decisions will differ from the read's (different V source, a tip-tape ledger, a live feature state, a possibly different model). The Look reports may record disagreement; nothing is reconciled.
  - **[Note 2026-10-10T13:11:59Z, `date -u`: one more live difference.]** The live engine's creator features count only from the bootstrap anchor, not from tape start as in training. See "Note 2026-10-10T13:11:59Z" before Appendix A, which also holds the soak checklist.

**The gaps that need an EXP-025 amendment (Appendix A).** EXP-025 Amendment 2 (#529, synthetic pools; open draft at 12f5133) is a separate amendment. Appendix A is the next one. **[Note 2026-10-10, QP-1010 (b1) item 3.]** #529 merged at 2026-10-09T17:53:58Z. Appendix A is Amendment 3, open as #548, not merged.
1. **Decisions before 2026-10-10T00: not time-critical.** The shadow's code guard, **outcome records start at 2026-10-10T00, is binding** (section 11 item 8), not a fallback. With it no outcome of a pre-window decision exists, so section 4's seal is not touched. Appendix A item 1 (the O-6 default) is held in reserve for the day the guard is relaxed. #529 records that, at 2026-10-09T17:53:08Z, MiScusi job #468 found no `c1nf` unit, output directory or process on `mal-fast-0`.
2. Decisions at or after 2026-10-24T00, inside no window. The default `end_ms` makes this nearly moot; an extension would need it.
3. The **executor's** CAP-PICK skip. Section 5.3 binds the read and the shadow; the live executor is not named.
4. The canary's **actual stake**: section 5.1 says "0.02 SOL" (and at most 0.1% of a pool's quote) and section 12 says "0.02 SOL stakes". The canary trades 0.05 SOL, at most about 0.25% of the quote.
5. The observer list, the report disclosure text, the rule that an EXP-025 amendment dated after the first canary or shadow outcome is not called outcome-blind, and the bar on splitting outcomes by synthetic class (Amendment 2 item 5).

**Timing.** Appendix A **need not merge before 2026-10-10T00:00Z, provided the shadow's guard is binding. It must merge before the first send** (section 4). #529 must merge before 2026-10-10T00:00Z, on its own text. **[Note 2026-10-10, QP-1010 (b1) item 3.]** It did, at 2026-10-09T17:53:58Z. Appendix A (#548) is still open and must merge before the first send.

**Why this cannot change a read.** The rule, data, analysis, pass bar and alpha pair are fixed by EXP-025 sections 0, 3 and 7. Appendix A changes none of them. It edits no pinned line, patch, hash or window. Section 5.1 already covers in-window decisions.

## 9. Seals, pool overlap and synthetic pools

### 9.1 The CAP-PICK seal (EXP-022 section 9, EXP-025 section 5.3)

- From 2026-10-16T01 to the end of EXP-022's read, there is **no C1-NF live or paper trade on a CAP-PICK pick**, no C1-NF record joined to a pick, and no per-pool C1-NF P&L for a pick before each CAP-PICK look. A breach is recorded as compromised.
- **Mechanism.** The executor asks the boolean `pick_oracle(mint)` (PR #509, `claude/cap-pick-oracle`, open draft at 983b12c) for every mint in scope and skips any that answers True. It reads only the boolean, writes no CAP-PICK field into any C1-NF record, and joins none. **The oracle fails closed:** missing, stale for more than 60 s, erroring, non-boolean or undecided means no buy. Seal refusals are a count only, never recorded per mint. **[Note 2026-10-10, QP-1010 (b1) item 10.]** Stale: #509 merged at 2026-10-10T09:23:49Z (head 8b3ce7c) and is wired into the executor: the config's `pick_file` is read through `StaleCheckedPickOracle` (`tools/c1nf_executor.py:479`, :1310) with #509's 60 s staleness. **But at b17dc0b `tools/c1nf_executor.py` does not import:** `StaleCheckedPickOracle` subclasses `h5.JsonlPickOracle`, which `tools/h5_executor.py` no longer defines (`tools/cap_pick_oracle.py:370` has `PickOracle`), so `tools/test_c1nf_executor_dec026.py` errors at collection and two `tools/test_c1nf_ops.py` closure tests fail (run 2026-10-10 about 09:58Z with `/data/mal/venv`). That is a builder fix, not made here. Fixed on main by #552 (merged 2026-10-10T10:36:17Z): the executor reads #509's `PickOracle`. The shadow's wiring rides #503, which is not merged. The pick feed as built is DEC-024 Amendment 6 and EXP-022 Amendment 6 (#543, merged 2026-10-10T09:26:27Z), which this file did not cite.
- **Keying.** EXP-025 section 5.3 keys the exclusion on mints "whose canonical pool's first print is at or after 2026-10-16T01". #504 asked the oracle about **every** mint and refused any with no row in the picks file as undecided. That refuses more than the read excludes (an old mint decided after 10-16T01 is refused by the executor and counted by the read). It is fail-safe but not the same population. The rebuilt executor uses section 5.3's keying, or the difference is disclosed in each look's report. Builder item, section 11. **[Note 2026-10-10, QP-1010 (b1) item 11.]** Decided by the build: #530 asks the oracle about **every** mint (`tools/c1nf_executor.py:35`), wider than section 5.3. So the disclosure branch applies: **Appendix A item 3's last sentence is mandatory, and the Look 1 and Look 2 reports must each state the difference.** Quant-proof on Q13: a stricter executor is acceptable (fail-safe) if it is disclosed.
- **Dependency.** Until #509 is merged and wired in, the executor refuses every buy from 2026-10-16T01, and the shadow seals every pool. **[Note 2026-10-10, QP-1010 (b1) item 10.]** #509 is merged and wired into the executor. From 2026-10-16T01 the executor still refuses every buy without the seal inputs of section 11 items 26 and 27 (the exporter with `CAP_PICK_OUT` and the read-only bind, and `FINAL_WRITTEN`).

### 9.2 Pool overlap with H5: no claim is made

- **This DEC makes no overlap claim.** The scoping's "0 overlap" had no source in the repo or the hunt reports I could search (EXP-025, DEC-025, DEC-023, DEC-024, `ARTIFACTS/exp025/`, HANDOFF, JUDGE-4, the C1-NF VERIFY). The claim is dropped.
- **What the rules imply [inferred; not measured].**
  - The **universes overlap.** H5 trades PumpSwap non-mayhem WSOL pools in the V band in the first 300 s after `s0` (DEC-024 section 4). C1-NF's universe is every graduation with V0 in [17.5e9, 17.7e9] and a first print within [-5 s, +120 s] of `complete` (EXP-025 section 2.1). Same band, same first minutes.
  - The **positions do not overlap in time.** H5 exits at `s0` + 330 s, at most graduation + 120 s + 330 s = 450 s for a C1-NF-universe pool. C1-NF's first decision is at graduation + 600 s. So no pool is held by both wallets at once, unless an H5 exit is stuck (DEC-024 section 5 rule 5 lets an H5 position stay open to `s0` + 600 s, which is graduation + 720 s).
- **Consequences, small.**
  - A C1-NF trade comes after H5's window on the same pool, so it cannot change an H5 outcome on that pool.
  - The H5 wallet's sells land at about graduation + 325 to 450 s, inside the 300 s feature window `[graduation + 300, graduation + 600]` of C1-NF's first decisions on that pool (v5, new5, flows). At H5's 0.02 SOL (0.10 SOL at its T1 step) against a stage-1 v5 of at least 1 SOL that is at most 2% (10%) of v5. Both wallets' trades are ordinary tape rows and are not removed.
  - The two canaries are not independent evidence: the rules select overlapping hot tokens, so outcomes are probably positively correlated (EXP-025 section 1).
- **Optional (Q2):** a count-only pass on exploration tape, per day: pools in H5's universe, C1-NF picks, and their intersection, with no price, fill or P&L. Not a precondition.

### 9.3 Synthetic-migration pools: kept in (the owner's decision)

- **The decision.** The owner chose to keep synthetic-migration pools **in** C1-NF's universe: EXP-025 Amendment 2 (PR #529, `OWNER_SYNTHETIC_DECISION_EXP025`, answer "Keep them in (Recommended)"; open draft at 12f5133; it must merge before 2026-10-10T00:00Z). The coordinator relayed the same decision at 17:30Z. This DEC follows it. **[Note 2026-10-10, QP-1010 (b1) item 3.]** #529 merged at 2026-10-09T17:53:58Z (8c38ee6), before that instant.
- **What it means for the canary.**
  - The canary and the shadow trade **all universe pools, synthetic ones included**. The executor applies no refusal by class. The model's October training rows include them too (Amendment 2 item 1).
  - **`synthetic_share_high` is not a C1-NF halt.** It is recorded and alerted only (section 7 rule 6).
  - **The class is logged as a separate counts-only stream.** No canary or shadow record that carries an outcome also carries the class (Amendment 2 item 5).
  - **No observer may split C1-NF outcomes by synthetic class before the final look** (Amendment 2 item 5; "before Look 2 is read, or, if Look 2 does not run, before the final C1-NF report"). That bars the owner, Helm, the manager, builders, the watchdog and the daily check from printing, posting or noting canary or shadow results by class: no Discord line, daily-note row, Console entry or message. A breach is recorded and the read is reported compromised. After the final look a split is report-only.
- **A correction to an earlier draft.** DEC-026 at e570954 said a synthetic pool's first PumpSwap print "may come later [unmeasured]". It is measured, structure only. `/data/mal/hunt-1008/h5-work/synthetic-1009/REPORT.md` section 2 (H5's counts-only structure report, InitBoost WSOL pools): for synthetic pools with an `s0` found (36 of 44) the time from the migrate transaction to `s0` is **0 s at the median, 0 s at p90, 7 s at the maximum** (0 / 2 / 25 slots); non-synthetic 44 of 45, 0 / 0 / 10 s. The completing transaction lands 0 to 10 slots before the migrate transaction (median 3). So for the measured pools the first print is well inside the universe's [-5 s, +120 s] window of `complete` [inferred]. 35.0% of 160 classified graduations were synthetic (REPORT section 1). So synthetic pools are expected in C1-NF's universe in about that share.
- **What is not measured.** Eight of the 44 synthetic pools had no `s0` found, so whether they enter the universe is not shown. September's evidence had none (0 of 61 graduations before the 2026-10-08T16:20Z redeploy, H5's sample), and whether the exploration tape can classify September graduations at all is unknown. The model has never scored a synthetic pool outside its October training rows. The canary will trade pools the September evidence cannot vouch for, at 0.05 SOL. The owner decided this knowing that (the question text in Amendment 2: "zero in September's evidence").

## 10. Scale-up (above the 0.05 SOL canary)

The canary starts at 0.05 SOL (section 1). There is no 0.02 SOL step. A step above 0.05 (first to 0.10, the code ceiling) needs the manager's written ask and **all** of these:
- **Profits.** The owner's option text: "Can step up to 0.10 from profits." The wallet is above its starting 0.5 SOL by the realized profit of the canary, after fees.
- **The owner's dated line** (O-2, default applied): `OWNER_LADDER_CONFIRMED` below, with his words, stating the step's stake, open cap, stops and funding. Without it nothing above 0.05 is active. This follows DEC-025 section 4 and EXP-025 section 5.1: "Scale-up beyond canary size needs a passed read or a further explicit owner override."
- **Enough closed trades at the current stake:** about 50 [manager's term; the owner's ladder is 25 to 50 trades a step]. Counting fills.
- **Live execution is consistent with the sim:** the landing p50 is at most 1.9 s, over at least 20 landed buys; the fill-selection rule has not latched; and, after at least 50 fills, the live-minus-twin CI90 upper bound is not below -1 pp (section 7 rules 1 to 3).
- **The paper twin is not negative:** the shadow's mean per trade for the canary's picks, priced at the canary's stake and fee, is not below zero.
- **EXP-025 Look 1, if it has been read, is not negative:** its deciding-cell flat and pressure means are not below zero. **Plainly: Look 1 cannot be read before about 2026-10-17T03Z (EXP-025 section 3), so this condition cannot bind a step made before then.**
- **The canary's own realized result, after fees, is not negative:** its mean per closed trade is not below zero. **Weak.** If the rule worked exactly as September says, the expected mean at 0.05 SOL is about +5% (+5.8% flat, +5.0% pressure) against a standard error of about 5 pp at 50 trades; a half-size edge is about +2%. **At zero edge a "not negative" check passes about half the time** [quant-proof]. The check guards against a gross fault. It is not a test.
- **No live-halt rule (section 7) and no stop that ends the canary is unresolved,** including the A3 flags that apply.
- **The step is funded from profits.** No further owner capital is planned. The 35% cap is set from the wallet at the step's start. At 0.5 SOL no 0.30 stake clears the exposure test (section 6): a 0.30 step is reachable only after profits lift the wallet to about 0.86 SOL [inferred]. **A step above 0.10 also needs a C1-NF price-impact check** (Q8).

**These are necessary and not sufficient.** The twin is observed in real time (section 8), and the looks, if read, are read as written. **Neither clears the promotion gate.** CLAUDE.md says nothing goes live before a book clears the gate on both fail models. This DEC does not waive that for any size above the canary, and it records **no** override of the kind DEC-024 section 7 records for H5.

```
OWNER_LADDER_CONFIRMED: none. No step above 0.05 SOL is active. A dated line from the owner, with his words, goes here and nowhere else.
```

Size above step 2, or any step on a passed read, is governed by DEC-018, DEC-019, DEC-020, DEC-025 section 5 (at most 4 x 0.25 SOL open at a 1 SOL bankroll, after the owner's yes) and the owner.

**Mechanics of a step.** To 0.10 SOL: the reviewed live config no longer lowers the stake, which is a reviewed config change and a pinned reinstall by Helm (a config value is in the pinned tree), on the manager's written ask [the manager's reading; the TIER file alone does not change the stake]. To T2: Helm edits `/etc/mal-c1nf/TIER` as H5's runbook describes (no wind-down, no restart; the executor writes a `tier_change` ledger row; the watchdog posts it; the daily check alerts if the file and the executor disagree for 15 minutes). A step down is the same edit.

## 11. What must happen before the first live send

Status is as of 2026-10-09, about 17:00Z, **from the PR list and PR bodies, not from the hosts**. Refreshed at 17:50Z: #502, #503, #506 and #509 are unchanged (last updated 01:52Z to 05:03Z), #504 is still closed, main is at 325be00 (DEC-024 Amendments 2 and 3 and the T1 top-up addendum, DEC-027, H5 synthetic-class work), which this branch has merged, and #529 (EXP-025 Amendment 2) is an open draft at 12f5133. Nothing was checked on `mal-fast-0` or `mal-core-0`.

**[Note 2026-10-10, QP-1010 (b1) item 13.]** The status line above is stale throughout. Refreshed from `gh` at 2026-10-10T09:54Z, main b17dc0b, no host contacted: #529 merged 2026-10-09T17:53:58Z; #522 (this DEC) 18:00:11Z; #530 2026-10-10T08:59:53Z; #531 09:00:00Z; #502 09:23:36Z; #506 09:23:43Z; #509 09:23:49Z; #543 09:26:27Z; #544 (Amendment 1) 09:36:10Z; #552 (oracle import fix) 10:36:17Z; #550 10:37:03Z. Open: #503 (draft, 9a80398), #548 (Appendix A as EXP-025 Amendment 3). Per-item notes are in the Status column; items 24 to 28 follow the table.

| # | Item | Status |
| --- | --- | --- |
| 1 | **EXP-025 Amendment 2 (#529)** merged before 2026-10-10T00:00Z, and **Appendix A** merged as the next EXP-025 amendment (section 8) | #529: open draft. Appendix A: not started, draft text below. It is not clock-bound (the shadow's guard is binding) but must merge before the first send **[Note 2026-10-10, QP-1010 (b1) item 3.]** #529 merged 2026-10-09T17:53:58Z. Appendix A: open as #548 (EXP-025 Amendment 3), not merged; still due before the first send. |
| 2 | **The owner's answers** to the choices of section 3 | **Answered** 2026-10-09 at 17:00Z and 17:30Z (O-1, O-3 as 0.05 SOL, O-4, O-5, O-7, O-8). O-2 and O-6 are defaults he was told, applied unless he objects. Relayed by the coordinator; the notebook entries were not opened by the drafter |
| 3 | **Quant-proof OK on this DEC's final head**, and the stop table (section 12) | Not requested. Table not computed **[Note 2026-10-10, QP-1010 (b1) item 14.]** The table is computed (QP-1010 part (b2), copied verbatim as section 12.1). Quant-proof has **not** given an OK on a final head. QP-1010 withholds it while the stale statements stand, while the stop-odds sentences lack the full-run zero-edge number, and while section 11 omits the code preconditions; these notes are the text fix for those three. An OK, if given, is recorded here with its head and instant. |
| 4 | **Synthetic pools:** kept in (O-8); no executor refusal by class; no class-split of outcomes before the final look | Decided by the owner (EXP-025 Amendment 2). The enforcement is builder work: the class as a separate counts-only stream, and a daily check and watchdog that print nothing by class |
| 5 | **Overlap with H5:** no claim is made (section 9.2) | Resolved by dropping the claim. A count-only pass is optional (Q2) |
| 6 | **Ledger** #502 (`claude/c1nf-ledger`) merged, with a review | OPEN draft at 53da60f. Its body reports 21 tests and byte-equal parity with the pinned ledger on 2026-09-20. No review recorded. Not run on fast-0; `duckdb==1.5.6` not checked there. The nightly rollup is not scheduled **[Note 2026-10-10, QP-1010 (b1) item 15.]** Merged 2026-10-10T09:23:36Z (head 68f05a4). Still not shown: the nightly rollup scheduled (Amendment 1 D2 sets the job chain; not run) and `duckdb` on fast-0. |
| 7 | **Features** #506 (`claude/c1nf-features`) merged, with a review | OPEN draft at 3ae9915. Exact mode bit-equal on two days; the **live mode drifts** (state features: median relative difference 2e-7 to 3e-5, p99 5e-4 to 1.7e-2, maximum 1.9, from the fee-model estimate; "the main open item"; no flag flipped on the two days tried). No review recorded **[Note 2026-10-10, QP-1010 (b1) item 16.]** Merged 2026-10-10T09:23:43Z (head 8b74dbc). Live-mode drift is still the open item; parity P4 (section 11.1) measures it. |
| 8 | **Shadow** #503 (`claude/c1nf-shadow`) merged, with a review | OPEN draft at 2a96ce0, "not for merge until the missing items are closed": no pinned model, no parity, features not on main, no live run. 72 tests. **Two changes this DEC needs:** the pick record must carry the decision-time `q_lamports` and `base_reserve` (its `PICK_FIELDS` do not; section 6, buy guard), and **outcome records start at 2026-10-10T00 as a binding code guard**, not a fallback (section 8) **[Note 2026-10-10, QP-1010 (b1) item 17.]** Stale. #503 is open at 9a80398, still a draft (QP-1010 read 50eeaa1). `PICK_FIELDS` carry `q_lamports`, `base_reserve`, `v_lamports` and `state_slot`; the binding `OUTCOME_START_MS` guard is implemented; pre-window and replay picks go to `c1nf-prewindow-picks-*`; 185 tests at 9a80398 per its body (172 at QP-1010's read). Still missing: a pinned model, parity, and a live run. |
| 9 | **Executor** (`c1nf` profile) merged, with a review and a **security review** (it holds a key) | **#504 is CLOSED, not merged**, at 2026-10-09T05:33:55Z, the same minute #484 merged; its base branch was `claude/h5-executor` (closed by that merge, inferred; no comment says so). The branch `claude/c1nf-executor` is still on origin at a98ff07, built on the pre-merge H5 head 9c5618b. It must be rebased onto main and reworked for the merged tier mechanism. Its limits (3 open, 40 a day, total 0.15) differ from section 6. Other gaps against this DEC: the buy guard fail-closed, the oracle keying (section 9.1), `end_ms`, priority per O-3, the file paths of section 5. No review of it is recorded **[Note 2026-10-10, QP-1010 (b1) item 18.]** Stale. #530 merged 2026-10-10T08:59:53Z (head 32265af): T1 is a 0.10 ceiling, 2 open, 30 a day, 0.20 / 0.30; the live config sets 0.05; the buy guard fails closed; `end_ms` is clamped; 505,000 per send; the paths follow section 5. The `reviewer` pass is recorded in Amendment 1 ("Required edits: none"). The **security review** is not recorded (none found on #530 at this note). |
| 10 | **Daily check and watchdog** for `c1nf` | Not written (H5's are `scripts/mal-fast/h5-daily-check.py` and the `h5-watch` units) **[Note 2026-10-10, QP-1010 (b1) item 19.]** Written and merged in #531: `scripts/mal-fast/c1nf-daily-check.py`, `scripts/mal-fast/c1nf-watch.py`, `mal-c1nf-watch.{service,timer}`. Their install and the watchdog test are item 21. |
| 11 | **Pinned installer and runbook** `docs/runbooks/c1nf-executor.md`, modelled on `docs/runbooks/h5-executor.md` | Not written **[Note 2026-10-10, QP-1010 (b1) item 20.]** Written and merged in #531: `docs/runbooks/c1nf-executor.md`, `scripts/mal-fast/install-c1nf-executor-pinned.sh`, `scripts/mal-fast/make-c1nf-manifest.sh`. The runbook's "DEC-026 ... Amendment 1 item C" now resolves (#544). |
| 12 | **Pinned model file** and its sha256 in a dated line | **2026-10-10: pinned** (branch `claude/c1nf-model-pin`, a separate canary model per Q7). File `ARTIFACTS/c1nf_model/c1nf_model_exp36.txt`, sha256 `faf8a01f5fb5019a3c26affc7de49f47270e7b6717eea34767bfcdc759399478` (LightGBM 4.7.0 text, 400 trees, 107 columns in `tools/c1nf_features.FEATURE_NAMES` order, `from_day` 2026-09-26). It is in `C1NF_MODEL_SHA256` (`tools/c1nf_executor.py`); `manifest.json` beside it is #503's `--model-manifest` format. Built by `tools/c1nf_model_pin.py` with the pinned `mlcommon.py` and `rule.json` on every stage-1 row of the 36 exploration days (692,113 rows, no October row, no October label); two training runs gave byte-equal files [measured]. **Recipe parity [measured]:** the same code rebuilds VERIFY's walk-forward model for 2026-09-25 and, loaded from its saved text file, scores that day's 3,958 stage-1 rows exactly as VERIFY's `v/preds.npz` (max abs difference 0.0; 244 of 244 selections at > 0.02) and as the pinned `16_confirm.py` book (65 rows, max abs difference 0.0). **How it differs from the read's model (Q7):** it is trained on VERIFY's `disc.npz`/`conf.npz`, built on the non-deterministic ledger, not on P2's deterministic rebuild (not run at this date; VERIFY reports a largest prediction difference of 0.0049 on C1's trades between its rebuild and C1's run); and it never retrains, while the read's recipe adds October rows before each day's cutoff after the FINAL. A P2 model, if wanted for the canary, is a new pin by a new dated line **[Note 2026-10-10, QP-1010 (b1) item 21.]** QP-1010 read the pin as empty at 528f0ca. It is now the code constant `C1NF_MODEL_SHA256`, set by #550 (merged 2026-10-10T10:37:03Z) to the sha256 above. Parity's P2 models are not built as far as main shows. |
| 13 | **Parity task 3:** the shadow's picks against VERIFY's 419 | Not done (HANDOFF; #503 body). The bar is set by quant-proof **before** the run. Reference: VERIFY reproduced C1's frozen primary at 99.25% of its trades, Jaccard 98.53%, on a non-deterministic ledger (EXP-025 section 9). #506 shows the live mode is not bit-equal, so the bar must say what a live-mode miss is allowed to be (Q7) **[Note 2026-10-10, QP-1010 (b1) item 22.]** **The bar is set**: quant-proof's QP-1010 part (a), copied verbatim as section 11.1, before any parity run. It does not move after a run. |
| 14 | **md5 decision-equivalence replay proof** (CLAUDE.md: every runner change carries one before deploy) | Not done. Three lists, md5 on each pair, on exploration day **2026-09-20** (EXP-025's E0 day; 34 C1-NF trades in VERIFY's September book): (a) the shadow's picks against the frozen scorer's, in the mode where they must be equal; (b) a shadow restarted mid-day (the 26 h bootstrap) against an uninterrupted run; (c) the executor's accepted and skipped intents, with reasons, against the pick list. Quant-proof reviews the replay, as for H5 (DEC-024 Amendment 1 item 3) |
| 15 | **Keyless shadow soak** of at least 24 h on live tip-tape triggers, 0 build errors, on the reviewed head, as a MiScusi job on fast-0 | Not started. A shorter soak is a dated owner decision, as DEC-024 Amendment 1 was; it is not assumed |
| 16 | **Keyless executor dry run** on the real feed: at least 5 complete simulated round trips with 0 simulate errors | Not started |
| 17 | **Reviews:** `reviewer` on each PR; the security review of item 9; quant-proof on item 14 | None recorded |
| 18 | **Memory and disk on fast-0:** `systemctl show user-1002.slice -p MemoryCurrent` before each start; the shadow's bootstrap, the rollup (reserve about 8 GB, #502) and H5's shadow and dry runs fit together; one heavy job at a time | Not measured. The 2026-09-29 OOM and reboot is the reason |
| 19 | **A3 monitor:** no flag that section 7 rule 6 makes a halt shows a halt at the time `LIVE_OK` is created | At 07:11Z `pins_changed` was on (the 10-08 redeploy). #517 (re-pin) is merged. DEC-024 Amendment 2 item 3 records the H5 install verified at 16:52Z. The official A3 run (job #449) is set for 19:23Z and was still ahead at 17:00Z. Not known now **[Note 2026-10-10, QP-1010 (b1) item 23.]** Time-stale. The A3 state is re-read at the instant `LIVE_OK` is created. |
| 20 | **CAP-PICK pick oracle** #509 reviewed and wired into the shadow and the executor | OPEN draft at 983b12c. Needed from 2026-10-16T01 only. Before it, the executor trades; from it, with no oracle, it refuses every buy **[Note 2026-10-10, QP-1010 (b1) item 24.]** Stale: #509 merged 2026-10-10T09:23:49Z and is wired into the executor (section 9.1). The shadow's wiring rides #503 (open). |
| 21 | **Helm:** creates the second wallet and gives the public address; pinned root-owned install with the manifest and sha256 check; auditd watch; the keyless dry run; the watchdog test message seen by the manager | Not done. The wallet does not exist |
| 22 | **Funding:** 0.5 SOL (O-1), finalized, with the transaction recorded | Decided by the owner. Not sent as far as the drafter knows. The wallet does not exist yet |
| 23 | **Limits as code constants with tests:** stops fire, config only lowers them (the stake from the 0.10 ceiling to 0.05), a restart cannot reset them, `TIER` missing or invalid means T1, and **the exposure test is kept at stake 0.05, total stop 0.175 and 2 open** (section 6) | Not done (the executor is not rebuilt) **[Note 2026-10-10, QP-1010 (b1) item 25.]** Stale: #530 claims it (`tools/test_c1nf_executor_dec026.py`). Quant-proof has not verified a test at exactly stake 0.05, stop 0.175 and 2 open. |

`LIVE_OK` and the go to Helm are separate acts, made by Helm and the manager after items 1 to 23.

**[Note 2026-10-10, QP-1010 (b1) item 12.]** **Preconditions the code now has, missing from the table above.** Items 24, 25 and 28 join items 1 to 23 as preconditions of the first send. Items 26 and 27 are preconditions of trading from 2026-10-16T01Z, not of the first send (item 26 cannot exist before 2026-10-16T02:00Z). **Without the seal inputs the canary pauses at 2026-10-16T01Z.**

| # | Item | Status |
| --- | --- | --- |
| 24 | `C1NF_WALLET_PUBKEY` pinned by a reviewed PR (`tools/c1nf_executor.py:109`; `None` means `wallet_unpinned`, and live refuses to start) | `None` at b17dc0b. The wallet does not exist (item 21) |
| 25 | `C1NF_MODEL_SHA256` pinned by a reviewed PR (`tools/c1nf_executor.py:112`; empty means `model_unpinned`) | Pinned by #550 (merged 2026-10-10T10:37:03Z): `faf8a01f5fb5019a3c26affc7de49f47270e7b6717eea34767bfcdc759399478` (item 12) |
| 26 | `/var/lib/mal-live/c1nf/FINAL_WRITTEN`, created by the manager, never before 2026-10-16T02:00Z (Amendment 1 item C) | Not due yet. Without it every buy in the seal window is refused |
| 27 | The CAP-PICK exporter job with an explicit `CAP_PICK_OUT`, and Helm's read-only bind `20-cap-pick.conf` (Amendment 1 item B) | Not recorded as running or installed. Due before 2026-10-16T01Z |
| 28 | The daily-check sudoers file, `/etc/sudoers.d/mal-c1nf-check` (runbook Step 9) | Not recorded as installed |

### 11.1 Item 13's pre-set bar: parity task 3 (quant-proof, QP-1010 part (a), copied verbatim 2026-10-10)

Copied from `/data/mal/hunt-1008/c1nf-verify/QP-1010.md` (research-0; sha256 `e25d99101e12f875689a7916ccc56c9fff89de1ff6e90a3e947fc1f2386f7a1b` at the copy), part (a), written by quant-proof on 2026-10-10 at about 09:50Z against main 528f0ca. The words are verbatim; its `###` headings are one level deeper here. It was set before any parity run. Paths such as `v/v3_report.py` are relative to that directory.

**This bar is set before any parity run. It does not move after a run.** A FAIL blocks the first send. The fix is a code change on a new head and a full new run. A re-run on the same head is allowed only for an infrastructure failure that produced no comparison output, declared in a PR comment before any output is read. The first complete run's result is reported, not replaced.

**What it tests.** The shadow (#503) reproduces the frozen C1-NF selection on September exploration tape: stage 1, the 107 features (#506), the deterministic ledger (#502), the model at threshold 0.02, the cap `h_top1 <= 0.5` with NaN dropped before the book, and the one-position-per-mint book with 60 s re-entry. It does not test the canary's live model. That model is trained on all 36 exploration days, so it is in-sample on these days. DEC-026 item 14(a) covers it with an md5 on 09-20. Parity is not an edge test, and nothing in it may be reported as one.

#### P0. Pins, recorded before the run

A PR comment on #503 (or a file committed before the run) lists the sha256 of each of these:
- the shadow head;
- `tools/c1nf_features.py` and `tools/c1nf_wallet_ledger.py` at main;
- every ledger snapshot manifest;
- the model manifest and each model file;
- `tokens.parquet`;
- the tape hour list;
- the two reference exports below.

#### P1. References

- **R419.** VERIFY's rebuilt spec-order C1-NF book: 419 rows at 1.3 s, END bound, threshold 0.02, cap before the book, re-entry at exit + 60 s. It is exported from this directory (`v/v3_report.py`'s book on `v/sim.parquet` with `v/preds.npz` and `ml/conf.npz`) as `mint, decision_T_ms, SD_slot, pred, h_top1`. The matching steps (P3 to P5) never open a P&L column. VERIFY's per-row P&L is used only in P5's selection check.
- **Rdet.** The EXP-025 P2 deterministic batch rebuild: the pinned scripts with `ledger/01_wallet_daily_det.py`, the same walk-forward and the same cap. It is exported with the same columns as R419. If P2 is not built, parity cannot run (item 13 stays open).

#### P2. Replay inputs

| Input | Value |
| --- | --- |
| Tape | `/data/mal/audit-1008/tape`, read-only. Two replays, each from its segment start: `--replay-from 2026-09-03T12 --replay-hours 288` (S2) and `--replay-from 2026-09-18T23 --replay-hours 152` (S3). Decisions on the 21 confirmation days. The shadow's replay guard stays on (exploration hours only, forbidden tokens refused) |
| V | `v0_lamports` from `/data/mal/hunt-shared/tokens.parquet` (the shadow's replay V and the batch's map) |
| Wallet ledger | #502's deterministic as-of snapshots `asof-D` (exploration adapter), one for each decision day D, built from tape days strictly before D |
| Model | `--model-manifest` with **21 files, one per confirmation day D**. Each is RULE.md's walk-forward model: trained from scratch on every stage-1 row with t < D 00:00Z - 3,600 s, frozen LightGBM params, seed 1, `num_threads` 3, on P2's deterministic disc/conf rows. **Not** the canary's live model |
| Cap and book | `ARTIFACTS/exp025/c1nf_cap.py` semantics; re-entry at the estimated exit + 60 s |
| CAP-PICK oracle | Absent. There is no seal window in September. The heartbeat's `withheld` counter must be 0 |
| Runs | **Run A:** state mode `exact` (the batch's state, the PRE-trade reserves of the next print, as #506's harness defines it). **Run B:** state mode `live` (prints with slot < SD only, the post-trade state from the fee model), the mode the canary runs. If the shadow has no exact switch, the builder adds a replay-only flag with a test before the run |
| Execution | MiScusi jobs, one heavy job at a time, at most 2 workers, memory-capped (`systemd-run --user --scope -p MemoryMax=24G`), with MemAvailable and the user slice checked before each |

#### Matching rule (all comparisons)

- **Exact match:** same `mint` and the same `decision_T_ms`. The grid is whole UTC minutes, and both sides define SD as the first tape slot whose block_time >= T. **`SD_slot` must be equal on every exact match.** Zero tolerance: it is the same tape, and one disagreement is a clock fault and a FAIL.
- **Near match:** same `mint` and |ΔT| = 60 s (one grid step). Matching is one-to-one, exact first, then nearest. Near matches are allowed only in P4 and P5, and only within the caps below.
- **Jaccard** = matched pairs / (|A| + |B| - matched pairs). **Share** = matched pairs / |reference|.

#### P3. Deterministic subset: md5 (run A against Rdet, all 21 days)

- **The list.** L = lines `mint,decision_T_ms,SD_slot` (decimal), sorted, `\n`-terminated. It is computed per day and overall.
- **The bar.** `md5(L_runA) == md5(L_Rdet)` on every day and overall. This is item 14(a) on all 21 days, not only 09-20.
- **Boundary ties.** A row whose `|pred - 0.02| < 1e-6` or `|h_top1 - 0.5| < 1e-9` on either side (float32 and float64 casts) is a tie. Ties, and later rows of the same mint on the same day that the tie's book entry blocks or frees, are removed from both lists before the md5. The removed rows are listed with their values. **At most 2 ties in total**; more is a FAIL.
- **On every matched row:**
  - `|Δpred| <= 1e-6`;
  - `h_top1` equal to 1e-9;
  - stage-1 flags identical;
  - the 107 float32 features bit-equal on at least 99.9% of matched rows, and no difference larger than 1 float32 ulp.

#### P4. Live-mode drift (run B against run A, same inputs)

- Share of run A's picks matched (exact or near) **>= 98.0%**.
- Jaccard **>= 97.0%**.
- Near matches **<= 1.0%** of run A's picks.
- Max |Δpred| and the per-feature drift (p50, p99, max) are reported.

This is the only place a "live-mode miss" is allowed (Q7). #506 measured live state drift at a p99 relative difference of 5e-4 to 1.7e-2 with no flag flip on two days. A pick-set difference above about 3% means the canary trades a different book.

#### P5. The headline comparison (run B against R419)

**Share of R419 matched >= 96.0% (at least 403 of 419), Jaccard >= 93.0%, and at most 8 near matches.**

- **Why these numbers.** On this subset, VERIFY's own rebuild against C1 shared 411 rows of C1's 422 and the rebuild's 419. That is a share of 97.4% and a Jaccard of 411 / 430 = 95.6%. On the full book it was 99.25% and 98.53%. Both pairs ran the same pinned pipeline, and the ledger was non-deterministic. The bar leaves about 1.4 pp of share and 2.6 pp of Jaccard below that subset reference. That room is for the ledger change (non-deterministic to deterministic) plus the live state.
- **Every unmatched row, on both sides, gets exactly one class, by a mechanical test:**
  - **M1, ledger:** the row is in Rdet but not R419, or the reverse (VERIFY's non-determinism defect);
  - **M2, live drift:** the row is in run A but not run B, or the reverse;
  - **M3, boundary tie:** as defined in P3;
  - **M4, book cascade:** same mint, an earlier row of that mint differs, and this row's T is within that row's exit + 60 s;
  - **M5, `no_decision_state`:** #503's malformed last print before SD;
  - **M6, universe:** the mint's canonical pool or its universe membership differs from VERIFY's `work/universe.parquet`.
- **Class limits.** **M5 + M6 <= 3 rows. Unclassified rows <= 2.** More of either is a FAIL.
- **Selection check.** This uses September exploration P&L, from VERIFY's own `v2_sim` column at 1.3 s, 505,000 lamports and no rent.
  - **The bar:** the flat mean of the matched R419 rows must be within **±1.0 pp** of R419's +8.151%. Outside it is a FAIL: the misses sit in the winners or in the losers, so the canary trades a different population.
  - **Why ±1.0 pp:** with about 16 misses, a 1 pp shift needs the missed rows to average about 25 pp away from the rest. That is about 3 standard errors of a 16-row mean at an SD of 34 pp.
  - **Report only:** the count of shadow-only rows, and their `v2_sim` P&L where VERIFY has a row for them.

#### P6. Pick record (run B)

- 100% of picks carry `q_lamports`, `base_reserve`, `v_lamports` and `state_slot`, and pass `validate_pick`. Anything less is a FAIL.
- Report only: the gap between the pick's decision state and the batch's spot (p50 and p99), and the share of picks where the 1.15x guard decision would flip. If that share is above 1%, it goes to quant-proof before the send.

#### FAIL, in addition to the bars above

- Any read outside exploration hours, or any October hour.
- Any forbidden path.
- A model or input sha256 that differs from P0.
- An outcome or P&L file opened in P3 or P4.
- A run on an unpinned head.
- Any edit to this bar after the first output line is read.

**PASS** = P3, P4, P5 and P6 all pass, in one run on the reviewed head. The result is a parity statement only.

## 12. Stop-probability table: partly computed by quant-proof

**[Note 2026-10-10, QP-1010 (b1) items 14 and 26.]** **Computed.** Quant-proof's full table is section 12.1 below, copied verbatim. "Partly computed" and the "to be computed" stop cells below are stale. **Still not computed:** the best-10% and best-28.9% adverse-fill rows; P(each ladder check passes at 50); P(the fill-selection rule latches with no selection); Q4's divergence N. The text below is the 2026-10-09 request, kept.

Quant-proof computed one column: P(a stop before 50 fills), in section 6's option table (chosen option (c): 0.06 / 0.05 flat / pressure if September holds, 0.11 / 0.09 at half the edge, 0.22 / 0.20 at zero edge). The rest is not computed here. The H5 table (DEC-024 Amendment 1 item 4) is the form. **Inputs quant-proof needs, with the sources in the repo:**
- **Limits:** stake 0.05 SOL (code ceiling 0.10); daily stop 0.20; total stop 0.30 clipped to 35% of the funded wallet (**0.175 at 0.5 SOL**); at most 30 attempts a day; at most 2 open; the run from the first send to `end_ms` 2026-10-24T00:30Z.
- **Stop rule:** the executor's exposure test (section 6), not realized-only. With it a first buy needs tier realized above -0.125, and a second concurrent buy needs tier realized above -0.075. Quant-proof's section 6 simulation used the barrier realized loss of (total stop - stake). The remaining outputs below should use the executor's actual test, and a realized-only variant.
- **Pick rate:** about 20 a day in September (419 trades over 21 days [exploration]); EXP-025 section 3 also uses 12 a day and 20/12 with the oracle exclusion. October is unmeasured. The share of picks the executor lets through (staleness, guard inputs, the open cap, clusters) is unknown.
- **Per-trade distribution:** EXP-025 section 3's four-part mixture calibrated to VERIFY (win rate 66.8%; 22 of 419 lose at least 90%; median +10.17%; p95 +63.17%; no-fail mean +9.625%; top-3 average about +165%; top-10 about +122%; p1 -99.29%, p5 -97.81%), or the 419 September trades drawn with replacement. Both are exploration.
- **Fail legs:** flat 15%, and pressure with intercept refit to a mean failure rate of 0.289. Plus the **adverse-fill stress**: the no-fail mean if the best 10% of fills fail (+1.855%) and the best 28.9% (-5.116%) [VERIFY section 6; not in the power table].
- **Fixed-cost term at 0.05 SOL:** two sends at 505,000 are 2.02%, of which 0.404% is already inside the 0.25 SOL means, so **-1.616 pp per fill**. One send (1.01%) for each guard-reverted attempt. Stranded ATA rent (4.08% of stake) for a stuck position. The alternative priority (55,000: round trip 0.22%) only if the owner reopens O-3.
- **Day effect:** SD 0.03 of stake as in EXP-025 section 3's day-effect row.
- **Not modelled in H5's table and needed here:** the 2-open cap with clustered picks; the pressure leg; the ladder's step checks (section 10); live fills worse than paper; stuck positions.
- **Outputs wanted (beyond the computed column):** total P&L p5 / p50 / p95; **P(first-50 P&L > 0)**; **P(each ladder check passes at 50)** per scenario (at zero edge a "not negative" check passes about half the time, quant-proof); the distribution of trades before a stop fires; and P(the fill-selection rule latches) under **no** selection effect, against #504's roughly 40% claim.
- 10,000 simulations, seed 1, reproduced by a second run, as for H5.

| Assumed edge per trade (gross, 0.25 SOL cell) | Net of the 0.05 SOL fixed-cost term [inferred: edge - 1.616 pp] | P(stop before 50 fills), option (c), flat / pressure [quant-proof] | Total P&L p5 / p50 / p95 (SOL) | P(first-50 P&L > 0) | P(ladder check passes at 50) |
| --- | --- | --- | --- | --- | --- |
| September holds: flat +7.458%, pressure +6.640% | +5.84% / +5.02% | 0.06 / 0.05 | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof |
| Half of it | +2.11% / +1.70% | 0.11 / 0.09 | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof |
| A quarter | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof |
| Zero | -1.62% | 0.22 / 0.20 | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof |
| -3% | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof |
| Best 10% of fills fail | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof |
| Best 28.9% of fills fail | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof | to be computed by quant-proof |

A stop firing is not evidence about C1-NF (section 6).

### 12.1 Quant-proof's stop-probability table (QP-1010 part (b2), copied verbatim 2026-10-10)

Copied from QP-1010.md part (b2) (sha256 in section 11.1). Paths `qp1010/...` are relative to `/data/mal/hunt-1008/c1nf-verify/` on research-0. Exploration; not evidence of an edge. QP's closing paragraph ("Quant-proof position on items 3 and 13" through "not the chance over the whole run.") is not copied; section 11 item 3's note records it. **The zero-edge full-run line: 0.57 / 0.55 by day 7, 0.66 / 0.64 by `end_ms` for a 10-15 start.**

**Model.** `qp1010/stop_sim.py` (sha256 `2aa34760de85f330ea4a665ee7598d71066030837fc7a3aef67ef209ca6f983c`).

- **Runs.** 10,000 runs, seed 1, reproduced at seed 2 (every P cell within 0.023, the expected Monte Carlo spread for a max over 224 cells; SE of a difference is about 0.007).
- **Limits.** Stake 0.05; total stop min(0.30, 0.35 x 0.5) = 0.175; daily stop 0.20; at most 30 attempts a UTC day (guard reverts count); 2 open; hold landing + 301.85 s.
- **Picks.** NegBin per day, mean 20, dispersion fitted to September's 21 daily counts (r = 4.36), uniform within the day.
- **Per-trade returns.** EXP-025 section 3's four-part mixture (`exp025_power.Model`) plus a day effect N(0, 0.03 of stake).
- **Edge.** The positive parts are scaled so that the **no-fail, no-rent gross mean at the 0.25 SOL cell is k x 9.625%**.
- **Canary fill.** max(r + day effect, -1) - 1.636 pp. The 1.636 pp is two sends of 505,000 + 5,000 at 0.05 SOL, minus the 0.404% already in the cell.
- **Rent and fails.** ATA rent is refunded (the executor closes the ATA). A failed attempt costs one send.
- **Legs.** Flat p = 0.15; pressure p = 0.2407 (EXP-025's mean-matching effective rate). Failure is independent of return, so correlated fill failure is **not** modelled.

**Stop definitions (the executor's test).** A buy is refused if (realized - (open spend + this stake)) <= -stop, as in `_budget_stop`.
- **Total stop:** tier realized <= -0.125 with nothing open. No buy can ever open again.
- **Daily stop:** day realized <= -0.15 with nothing open. No buy until 00:00Z.
- **"By end_ms":** the run lasts from the first send to 2026-10-24T00:30Z. Start 10-17T00Z is 7 days + 30 min, 10-16 is 8 days + 30 min, 10-15 is 9 days + 30 min.

**Primary: the executor's exposure test (h5_executor._budget_stop as #530 inherits it).** Cells are flat / pressure. 10,000 runs, seed 1.

| Edge (no-fail gross mean at the 0.25 SOL cell) | Canary mean per attempt, flat / pressure | P(total stop) by day 7 | P(total stop) by end_ms, start 10-17 | start 10-16 | start 10-15 | P(daily stop) by day 7 | P(daily stop) by end_ms, start 10-17 | start 10-16 | start 10-15 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| September holds | +6.65% / +5.83% | 0.061 / 0.062 | 0.061 / 0.062 | 0.061 / 0.063 | 0.062 / 0.063 | 0.040 / 0.035 | 0.040 / 0.035 | 0.046 / 0.040 | 0.053 / 0.046 |
| Half | +2.56% / +2.18% | 0.187 / 0.183 | 0.187 / 0.183 | 0.197 / 0.194 | 0.205 / 0.203 | 0.055 / 0.043 | 0.055 / 0.043 | 0.063 / 0.050 | 0.073 / 0.058 |
| Quarter | +0.51% / +0.35% | 0.337 / 0.332 | 0.337 / 0.332 | 0.363 / 0.361 | 0.386 / 0.386 | 0.055 / 0.040 | 0.055 / 0.040 | 0.062 / 0.045 | 0.071 / 0.051 |
| Zero | -1.53% / -1.48% | 0.574 / 0.553 | 0.575 / 0.554 | 0.622 / 0.600 | 0.660 / 0.642 | 0.039 / 0.031 | 0.039 / 0.031 | 0.043 / 0.034 | 0.047 / 0.037 |
| Gross -3% | -4.08% / -3.76% | 0.872 / 0.849 | 0.873 / 0.850 | 0.911 / 0.890 | 0.934 / 0.921 | 0.013 / 0.010 | 0.013 / 0.010 | 0.014 / 0.011 | 0.015 / 0.011 |

**Variant: realized-only stops (no exposure term).** Cells are flat / pressure. 10,000 runs, seed 1.

| Edge (no-fail gross mean at the 0.25 SOL cell) | Canary mean per attempt, flat / pressure | P(total stop) by day 7 | P(total stop) by end_ms, start 10-17 | start 10-16 | start 10-15 | P(daily stop) by day 7 | P(daily stop) by end_ms, start 10-17 | start 10-16 | start 10-15 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| September holds | +6.65% / +5.83% | 0.024 / 0.024 | 0.024 / 0.024 | 0.024 / 0.024 | 0.025 / 0.025 | 0.008 / 0.006 | 0.008 / 0.006 | 0.009 / 0.008 | 0.011 / 0.009 |
| Half | +2.56% / +2.18% | 0.096 / 0.095 | 0.096 / 0.095 | 0.103 / 0.103 | 0.109 / 0.111 | 0.010 / 0.008 | 0.010 / 0.008 | 0.013 / 0.010 | 0.015 / 0.012 |
| Quarter | +0.51% / +0.35% | 0.214 / 0.200 | 0.214 / 0.201 | 0.239 / 0.228 | 0.261 / 0.252 | 0.012 / 0.009 | 0.012 / 0.009 | 0.014 / 0.010 | 0.016 / 0.011 |
| Zero | -1.53% / -1.48% | 0.421 / 0.399 | 0.422 / 0.400 | 0.475 / 0.455 | 0.525 / 0.503 | 0.009 / 0.007 | 0.009 / 0.007 | 0.010 / 0.007 | 0.011 / 0.008 |
| Gross -3% | -4.08% / -3.76% | 0.771 / 0.732 | 0.773 / 0.734 | 0.836 / 0.799 | 0.880 / 0.850 | 0.003 / 0.002 | 0.003 / 0.002 | 0.003 / 0.002 | 0.003 / 0.002 |

**Other outputs, primary (exposure) mode, flat / pressure.**

| Edge | P(total stop before 50 fills) | P(reach 50 fills) | P(first-50 P&L > 0, given 50 reached) | Realized P&L to end_ms, start 10-15, p5 / p50 / p95 (SOL), flat | same, pressure | Fills to end_ms (start 10-15) p50, flat | P(any stop refusal by day 7) | Picks skipped per run: open cap / 30 cap |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| September holds | 0.051 / 0.052 | 0.949 / 0.948 | 0.946 / 0.946 | -0.128 / +0.544 / +0.972 | -0.129 / +0.479 / +0.882 | 141 | 0.121 / 0.112 | 0.4 / 11.7 |
| Half | 0.119 / 0.124 | 0.881 / 0.876 | 0.824 / 0.819 | -0.150 / +0.198 / +0.555 | -0.149 / +0.169 / +0.507 | 135 | 0.216 / 0.200 | 0.4 / 10.2 |
| Quarter | 0.191 / 0.195 | 0.809 / 0.805 | 0.688 / 0.672 | -0.159 / +0.011 / +0.356 | -0.158 / -0.000 / +0.323 | 125 | 0.296 / 0.278 | 0.3 / 8.6 |
| Zero | 0.297 / 0.307 | 0.703 / 0.693 | 0.485 / 0.479 | -0.164 / -0.129 / +0.159 | -0.163 / -0.129 / +0.146 | 88 | 0.397 / 0.386 | 0.2 / 6.2 |
| Gross -3% | 0.517 / 0.531 | 0.482 / 0.469 | 0.213 / 0.195 | -0.168 / -0.136 / -0.084 | -0.168 / -0.135 / -0.068 | 48 | 0.498 / 0.485 | 0.1 / 3.2 |

**Sensitivities, primary mode, September and zero edge only, flat / pressure.**

| Arrivals | Edge | P(total) day 7 | P(total) end_ms, start 10-15 | P(daily) day 7 | P(daily) end_ms, start 10-15 | Fills to end_ms p50 (flat) | Open-cap skips per run |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 20/day, uniform in the day | September | 0.061 / 0.062 | 0.062 / 0.063 | 0.040 / 0.035 | 0.053 / 0.046 | 141 | 0.4 |
| 20/day, uniform in the day | Zero | 0.574 / 0.553 | 0.660 / 0.642 | 0.039 / 0.031 | 0.047 / 0.037 | 88 | 0.2 |
| 12/day, uniform | September | 0.051 / 0.050 | 0.053 / 0.053 | 0.017 / 0.014 | 0.024 / 0.019 | 89 | 0.1 |
| 12/day, uniform | Zero | 0.399 / 0.372 | 0.497 / 0.469 | 0.015 / 0.011 | 0.020 / 0.013 | 73 | 0.1 |
| 20/day, clusters of about 3 within 5 min | September | 0.053 / 0.056 | 0.056 / 0.060 | 0.016 / 0.015 | 0.022 / 0.020 | 99 | 62.5 |
| 20/day, clusters of about 3 within 5 min | Zero | 0.407 / 0.401 | 0.504 / 0.498 | 0.010 / 0.009 | 0.014 / 0.012 | 76 | 40.2 |

**Reading it.**

- **September holds or half.** The total stop is rare: about 6% by day 7, and 6% to 21% by end_ms.
- **Zero edge.** The canary is more likely than not to hit the total stop before end_ms: 0.66 / 0.64 for a 10-15 start, and 0.57 / 0.55 by day 7.
- **The daily stop** is a minor channel at every edge (<= 0.073). The total stop (0.175) sits below the daily stop (0.20), so the daily stop fires only after earlier profits.
- **The exposure test bites.** It refuses earlier than a realized-only rule, about +0.15 on P(total) at zero edge. In 12% to 40% of runs some pick is refused by a stop by day 7. Those refusals are transient while positions are open.
- **The ladder check.** "First-50 P&L > 0" passes 0.485 at zero edge, confirming "about half". It is not a test.
- **The 30-a-day cap** skips about 12 picks per run at 20 a day, because NegBin days are lumpy.
- **Clustered arrivals** cost about a third of fills through the open cap (open-cap skips of 40 to 63 per run) and lower the stop odds. Picks skipped by the cap are the hot clustered ones, a selection effect that this model does not price.

**Against DEC-026 section 6's relayed column, P(stop before 50 fills), option (c).**

| Edge | DEC-026 (relayed) | This run |
| --- | --- | --- |
| September holds | 0.06 / 0.05 | 0.051 / 0.052 |
| Half | 0.11 / 0.09 | 0.119 / 0.124 |
| Zero | 0.22 / 0.20 | 0.297 / 0.307 |

The zero cell differs for three reasons:
- **The zero point.** Here the gross no-rent mean is 0 before the canary's costs, so the canary nets -1.64 pp per fill. EXP-025's k = 0 (rent-inclusive zero) nets about -0.82 pp per fill, which falls between this run's quarter row (0.191) and zero row (0.297).
- **Open positions.** The executor's test counts open exposure.
- **The day effect.**

The zero-edge column remains the honest planning case (DEC-026 section 6).

**Not modelled.**
- Correlated fill failure and the best-10% / best-28.9% adverse-fill rows.
- Live fills worse than paper.
- Stuck positions and stranded rent.
- Smaller price impact at 0.05 than at 0.25 SOL.
- The ladder's other checks.
- The fill-selection latch.
- An October regime change or pick rate.
- Synthetic pools (none are in September's evidence).

**Files (QP-1010, verbatim).**

- `qp1010/stop_sim.py` (sha256 `2aa34760de85f330ea4a665ee7598d71066030837fc7a3aef67ef209ca6f983c`).
- `qp1010/seed1.json` (`8a170474a8b24031fc74fad73dc0d61824c014cab65d02a24dcf2240c4290964`).
- `qp1010/seed2.json` (`027ee4f37e812cabd342d8e4813256df9009349aa604b38410084053c9b9140c`).
- `qp1010/seed1.log`, `qp1010/seed2.log`, `qp1010/tables.md`.
- Reproduce: `/data/mal/venv/bin/python -I qp1010/stop_sim.py --seed 1 --sims 10000 --json out.json` (about 10 min wall time; memory not measured, the arrays are about 10,000 x 400 floats).

## 13. Helm's steps

A runbook, `docs/runbooks/c1nf-executor.md`, modelled on `docs/runbooks/h5-executor.md` (Who does what, What is where, Helm's steps with expected output, Wind-down, tier steps, Never), is a builder item (section 11, item 11). Until it is written and merged, these are the steps, in order. Helm owns the commands. **[Note 2026-10-10, QP-1010 (b1) item 20.]** The runbook is written and merged (#531, with Amendment 1's steps). Helm's steps defer to it; the list below is the 2026-10-09 text, kept.

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
- **The canary can show a fault or a large gap, not prove an edge.** At 0.05 SOL and 505,000 lamports a send the fixed cost is 2% of every stake and 10% of the wallet over the first 50 trades, and the result's noise is about 5 pp at 50 trades (section 6). That is a thin test, and it is a safer one.
- **Your 17:30Z choice (stake 0.05, effective total stop 0.175) is the safest of the four options and the least likely to stop early.** Quant-proof's simulation: a stop before 50 fills in about 6% of runs if September's edge holds and about 22% at zero edge, with a worst-case floor of about 0.22 SOL. At a 0.10 stake the same wallet stops 31% of runs if September held and 63% at zero edge. A step back up to 0.10 makes sense when profits have lifted the wallet (section 6). **[Note 2026-10-10, QP-1010 (b1) item 2.]** "Least likely to stop early" ranks the four options **before 50 fills** (and before 30). The full-run odds of options (a), (b) and (d) were not computed. Over the whole run, at zero gross edge, under the executor's exposure test, P(total stop) is **0.57 / 0.55 by day 7 and 0.66 / 0.64 by `end_ms` for a 10-15 start** (flat / pressure; section 12.1); if September's edge holds, about 0.06. Your 17:30Z choice was made on "~22% if there's no edge", the before-50-fills number, not the chance over the run. No limit changes with this note.
- **Its live picks are not the read's picks.** A good canary week does not move the formal read, and a bad one does not either (section 8).

**Quant-proof questions** (none is the owner's to answer).
- **Q1.** Synthetic pools: decided by the owner (kept in; EXP-025 Amendment 2). Open for quant-proof: the class kept as a separate counts-only stream with no class-to-outcome join in the canary's and shadow's records, and the eight of 44 synthetic pools with no `s0` found in REPORT.md section 2 (section 9.3).
- **Q2.** Overlap with H5: this DEC makes no overlap claim (section 9.2). By the two rules the universes overlap and simultaneous holdings do not occur [inferred]. A count-only pass is optional and asked only if quant-proof wants it.
- **Q3.** Power of the fill-selection rule (3 pp, at least 8 unfilled of 30) at C1-NF's variance; whether to keep it as a rate tripwire (section 7 rule 1).
- **Q4.** N and the line for the divergence rule at C1-NF's paired variance. **N = 50 is the manager's term** (DEC-024 uses 100) (section 7 rule 2).
- **Q5.** Whether 1.9 s is the right landing halt given the book's flat response to latency in exploration (section 7 rule 3).
- **Q6.** Whether any of `boost_disabled`, `boost_share_low`, `boost_budget_or_slices_changed` should halt C1-NF rather than alert (section 7 rule 6).
- **Q7.** The pinned model (exploration-only; P2 or a separate canary model) and the bar for parity task 3, including what a live-mode miss may be (section 11 items 12 and 13). **[Note 2026-10-10.]** The parity bar is set (section 11.1).
- **Q8.** A price-impact check at 0.30 SOL for C1-NF's pools, before T2 (section 6).
- **Q9.** The rent number: 2,039,280 lamports (EXP-025 section 6) against 1,513,840 (DEC-024 section 4).
- **Q10.** The twin priced at the canary's stake and fee. At zero edge a "not negative" ladder check passes about half the time (quant-proof); how the other checks behave (sections 6 and 10).
- **Q11.** Appendix A: scope, and the timing (not clock-bound if the shadow's outcome guard is binding; before the first send) (section 8).
- **Q12.** The stop-probability table (section 12). **[Note 2026-10-10.]** Computed (section 12.1); the rows and outputs listed there as not computed stay open.
- **Q13.** The oracle keying difference between #504 (every mint) and EXP-025 section 5.3 (pools first printing at or after 2026-10-16T01), and whether the executor may be stricter than the read (section 9.1). **[Note 2026-10-10.]** Answered (QP-1010 (b1) item 11): acceptable if disclosed; #530 asks about every mint, so the disclosure is mandatory (section 9.1).
- **Q14.** Stop coherence at a 0.5 SOL wallet: **answered by the owner's choice of option (c) and quant-proof's simulation (section 6)**. Open: confirm the simulation's barrier matches the executor's exposure test (the executor refuses earlier than the simulated barrier when positions are open), and the stop odds at a 0.10 step on a larger wallet. **[Note 2026-10-10.]** The exposure-test part is answered: section 12.1's primary mode is the executor's `_budget_stop`. The 0.10-step odds are not computed.

## Amendment 1 (2026-10-10T09:09:01Z, `date -u`; the manager's decision; before any canary send): the #530 review's items A to G, and #502's D1 and D2

**Basis.** The `reviewer` pass on #530 (`claude/c1nf-executor-v2` at `32265af`; "Required edits: none"; 113 C1-NF tests and 1,098 H5 and probe tests passed there) listed seven open manager decisions, each with a recommended value. #530 and #531 are merged (main `5cc8632`). This amendment adopts all seven as recommended, with one placement choice in B, and records two decisions for #502. It changes no stake, stop, tier, cap, rule, model, threshold or EXP-025 parameter. Items A, F and G name values the merged code already holds; D makes the DEC's text match the code's stricter reserve; E makes rule 4's text match the code; B and C close the seal inputs, which already fail closed without them. No host was contacted.

**A. Escalated and emergency priority: 1,010,000 lamports** (`escalated_priority_lamports`, `tools/c1nf_executor.py:189`). It is 2 x the owner's O-3 value of 505,000 and, per the review, equals the signer cap. One escalated send is 2.02% of a 0.05 SOL stake. The per-exit reserve follows it (D). The number now belongs to this DEC, not to the builder. Section 6's Priority row carries it.

**B. `pick_file` = `/srv/mal-cap-pick/picks.jsonl`** in `scripts/mal-fast/c1nf-executor-live.json` (the keyless dry-run config does not have the key: its round trips run before the seal window). #509's exporter runs with an explicit `CAP_PICK_OUT`, not its `$HOME` default. The exporter's user writes the file; the C1-NF unit can only read it. **Placement (the manager's choice):** the review suggested `/srv/mal-cap-pick` for the exporter's output itself. Here `/srv/mal-cap-pick` is the fixed path **inside the unit**, and the host directory is the job user's `/home/<jobuser>/data/h5-shadow/cap-pick`, the single exporter's `CAP_PICK_OUT` that H5's `pick_file` also reads (#540); the exporter's FINAL marker directory `~/data/cap-pick-oracle` is not bound. It is bound read-only by a Helm drop-in (`scripts/mal-fast/mal-c1nf-executor-cap-pick.conf`, installed as `20-cap-pick.conf`, checked by `check-c1nf-unit.py --cap-pick`). That is the pattern H5 uses for `/srv/mal-h5-shadow` and C1-NF for `/srv/mal-c1nf-shadow`: the job user needs no root-made directory under `/srv`, and the bind is checked to be read-only from a plain home path. The installer refuses a live config without this `pick_file`, and the daily check accepts and checks the drop-in. It must be installed before 2026-10-16T01Z, or every pick from then is refused and the Look-2 half of the canary does not trade. #509 is open at this writing. Runbook: `docs/runbooks/c1nf-executor.md`, "The CAP-PICK exporter for C1-NF" and Step 5.

*Note 2026-10-10T09:45Z: #509 is merged (`b5ba835`, 2026-10-10T09:23:48Z) and so is #540 (`288f490`, 2026-10-10T09:31:54Z), which pinned H5's `pick_file` to the same `h5-shadow/cap-pick` directory this item names. #540 removed the `JsonlPickOracle` that the C1-NF executor subclassed, so `claude/c1nf-pickoracle-fix` switches the executor to #509's `PickOracle`, as H5 reads it; until that merges the executor does not import. The exporter job, the bind and the file modes have not been run or checked on a host.*

**C. `FINAL_WRITTEN`.** The manager writes `/var/lib/mal-live/c1nf/FINAL_WRITTEN` with `sudo touch` on fast-0, only after the DEC-016 FINAL is written, **never before 2026-10-16T02:00Z**, in the same ledgered step as `/var/lib/mal-live/h5/FINAL_WRITTEN` (H5's method, `docs/runbooks/h5-executor.md:432`). It is section 11 item 11's runbook entry ("Item 11: the FINAL markers").

**D. Section 6's reserve text** is now: "1,015,000 per exit (one escalated send + base fee); a first 0.05 SOL buy needs 103,625,000 lamports, about 0.104 SOL" [inferred arithmetic: 50,000,000 stake + 505,000 buy priority + 5,000 base fee + 2,100,000 ATA rent + 1,015,000 for one exit + 50,000,000 floor]. It replaced "1,000,000 per exit". The executor reserves `max(1,000,000, 1,010,000 + 5,000)` per exit (`c1nf_exit_reserve`, `tools/c1nf_executor.py:326`). The 3,050,000 full-ladder reserve is **not** adopted: the 0.05 SOL wallet floor already covers the worst retry cost, about 10 x 1,015,000, roughly 0.0102 SOL per position, and buys stop after 5 failed sells (`sell_stuck`, `tools/probe_live.py:403`).

**E. Rule 4's retry text** (section 7) now matches the code: H5's ladder with no C1-NF override, a resend on each failure up to `SELL_MAX_ATTEMPTS` = 10 (`tools/probe_live.py:55`), then the position is abandoned and Helm runs the root sell-and-close. The old text, "Retries back off from 30 s to 10 min", described no code. H5's back-off applies only before the escalation and only after 5 failed sells; for C1-NF that window is landing + 300 s to + 315 s, so in practice it never applies. A fast exit is worth more than the fees saved, buys are already stopped by `sell_stuck`, and the cost is bounded as in D. The code does not change.

**F. `max_attempts` 450** (`tools/c1nf_executor.py:188`): kept, as a backstop only. At 30 a day, the time up to the `end_ms` clamp (2026-10-24T00:30Z) allows at most about 420 to 435 attempts (the review's count), so 450 never binds.

**G. `max_days` 15**: kept, as a backstop only. `C1NF_END_MAX_MS` and the total stop bind first.

**#502 (the C1-NF wallet ledger, `claude/c1nf-ledger`, open at `2e437ff`).** The PR left two decisions to the manager (its review comment of 2026-10-10T08:17Z, items 4 and 8).

*Note 2026-10-10T09:45Z: #502 is merged on main as `31d6c3e` (2026-10-10T09:23:35Z); "open at `2e437ff`" above is the state at this amendment's writing.*

- **D1, the ledger root's seeding (#502 runbook section 2): option B, plus the 10-05 tip day.** The live root starts from the 36 exploration daily files (byte-equal to the pinned EXP-025 wl/, per #502) and the tip days **10-05**, 10-06 and 10-07 from research-0 `/data/mal/hunt-1008/c1nf-ledger/out/all/daily/`, sha256 lists equal at both ends, then `check --daily` exit 0; then the tip days from 10-08 to yesterday are built in date order (where, in D2), then `asof --day <today>`. Option A (tip days only) is not taken: its `ndays`, `n` and skill features would differ from training and from EXP-025's read (DEC-026 section 11 item 13). 10-05 is a partial day: the tip archive starts at 10-05T05 (19 h that day), and the follower's 10 `backlog_jump` gap records (599 slots) are all on 10-05 between 05:19 and 05:26Z [from #502's PR body]. With 10-05 in, the snapshot's hole is the known `[09-25T07, 10-05T05)` and no wider [inferred]. #502's option B names 76 files; research-0's `out/all/daily/` has no 10-05 (38 manifests, review 2). 10-05 is built first on research-0 from `/data/mal/tip-tape-archive/fast-trades-tip` with `--verify-sha --allow-missing-hours`, and its manifest must record `hours_missing` 00–04 (#502 runbook section 2, B step 0). The copy is then 78 files.
- **D2, scheduling: the job chain.** On mal-fast-0, MiScusi jobs chained with `after`, each only after the previous one succeeded: (0) preconditions: venv `/home/claude/venvs/c1nf-ledger`, duckdb prints `1.5.6`, one `--allow-open-day` dry build of today as the job user; (1) the seeding copy and its two checks; (2) the catch-up builds, 10-08 to yesterday, in date order; 10-08 and 10-09 are built on research-0 with `--verify-sha` and copied (10-08 leaves the fast-0 live dir about 10-11T00Z); then `asof --day <today>`; (3) the nightly rollup at 00:15Z (#502 runbook section 3: POSIX `sh`, `mem_gb: 8`, `--require-prev-day`, then `check --asof-day`). Steps as in #502 runbook sections 2–3. A failed step stops the chain. A failed or refused night is an alert the same day. One heavy job at a time on fast-0, with `systemctl show user-1002.slice -p MemoryCurrent` read before the first submit.
- Neither has run. They take effect when #502 is reviewed and merged.

*Note 2026-10-10T09:45Z: #502 is now merged (`31d6c3e`, 2026-10-10T09:23:35Z), so D1 and D2 are no longer waiting for the merge. Neither the seeding copy nor any step of the chain has run on fast-0.*

**Not decided here.** The review's non-blocking notes: whether the `sell_stuck`, `feed_stale`, `feed_gap` and `sps_unmeasured` refusals belong in `STOP_REASONS` (`tools/c1nf_executor.py:175`) rather than counting as unfilled picks in rule 1's monitor; and the absent exit reserve for a buy still in flight (H5's, unchanged). Both stay open.

**Not verified.** Nothing here was run on a host: not the bind under systemd, not the exporter or the job user's file modes, not the ledger copy. The review's own "not verified" list stands (the chain, the md5 replay of item 14, the dry run of item 16).

## Amendment 2 (2026-10-10T09:54:08Z, `date -u`; text only; before any canary send): quant-proof QP-1010's stale-statement fixes, the stop table, item 13's bar

**Basis.** Quant-proof's `/data/mal/hunt-1008/c1nf-verify/QP-1010.md` (sha256 `e25d99101e12f875689a7916ccc56c9fff89de1ff6e90a3e947fc1f2386f7a1b`), part (b1)'s 27 stale statements at main 528f0ca, part (b2)'s stop table, and part (a)'s parity bar.

**What it does.** It adds dated notes at each stale statement, marked "Note 2026-10-10, QP-1010 (b1) item N", and leaves the old dated text as it was. It copies part (b2) verbatim as section 12.1 and part (a) verbatim as section 11.1. It adds section 11 items 24 to 28. It says, wherever the old text gives the "~6% / ~22%" stop odds, that they are **before 50 fills**, with the full-run zero-edge number beside them (0.57 / 0.55 by day 7, 0.66 / 0.64 by `end_ms` for a 10-15 start).

**What it does not do.** It changes no stake, stop, tier, cap, rule, model, threshold, limit or EXP-025 parameter. The owner's quoted words are unchanged. It records no quant-proof OK. Items 6 and 7 of (b1) were partly closed by Amendment 1 (#544) after QP-1010's read; the notes say so.

**Not verified.** No host was contacted. (b1) item 4 (the wallet-wide `STOP`) is not checked on the host. `docs/HANDOFF.md` lines 147 and 265-268 (b1 item 27) are outside this file and not edited here.

## Note 2026-10-10T09:55:36Z (`date -u` at drafting; the manager's decision; before any canary send): security review F2, F3 and F4

**Basis.** The C1-NF executor security review (section 11 items 9 and 17), research-0 `/data/mal/hunt-1008/c1nf-verify/SECURITY-REVIEW-1010.md`, head reviewed main `528f0ca`, verdict FINDINGS. F0 and F1 (blockers that fail closed) are fixed in their own PRs. This note records F2, F3 and F4. It changes no stake, stop, tier, cap, rule, model, threshold or EXP-025 parameter, and nothing of H5's unit or code.

**F2 (MEDIUM), decided: the C1-NF executor runs as its own system user, `mal-c1nf`, not `mal-live`.** The review: both key-holding units ran as `mal-live`, and systemd makes `/run/credentials/<unit>/<name>` readable by the unit's `User=`, so a code-execution bug in either process could read the other wallet's key; the H5 -> C1-NF direction cannot be closed from C1-NF's unit without editing H5's. With one uid per wallet neither unit's process can read the other's credential, in both directions, and H5's unit is not edited. Section 5's "User and sandbox" and "State dir" rows now say `mal-c1nf`. The review's weaker stopgap (`InaccessiblePaths=` on H5's credential directories, start-time only, one direction) is not taken. What changes (PR `claude/c1nf-own-user`):
- `scripts/mal-fast/mal-c1nf-executor.service`: `User=mal-c1nf`. `check-c1nf-unit.py` expects exactly that line; H5's `mal-live`, root, `Group=` and `SupplementaryGroups=` refuse.
- Helm, before the install (runbook Step 3b): `useradd --system --no-create-home --shell /usr/sbin/nologin --user-group mal-c1nf`, and `setfacl -m u:mal-c1nf:--x /var/lib/mal-live` (search only: `/var/lib/mal-live` stays `mal-live` 0700, and without the entry the unit cannot reach its own state dir). Step 5: `/var/lib/mal-live/c1nf` is `mal-c1nf:mal-c1nf` 0700. The pinned installer creates none of this; it refuses if the user or group is missing, has `mal-live`'s uid, or is in the `mal-live` group.
- The daily check expects `mal-c1nf:mal-c1nf:700` on the state dir. Its `--print-sudoers` adds the manager's two `--status` reads, run as `mal-c1nf`, never root. `--clear-halt` and `--mark-closed` run as `mal-c1nf`.
- Runbook Steps 6 and 11 add the review's check that never opens a key (access(2) only, exit codes only), each with a positive control: `systemd-run --wait --quiet -p User=mal-c1nf /usr/bin/test -r /run/credentials/mal-h5-executor.service/probe-wallet` expects 1, and `-p User=mal-live` against `/run/credentials/mal-c1nf-executor.service/c1nf-wallet` expects 1.

**F3 (LOW), accepted as a residual risk for the canary: egress is not limited to RPC.** The unit has `RestrictAddressFamilies=AF_INET AF_INET6` and no `IPAddressDeny=`/`IPAddressAllow=` (H5's unit is the same; H5's review did not cover it). A compromised process could reach any internet host to exfiltrate the key, and localhost services (the Postgres port, other listeners) and link-local metadata. The review's proposed fix, kept here as the reference:

> Fix: in-unit (with a checker update, tested in the keyless dry run so DNS and RPC still resolve): `IPAddressDeny=localhost link-local multicast 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 fc00::/7` `IPAddressAllow=127.0.0.53/32 127.0.0.54/32` (the resolved stub; check /etc/resolv.conf on fast-0 first). A real "RPC only" egress needs a per-uid nftables allowlist or an egress proxy. That is firewall work, which belongs to Helm: the request goes via the owner. Otherwise the gap is recorded as an accepted residual in DEC-026.

Why it is accepted for now: low severity; using it needs code execution inside the pinned, hash-verified, sandboxed process first; the in-unit lines are a unit change that needs a keyless dry run on fast-0 to show DNS and RPC still resolve, then a new install and hash check; the real allowlist is firewall work, which is Helm's. The loss is bounded by the 0.5 SOL funding cap (owner capital, no top-up).

**F4 (LOW; H5's review NIT, still open on main, shared with H5), accepted as a residual risk for the canary: the signer has no PumpSwap discriminator allowlist and no buy min_out floor** (`tools/probe_live.py:309-320`, the PumpSwap branch of `validate_message`). Today no code path builds such a message; C1-NF refuses `zero_quote`, so on-chain min_out > 0; spend is capped twice (the instruction amount and the WSOL wrap transfer, both <= stake). The review's proposed fix, kept here as the reference:

> Fix: in the PumpSwap branch, refuse unless `data[:8] in (tx.DISC_BUY_EXACT_QUOTE_IN, tx.DISC_SELL)` (add tx.DISC_BUY only if a builder uses it). For a buy discriminator, require `max_spend_lamports is not None` and a min-out field >= 1 (buy_exact_quote_in: `int.from_bytes(data[16:24], "little") >= 1`). This is shared code: run test_probe_live, test_h5_* and test_c1nf_*. H5's installed tree changes only at H5's next pinned install.

Why it is accepted for now: low severity, and the fix is in signing code H5 shares, so it belongs in one shared-code PR with its own reviewer pass and H5's next pinned install, not in the C1-NF canary's path before 10-16.

**Revisit both after 2026-10-16** (after the DEC-016 FINAL and the CAP-PICK seal start). The manager then decides F3 (the in-unit lines, or a request to the owner for Helm's per-uid allowlist) and F4 (the shared-code PR for H5 and C1-NF), and records the decision here.

**Not verified.** Nothing in this note was run on a host: not `useradd`, the ACL, the `chown`, the `--status` reads as `mal-c1nf`, or the credential access checks. They are runbook Steps 3b, 5, 6, 7 and 11.

## Note 2026-10-10T13:11:59Z (`date -u` at drafting; text only; before any canary send and before any soak result is used as evidence): the live creator-feature anchor, and quant-proof's soak checklist

**Basis.** Quant-proof's ruling on #503 item 7 (met), with one fix asked before merge (the per-feature restart diff, `tools/c1nf_vmode_parity.py`, job #535 compared 0 rows) and this note. It changes no stake, stop, tier, cap, rule, model, threshold or EXP-025 parameter, and it records no quant-proof OK on item 15.

**A documented live difference, listed before any soak result.** The live engine counts creator history **only from the bootstrap anchor**. These features come from the engine's creator tables (`_by_cr`, `_g_cr`, `_cr_pools`) and from each mint's create row (`_info`): `cr_prev_creates`, `cr_prev_grads`, `cr_known_out`, `cr_lmax6`, `cr_lend6`, `cr_big6`, and the creator-history gates behind them. The first start's anchor is the start time minus `--bootstrap-hours` (26), replayed through the current hour: **27 hours back** (`bootstrap_plan`; the anchor is written once to `c1nf-anchor.json` and every restart replays from it). The training batch (12_passC) counts the same features **from tape start**. So a creator that was active before the anchor looks newer live than in training: **live undercounts** `cr_prev_*` and `cr_known_out` for those creators, and the model sees a different value than it was trained on. A restart replays from the same anchor, so it is meant to rebuild the history an uninterrupted run holds (item 7's anchored mode is the test of that; its result is not recorded in this note). This is a difference between **live and training**, not between a restarted and an uninterrupted live run. It also means a window-only rebuild (job #513's shape, `--restart-mode window`) is not what `run_live` does.

**Not measured.** How many decision rows, stage-1 rows or picks this touches, and in which direction the pick set moves, has not been measured: no job has compared live-anchor creator counts with tape-start counts. Nothing here is an edge claim. The shadow's picks are the live rule's picks (section 8), and a soak result is a result about that rule on this feature state. **No soak result (item 15), shadow outcome or canary fill statistic is to be used as evidence about the read's rule before this difference is carried next to it.**

**Soak checklist (quant-proof's, for item 15 and the first real restart).**
1. **Replay-vs-live first-hour check after the first real restart.** Replay the first hour after the restart from the tape (the same anchor, `tools/c1nf_vmode_parity.py` rules) and compare it with what the live shadow wrote. Compare the **decision-row md5 over (T, pool, feature_hash)** and the **picks**. Differences are allowed **only on held mints**, the mints the book held at the restart, which the executor refuses with `already_held`. Any other difference is a FAIL of the check, listed by (T, mint) with the feature names that differ (names and counts only).
2. **A per-start record, for every start (the first and each restart).** Fields: `bootstrap_anchor`, `bootstrap_hours_n`, `bootstrap_empty_hours` (**must be 0**), wall time, peak memory, `stream_expires`, `expire_errors`, and the gap from the end of the bootstrap to the first live decision minute. The shadow's `c1nf_start` record carries `bootstrap_anchor`, `bootstrap_first_start`, `bootstrap_hours_n` and `stream_expire_s`; its counters carry `bootstrap_empty_hours`, `bootstrap_rows`, `stream_expires` and `expire_errors`. Wall time, peak memory and the gap to the first live decision minute are **not** in the shadow's record: the MiScusi job log or the manager records them (`systemctl show` for memory, per section 11 item 18).
3. **Scoring removes or flags repeat picks on mints held at a restart.** A restart drops the shadow's book, so it can pick again a mint the executor still holds; the executor refuses it with `already_held`, and the live position is the one that counts. Any scoring of the shadow's picks across a restart either drops those repeat picks or flags them, and says which.

**Not verified.** No host was contacted. The size of the creator-anchor effect, the live restart time at a long anchor, and every item above on the real feed are open. `bootstrap_empty_hours` and the other fields are read from the code on this branch, not from a run.

## Note 2026-10-10T14:14:11Z (`date -u`; text only; before any canary send and before any soak result is used as evidence): item 15 runs at #571's merge head, not 7ba92d1

**[Note 2026-10-10T14:14:11Z: item 15 runs at #571's merge head, whose `tools/c1nf_shadow.py` blob is `9d437f04012b6c0119396e16dc88b85f06b4210e`, not 7ba92d1.]** #571 changes only `TipTail.bootstrap` in `tools/c1nf_shadow.py` (blob 39941b6 → 9d437f0). Each hour is now read in two passes, sort keys and byte offsets first and then the rows, instead of being held as one list in memory. This is because job #568 was OOM-killed at anon-rss 1,939,908 kB. Model, threshold, features, decision logic, `OUTCOME_START_MS` and the record contract are unchanged. The proof is at the one changed interface (the rows fed to `Shadow.feed`, plus `off`, `ino`, `rows_read`, `bad_lines`, `resets`). Unit tests compare against a frozen copy of the old code, and the reviewer fuzzed about 1,300 tip dirs. Job #591 ran at 9cbafa7 (blob 9d437f04) on live tip hour 2026-10-10T12, 300k-line slices of each kind file, plain and as `.zst`: row-sequence md5 `2a51e67f3d5c618f195ed215eaf1b0e3` old and new, 603,490 rows each, empty hours 0/0, `bad_lines` 0/0, offsets equal. A replay-mode decision md5 does not call this path, so item 14 stays open as before. Known difference: in a `.zst` hour a bare CR no longer splits a line; follower output never contains one. Each bootstrap row is now parsed twice, so checklist item 2 records the bootstrap wall time and the cgroup's peak memory (MemoryPeak), not RSS. The out dir (`/home/claude/data/c1nf-shadow`) is not on tmpfs. Checklist item 1 is the first real-feed test of this path. (Quant-proof OK on #571 at 9cbafa7, 2026-10-10.)

**[Same note: soak hours and the event-V acceptance.]** Quant-proof's ruling of 2026-10-10 (~14:00Z; `/data/mal/hunt-1008/c1nf-verify/QP-P7-1010.md`, sha256 bf298d8a…) records the tip follower's P7 line-1 acceptance as pinned as FAILED (job #576; a law-scope gap on `multi_hop_swap` and dust exact-out buys, not a stamp error) and keeps the event-V deploy without accepting it. Until the amended acceptance passes on a declared fresh hour, the stamp feeds nothing that counts: **no item-15 soak hour counts** before that PASS instant, and no event-mode live trading. A soak started earlier runs for operations only (memory, errors, bootstrap); its item-15 clock starts at the PASS instant, recorded in LAB_STATE.

## Note 2026-10-10T15:10:04Z (`date -u`; text only; before any canary send and before any soak result is used as evidence): item 15 runs at #576's merge head

**[Note 2026-10-10T15:10:04Z: item 15 runs at #576's merge head, `tools/c1nf_shadow.py` blob `008816fe6c8b00a2f96469228185c2278b648ca5`, not 9d437f04.]** #576 changes only how the wallet ledger is read: `_PreadSnapshot` (sparse index + `pread`) replaces memmap reads in `AsofDirLedger.snapshot_for_day`, with fallback to `_AsofSnapshot`. `tools/c1nf_wallet_ledger.py`, the hash pin, the model, the threshold, the features, the decision logic and `OUTCOME_START_MS` are unchanged. The proof is at the changed interface: `get()` is bit-equal for every wallet on exploration as-of days 2026-09-20 (10,912,272 wallets, md5 `f068fa8df8b63c74e598ab91d7405b16`) and 2026-09-19 (10,019,059, `50652d0c853253eb5da2aadb8cb4a7cd`), old = new = `passa_matrix`, plus 2,400,010 absent-wallet probes per day (jobs #601/#602). Memory on research-0: VmRSS peak 1,026,744 kB old vs 259,268 kB new (#599/#600). The fast-0 split is extrapolated, not measured. A replay-mode decision md5 does go through this path, so item 14 covers it when it runs; item 14 stays open. The restart is operations-only under the P7 ruling above; checklist items 1 and 2 apply to it, with RssFile/RssAnon and cgroup `memory.stat` file/anon recorded. (Quant-proof OK on #576 at 7bcffaa, 2026-10-10; reviewer OK at 7bcffaa.)

## Note 2026-10-10T21:37:10Z (`date -u`; text only; before any canary send and before any soak result is used as evidence): item 15 runs at #582's merge head

**[Note 2026-10-10T21:37:10Z: item 15 runs at #582's merge head `da9b5b0`, not 09b4389.]** Blobs, old → new:
- `tools/c1nf_shadow.py`: `008816fe` → `c2dafcdcdc69f7f0269746431c01755dc7f45e4b`.
- `tools/c1nf_features.py`: `8d7be366` → `d6c87354a520ea25efc59975d7d041570d8226f7`.
- `tools/c1nf_vmode_parity.py`: `f5766c41` → `1574573ed10c663784f4678d04c251e96ff8460b`.

**Why.** Job #608 (item-15 soak at 09b4389, operations-only) was OOM-killed at 18:06:01Z. Its process anon-rss was 1,939,888 kB and cgroup anon 1,987,047,424 B at the 1,945,600 kB limit, growing about 400 MB/h. The engine kept every canonical in-band print of each pool until g0 + 25 h, at about 1,051 B per print. Replay #621 (48 h, 2026-09-19/20 exploration tape) reached RssAnon 9,796,372 kB, with VmHWM 10,736,316 kB.

**What #582 changes.** It changes memory only:
- **F1:** a 1 h print window. Off by default in `FeatureEngine`; on only through `cs.build_engine`, used by `run_live` and `c1nf_vmode_parity`, so `c1nf_parity` is unchanged. A look-back past a dropped print counts `window_violation` and answers None.
- **F2:** typed columns, with q, b, seen_any and the per-print lp dropped.
- **F3:** per-pool trader ids.
- **F4:** live keeps 1 ledger day; `_PreadSnapshot` memo cap 200k.
- **F5:** `malloc_trim(0)` after each stream expire, plus the record additions below.

The model, threshold, features, decision logic, `OUTCOME_START_MS` and the record contract are unchanged. The record changes are additions only:
- heartbeat: `rss_anon_kb`, `vm_hwm_kb`, `held_prints`, `held_pairs`, `window_violation`;
- shadow counters: `malloc_trim`, `malloc_trim_unavailable`.

**Proofs** (PR #582 comment 6102366435; reviewer OK 6102420479 and quant-proof OK 6102424925, both at cf686467):
- **md5 decision equivalence on 2026-09-20,** `c1nf_vmode_parity` anchored, the same tool blob `1574573e` on both heads. OLD is 09b4389 (#642), whose blobs equal 822cefb and main 38dec78. NEW is cf68646 (#643). Equal on both:
  - decision rows 437,281: float32 `287e6bca6193705d483714296abdba41`, float64 `70d7340b6f8bbf7e185c3de2e52931d1`;
  - picks 2,161: `93c5af1a0354217fc10879fd9afdf7d4`;
  - anchored-restart rows 223,998: `8cd29588fbdf2881c4d2e4cec8ddb6e0` / float64 `48c35a9078029bab8c76f0f9f78c4d17`;
  - restart picks 1,177 / 1,180, with 151 differences, all on held mints, on both sides;
  - feature_diff: 0.
  - The new head dropped 16,621,527 prints with window_violation 0. The runs used the stub model (pred 0.05); the float64 vectors are bit-equal, so the pinned model sees the same inputs.
- **Memory, 48 sim hours, window on, 1.9 GB cap, job #653: PASS** under the rule written before the first run (VmHWM ≤ 1,500,000 kB; slope h25–48 ≤ 20,000 kB/h; window_violation 0; errors 0).
  - VmHWM 1,022,988 kB. RssAnon 735,984 → 896,232 kB from h25 to h48, a slope of 7,424.5 kB/h. 0 errors.
  - It fed the pre-serialised row stream (`harness_wp`, #652, round-trip exact, 26,480,626 rows). The pandas harnesses ran out of memory in the harness process itself: #644, #645 and #648, with controls #649 and #650.
- **Unit tests:** 352 passed, 2 skipped (#641); 355 passed with duckdb (#646); executor and model_pin 82 passed.

**Not measured.** Do not claim any of these:
- memory on mal-fast-0 itself;
- anything past 48 h. Growth until about 72 h, when `_info` and `bc_traders` prune, to roughly 1.1 GB RssAnon is an estimate;
- the wall time of `malloc_trim`, which runs in `_expire` before `_decide` on every tenth minute;
- a real-tape `c1nf_parity` md5 with the window off and F2/F3 on (only the synthetic cross-head twin covers it);
- the one-day ledger keep across a UTC midnight on real tape (synthetic test only).

**Item-15 soak rules at this head** (quant-proof and reviewer, PR #582):
- (a) The heartbeat `window_violation` stays 0. Any non-zero value voids bit-equality from that minute and stops item 15's clock.
- (b) From hour 25, RssAnon grows at most 20 MB/h over any 24 h.
- (c) Alert when RssAnon reaches 1.5 GB.
- (d) `malloc_trim_unavailable` is 0 on fast-0.
- (e) MemoryPeak and RssAnon are recorded at every start (checklist item 2).

The item-15 clock starts at this soak's start. The event-V P7 amended PASS (2026-10-10T16:10:46Z, job #598) precedes it. The restart is recorded in LAB_STATE. The creator-anchor note of 13:11:59Z still applies: the anchor stays 2026-10-09T12.

## Appendix A. Draft EXP-025 amendment for the canary (not applied; its own PR into `EXP/EXP-025-c1nf-part1-prereg.md`)

**This is proposed text.** It edits nothing in EXP-025 today. EXP-025 Amendment 2 is #529 (synthetic pools; open draft at 12f5133), so this text is Amendment 3 if #529 merges first, and whoever merges it renumbers it. The sentences in quotation marks are proposed wording in the form of [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md) Amendments 2 and 3. The owner has not seen them. **[Note 2026-10-10, QP-1010 (b1) item 3.]** #529 merged first (2026-10-09T17:53:58Z), so this is Amendment 3. It is open as #548.

### Amendment 3 (`<MERGE INSTANT, UTC, from date -u at merge>`; before any canary send): declared observation for the canary and shadow outcomes, the canary's stake, the executor's CAP-PICK skip, observers, no split by synthetic class, report disclosure

**This amendment is blind to C1-NF outcomes (to be confirmed at merge).** At the merge instant no C1-NF shadow or canary outcome (label, fill, exit, P&L, mean, CI or day sign) of any October decision had been computed or seen. If any had, this sentence is replaced by a plain statement of what was seen and when. Nothing else is claimed about what was in view.

**The gap it closes.** Section 5.1 declares the real-time observation for "decisions inside the windows" and names a 0.02 SOL canary. Not in it: the canary's actual stake; decisions at or after 2026-10-24T00; the live executor's CAP-PICK skip (section 5.3 names the read and the shadow); the observers and the report disclosure; and the bar on splitting outcomes by synthetic class, which Amendment 2 sets for the read side.

1. **Pre-window decisions (held in reserve).** The shadow's code guard starts its outcome records at 2026-10-10T00 and is binding (DEC-026 section 11 item 8), so no outcome of a decision before 2026-10-10T00 exists and this item is not used. If the guard is relaxed, "by a manager decision derived from the owner's recorded decision O3 (DEC-025) and the default the owner was told on 2026-10-09 (DEC-026 O-6, applied unless he objects), the paper shadow's and the live canary's outcomes for decisions with T in `[<MERGE INSTANT>, 2026-10-10T00)` are observed in real time. This is declared before any such outcome exists." The read's own labels for those rows (pass A inside the locked job) stay sealed. The read never takes the shadow's output as training input. **[If the owner objects to the O-6 default, this item is deleted.]**
2. **Post-window decisions.** "The same outcomes for decisions with T at or after 2026-10-24T00, up to the canary's `end_ms`, are observed in real time. No look reads them." This is not a CAP-PICK exception (item 3).
3. **The executor's CAP-PICK skip.** Section 5.3's exclusion and fail-closed oracle apply also to the live executor of DEC-026: from 2026-10-16T01 to the end of EXP-022's read it trades no mint the oracle answers True for, writes no CAP-PICK field into any record and joins no record to a pick. A missing, stale, erroring, non-boolean or undecided oracle means no buy. If the executor refuses a wider set than section 5.3's keying (DEC-026 section 9.1), each look's report says so.
4. **The canary's stake.** Section 5.1's "0.02 SOL stakes" and "at most 0.1% of a pool's quote", and section 12's "0.02 SOL stakes", read **0.05 SOL stakes** and **at most about 0.25% of a pool's quote** (0.05 SOL against a stage-1 real quote of at least 20 SOL). The separate wallet and DEC-026 are unchanged. The canary's trades remain real chain activity, appear in the tape as ordinary rows and are not removed (that would edit chain truth); the read reports them. DEC-025 section 4 names 0.02 SOL too; DEC-025 is not edited here, and DEC-026 section 1 records that the owner's answers supersede it.
5. **Observers, and no split by synthetic class.** The owner, Helm, the manager, builders, the DEC-026 watchdog (its Discord alerts, including the stops and the wallet line) and the daily check may read the canary's ledger and the shadow's outcomes in real time. The read tool may not: its inputs stay section 4's hours, it never takes the canary's ledger or the shadow's output, and its refusal set is not loosened. **No observer, and no report, note, alert, Console entry or message, may split C1-NF outcomes by synthetic class before the final look** (Amendment 2 item 5, "before Look 2 is read, or, if Look 2 does not run, before the final C1-NF report"). The class stays a counts-only stream. A breach is recorded here and the read is reported compromised.
6. **Report disclosure.** The Look 1 report and the Look 2 report each state that the shadow's and canary's outcomes were observed in real time for decisions in the windows (section 5.1) and, if used, for decisions at or after 2026-10-24T00 (item 2); that this can have influenced later choices, for example an owner step to a larger stake; and that **any EXP-025 amendment dated after the first such outcome was made with outcomes in view and is listed in the report, not called outcome-blind.** The disclosure does not change the verdict.
7. **Unchanged.** The rule block, the five pinned lines, the patches and hashes, the windows, the alpha pair (0.005, 0.020), items 1 to 9 of section 7, P2 to P7, R1 to R14, section 11.5, k = 1, and everything Amendment 2 sets. Section 5.1's sentences stand: Look 1 and Look 2 are always read as written and never skipped, delayed, re-scoped or re-thresholded because of anything the shadow or canary shows; nothing they show may change any EXP-025 parameter; a canary halt, stop or step stops or changes the canary only.
8. **Spending.** Nothing is spent. No look is refused, withdrawn or not run because of this amendment.
9. **Enforcement in the tools.** The shadow withholds outcome-bearing records for decisions before 2026-10-10T00 (binding). The shadow and the executor write the class, if at all, only to a separate counts-only stream. The daily check and the watchdog print no outcome by class.
10. **Provenance.** A manager-side draft by the DEC-026 drafter, derived from DEC-025 section 4, EXP-025 section 5.1 and Amendment 2. The owner's words are the O3 sentence and the `OWNER_CANARY_CONFIRMED` line and addendum of DEC-026. The owner may revoke it. A revocation applies from its recorded instant, restores section 4's seal for decisions after that instant, and does not undo the disclosure for outcomes already observed.

**Timing.** This amendment **need not merge before 2026-10-10T00:00Z, provided the shadow's outcome guard is binding** (it keeps every pre-window outcome from existing, and section 5.1 already covers in-window decisions). **It must merge before the first send.** Amendment 2 (#529) is the one that must merge before 2026-10-10T00:00Z.

## Sources

- [DEC-025](DEC-025-c1nf-family.md) (sections 4 and 5, `OWNER_DECISION_CONFIRMED`), [EXP-025](../EXP/EXP-025-c1nf-part1-prereg.md) (sections 0, 2, 3, 4, 5, 6, 7, 9, 10, 11).
- [DEC-024](DEC-024-h5-live-canary.md) with Amendments 1 to 3, [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md) Amendments 2 to 4, `docs/runbooks/h5-executor.md`, `tools/h5_executor.py` (`TIERS`, `TIER_WALLET_FRAC`, lines 147 to 163), `tools/pump_structure_monitor.py` (halt flags, WATCH_RULES, `SYNTHETIC_MAX`, `post_complete_buy_seen`).
- [DEC-019](DEC-019-execution-probe.md), [DEC-020](DEC-020-size-step-proposal.md), [DEC-021](DEC-021-champion-challenger.md) section 7, [DEC-018](DEC-018-live-trial-readiness.md), [EXP-022](../EXP/EXP-022-cap-pick-part1-prereg.md) section 9.
- PR bodies: #502 (ledger, 53da60f), #503 (shadow, 2a96ce0), #504 (executor, closed, a98ff07), #506 (features, 3ae9915), #509 (pick oracle, 983b12c), #517, #518.
- `/data/mal/hunt-1008/c1nf-verify/VERIFY.md` sections 5.2 and 6 (as EXP-025 copies them) and `/data/mal/hunt-1008/JUDGE-4.md` (text search only). Exploration; not evidence of an edge.
- `docs/HANDOFF.md`, state 10-09 ~07:35Z (ladder table, C1-NF open items, A3 state).
- **[Note 2026-10-10, QP-1010 (b1) item 27.]** The PR heads above are stale: #502 merged (head 68f05a4), #506 merged (8b74dbc), #509 merged (8b3ce7c), #503 is an open draft at 9a80398, and #504 is superseded by #530. The `tools/h5_executor.py` line range above (147 to 163) has moved (section 6 notes). Not cited before: #530 (executor v2, 32265af), #531 (ops, f6e70e2), #540, #543 (DEC-024 Am.6 and EXP-022 Am.6), #544 (Amendment 1), #548 (Appendix A, open), #550 (model pin, merged 10:37:03Z), #552 (oracle import fix), and quant-proof's `/data/mal/hunt-1008/c1nf-verify/QP-1010.md` (sha256 `e25d99101e12f875689a7916ccc56c9fff89de1ff6e90a3e947fc1f2386f7a1b`). Outside this file, `docs/HANDOFF.md` lines 147 and 265-268 ("DRAFT PR #522", "#504 CLOSED") are stale too.
