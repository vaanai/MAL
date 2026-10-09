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
