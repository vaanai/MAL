# EXP-027: r4-b on Forward walk 2 (push-family book 1, DEC-030 slot 5), pre-registration

**Draft. Not registered. Nothing has run.** Written 2026-10-16 (`date -u` 2026-10-16T19:58:25Z at the start of writing). To write it, no file under `/data/mal/blocks/` (forward-1002, forward-1002ev, forward-1016 or any sealed block), `/data/mal/exp012-forward`, or any shadow, canary, probe or walk output was opened. The inputs are text only:

| Input | sha256 at writing |
| --- | --- |
| [DEC-030](../DEC/DEC-030-walk2-push-slot.md) draft and DEC-021 Amendment 6 draft, branch `claude/dec030-walk2-push-slot` | (git blob on that branch) |
| `/data/mal/hunt-1008/push-1016/WALK2-PREREG-CHECKLIST.md` | `26cfd7f4bf22dba43b39744eaaba449f73f3fce30335e61ecdb61d07542fbce7` |
| `/data/mal/hunt-1008/laya-opt/FROZEN-r4-b.md` (cited `FROZEN:<line>`) | `cc626dde5c3e68222b1fcf066db19a6ce0a412a2e91507e941effd10d0aebc49` |
| `/data/mal/hunt-1008/push-1016/hunt6/r4b-verify/QP-RULING-1016.md`, ruling and follow-up (cited `QP §n` and `QP-FU §n`) | `bf12e075f72087d984954b84e4806a522add905f48183391d809d5f3883904a5` |
| `/data/mal/hunt-1008/push-1016/hunt6/r4b-readtool/PLAN.md` (cited `PLAN §n`) | `bb6a6876ad39fe35e8cce4b92cd7f7cfd840cf320248ff47ab998ab9f5ca3552` |
| `/data/mal/hunt-1008/JUDGE-3.md` (line 248 only) | `e54fa0ae0053c6480b00105a0c110ddc6d985471061a22e5b27af9540b5752bb` |
| PR #672 (features, E-a) head `8311d52`, PR #673 (core, E-b, E-c) head `34d2bf6`: PR bodies and `tools/r4b_book.py`, `tools/r4b_frozen.py` source | (git) |
| [EXP-022](EXP-022-cap-pick-part1-prereg.md), [EXP-025](EXP-025-c1nf-part1-prereg.md), [EXP-026](EXP-026-h5-boostclock-v2-prereg.md), [DEC-014](../DEC/DEC-014-holdout-ledger-and-multiplicity.md), [DEC-019](../DEC/DEC-019-execution-probe.md) Am.2, [DEC-021](../DEC/DEC-021-champion-challenger.md), [HOLDOUT_LEDGER](../docs/HOLDOUT_LEDGER.md) | (git, `origin/main` at `ee7bd45`) |

This file is docs only. The builder, scorer, xs engine, layout adapter, count job and read tool are separate PRs (section 2.2). Nothing in this file says or implies that r4-b makes money. **r4-b has zero out-of-sample trades and has not cleared the promotion gate** (QP §header). **A FAIL is the most likely outcome** (section 9).

| Field | Value |
| --- | --- |
| **ID** | `EXP-027-r4b-walk2-prereg` |
| **Status** | **DRAFT.** Not registered. Registers only on the conditions of section 0.1. |
| **Declared (UTC)** | 2026-10-16 (draft) |
| **Family and slot** | Push-family **book 1** of [DEC-030](../DEC/DEC-030-walk2-push-slot.md) (draft), DEC-021 §8's fifth slot (Amendment 6, draft), family alpha 0.025, Bonferroni 0.025/k. |
| **Parent** | `FROZEN-r4-b.md` (laya-opt judge, 2026-10-08 ~23:25Z): the l3 hourly top-10 cross-sectional book on S1's 4-h LightGBM, 5-seed bag. Quant-proof's VERIFY ruling of 2026-10-16 and its follow-up (`QP-RULING-1016.md`). |
| **Rules** | DEC-014 (a), (b); DEC-021 §1, §4, §8, Amendments 1 (§4, §5 definitions), 5, 6 (draft); DEC-030 §1, §1a, §2 (R1), §2a, §3; DEC-019 Amendment 2 (the 160k condition only); HOLDOUT_LEDGER Rules item 5 (draft) and the Forward walk 2 row. |
| **Hypothesis** | The frozen r4-b book (section 2) has mean SOL per attempt > 0 at 0.25 SOL per position on post-W0 walk-2 hours, under both fail models at both entry latencies (1.3 s and 1.9 s), on the deciding cost stack (section 5), and clears the promotion gate and the day-level test at 0.025/k (section 6). |
| **Kill condition** | A FAIL (section 6) retires r4-b: no retune, no refit, no change to K or stake, no re-read (section 7.2). |
| **Expected outcome** | **FAIL.** On the pinned stack, at N = 20 blocks, QP puts P(full pass) at about 0.24 at the central effect and 0.63 if all of the measured edge is real (k = 1); about a third less at k = 2 (section 9). P(pass) with no edge ≈ 0.022. |
| **Tools** | Builder `tools/r4b_features.py` (#672), pins/scorer/book `tools/r4b_frozen.py`, `tools/r4b_score.py`, `tools/r4b_book.py` (#673), adapter `tools/r4b_layout.py`, count job `tools/r4b_count.py`, read `tools/r4b_read.py` (PLAN §2, §7). |

**Labels.** [measured]: copied from a cited file that computed it; exploration arithmetic is measured arithmetic, not evidence of edge. [inferred]: reasoned. [pinned]: a design value this file fixes; not evidence. **QP to confirm**: a rule written here that quant-proof has not yet ruled on; it binds only as written in the merged head, and quant-proof's OK on that head is its confirmation.

## 0. Status, registration, route and pinned lines

### 0.1 When this file registers

It registers only when **all** of these hold, and it never registers otherwise:

1. **DEC-030 has merged** with the owner's dated line in its section 4 (question `q__jVaqpxB937kEg` is PENDING at writing), quant-proof's OK on DEC-030's exact final head, and a reviewer verdict on the DEC-030 PR.
2. **This file merges** with quant-proof's OK on **this file's exact final head** and a **reviewer verdict on this PR**. The merge is a separate call with `--match-head-commit`.
3. The ledger edit of Appendix A is in this PR's final head (DEC-030 §2a: "The edit is made in that book's own pre-registration PR, which does not merge without it").
4. Every **TO FILL** item of section 13 marked "before merge" is filled in the final head.
5. The merge instant is **not before 2026-10-18T00:00:00Z** (section 0.3).

### 0.2 Route (the owner's line in DEC-030 section 4 is PENDING)

This file is written for **route R1** (DEC-030 §2, R1: no backward-block PASS for push-family books; DEC-021 Amendment 5's "only through §2" overridden for push-family books).

> **CONDITIONAL, read before merge.**
> - **Under R2:** this file **cannot register until r4-b has PASSED a one-shot read on a reserved, unread backward block** under the promotion gate at p < 0.025/m with **m = 17** (DEC-030 §2, R2; DEC-014 per-block terms: that block's Part 1 merged first, one block kept unspent). That needs its own Part 1 (not this file), and the first push-family PR must list all 17 sources of m in the ledger or raise m. The pinned line `EXP027_ROUTE` is then changed to R2 before merge, and the PASS record is cited here by path and sha256. Note: the three candidate blocks predate the 2026-10-02 upgrade and the 2026-10-13 redeploy (DEC-030 §2).
> - **Under NO SLOT:** this file **does not register**. DEC-030 does not merge, slot 5 is not opened, and r4-b has no registrable read on walk 2. The file is then closed as withdrawn before counting, with no hour read.

### 0.3 W0 and the earliest merge

- **W0 [pinned].** The first full UTC hour strictly after this file's merge instant, by `date -u` (DEC-030 §1a: "the first full UTC hour after its pre-registration merges ..., never moved"). Hours before W0 are never counted.
- **W0 must be ≥ 2026-10-18T01:00Z** (QP §2: "use only pools created at or after 10-16T01Z ... The 48 h ramp is complete if W0 ≥ 10-18T01"). **DEC-030 has no provision for a W0 later than the first full hour after merge**, so a later W0 cannot be declared. Therefore: **this file must not merge before 2026-10-18T00:00:00Z.** A merge at or after that instant gives W0 ≥ 2026-10-18T01.
- **Block alignment (advice, not a rule).** QP §3 recommends W0 at 00:00Z so the 24 h blocks are UTC dates. That happens only if the merge falls in the hour before a midnight (earliest: merge in `[2026-10-18T23:00Z, 2026-10-19T00:00Z)` gives W0 = 2026-10-19T00). If the merge falls elsewhere, the blocks start at W0's hour; the gate's date items still count UTC dates (section 6).
- **Last W0 [pinned].** The stop needs at least 20 blocks with attempts by the cap, so W0 + 480 h ≤ 2026-11-16T00, i.e. **W0 ≤ 2026-10-27T00**. If this file has not merged by 2026-10-26T23:59:59Z, it is withdrawn before counting; a re-filing is a new file on later hours.
- **Withdrawal** is possible only before W0. After W0 this book is never withdrawn, re-scoped or delayed except by a section 7.1 NOT_DECIDABLE condition.
- **Window record.** The manager appends the merge instant, W0, the cap instant and k at registration (dated, `date -u`, from the merge record only) under "Window record" at the end of this file, and in the ledger sub-entry, before W0.

### 0.4 Pinned lines

```
EXP027_BOOK_ID: r4-b
EXP027_FAMILY: DEC-030 slot 5, push-family book 1
EXP027_ROUTE: R1
EXP027_COUNT_START: W0 = the first full UTC hour strictly after this file's merge instant (date -u); never moved; refused if before 2026-10-18T01
EXP027_LAST_W0: 2026-10-27T00
EXP027_STOP_RULE: E = the first 24 h block boundary W0 + 24h*j at which at least 20 blocks from W0 hold an attempt and at least 125 attempts are counted
EXP027_EXIT_HORIZON_H: 1
EXP027_CAP_END: the earlier of W0 + 504 h and 2026-11-16T00
EXP027_DEADLINE: E + 48 h
EXP027_ALPHA: 0.025/k, Bonferroni, k frozen at the first push-family read
EXP027_DECISION_P: the largest p over the 4 cells (flat, pressure) x (1.3 s, 1.9 s) of the one-sided day-level t over 24 h blocks from W0, empty blocks dropped, df = W - 1
EXP027_SEND_FEE_LAMPORTS: 505000, or 160000 only under section 5.3
EXP027_RENT_DECIDING: 0 per filled trip under section 5.2 conditions (i)-(iii); 2039280 per filled trip if condition (i) is unmet at merge
```

- **Format.** Each line matches `^<KEY>: ` exactly once in this file, as written. The read tool refuses unless each does and unless this file is clean against HEAD. The values never change after merge.
- **Meaning.** `E` is the stop instant. The last entry mark is E − 1 h; every open position is sold at the E mark; hours are read to E + 1 h (the exit horizon). If E has not occurred by `EXP027_CAP_END`, the book is NOT_DECIDABLE (p = 1, stays in k). With the cap, the window plus its exit horizon ends by 2026-11-16T01 (DEC-030 §1a, §2a).
- **k.** Under DEC-030 §1, k is the number of push-family books registered when the first push-family read starts (k = 1 if r4-b is alone then; 2 or 3 if more books have registered). After that instant k never changes. A FAIL or NOT_DECIDABLE push book stays in k at p = 1.

## 1. What this is and is not

- **One book, one EXP** (DEC-030 §1). Push-family book 1. Retunes of r4-b are the same book (DEC-014 clarification) and are never registered.
- **It supersedes FROZEN's forward-1002 read.** The FROZEN window "forward-1002 **[2026-10-02T16:00Z, 2026-10-14T01:00Z)**, read **once**" (FROZEN:72) **was never run and is renounced.** No r4-b outcome on any forward-1002 or forward-1002ev hour is ever computed for any purpose. FROZEN's universe edge (pools created ≥ 2026-10-02T16:00Z, FROZEN:76) and its slot-era halves bar (FROZEN:104) are replaced by sections 3 and 6 of this file. FROZEN §1, §2 and §4's cost items stand as amended here.
- **It supersedes JUDGE-3.md:248** ("Data: the same forward-1002 window. Its decisions start 10 min after graduation, but the since-seed features still carry pool-open information, so it also stays out of walk 2.") by quant-proof's D3 ruling (section 3.6).
- **Not a promote and not gate evidence today.** The development figures are in-sample for selection (section 10.4).
- **Not a live authorization.** A PASS leads only to the DEC-018 path and the owner's yes (DEC-030 §1, §5). DEC-021 §7 takes its second branch (no gated champion on walk 2); §6 does not apply.

## 2. The frozen book and its pins

### 2.1 Spec (copied from FROZEN §1; nothing changes)

- **Universe.** l3 decisions: every graduated token with a canonical PumpSwap pool (the PDA), on a 2-min grid from 10 min to 48 h after graduation, that traded in the last 10 min; restricted as in section 3.4.
- **Features.** The 63 past-only features of `02_build.py` (post-order-fix md5 `535ac7062ab0eb15c2d08bceb9175710`), in `features.txt` order; prints with slot < the decision slot; complete market minutes only.
- **Score.** The mean over seeds 1..5 of `Booster.predict(X)` (raw float64), the five frozen boosters.
- **Book.** At every UTC hour mark t: rank live tokens by score, descending, ties to the lower token id (`mid`); sell every held token not in the top 10 (trigger at t); buy every top-10 token not held; a held top-10 token continues at no cost; no minimum score, no hysteresis, no intra-hour stop; at most 10 positions at 0.25 SOL (2.5 SOL at risk); exits only at hour marks; a token sold at a mark is not re-bought at that mark (#673 `xs_book`).
- **Ranking precision [pinned].** The forward read ranks the **float64** bag score, as FROZEN §1 states. **Disclosure:** the development harness ranked a **float32** cast of the bag (`get_score`), and E-c reproduces the harness with `rank_dtype=np.float32`. Near-ties can order differently between the two; this is not corrected.
- **Book start.** The book is flat at W0; the first buys are at the W0 mark. No position opened before W0 exists.

### 2.2 Pins

| Item | Pin | Source |
| --- | --- | --- |
| r4-b_seed1.txt | md5 `2ad4137024aa1d765224c27e5d8b2310`, sha256 `d726c0ab3f294bfe0c059360893a6db959863e8fb031d13cd7163363e7bc0335` | FROZEN §2 |
| r4-b_seed2.txt | md5 `eb26e2ea1c1b45cecfa4133a735a5002`, sha256 `c12831c8a9e8ea1a00ebab6e15f0e51e4b6372913dbb0507c8516b0711d31875` | FROZEN §2 |
| r4-b_seed3.txt | md5 `8591cc0b4abe97a46f85107bb173fe97`, sha256 `fd93f682038f248d9cc60ce8972d40ffdd6898a4a806fe9163c9bc23dcb1a2c9` | FROZEN §2 |
| r4-b_seed4.txt | md5 `45d6b1c48c10e0bed550eb338f0f32c9`, sha256 `f2555f6190e34c085431014858a6ac430294127e373614bd6d0fa598bda9fb53` | FROZEN §2 |
| r4-b_seed5.txt | md5 `4938a1006addb81cc1586320d1a9abbe`, sha256 `2451d659cecd43f6a142f4e5efd018ed48e129ff827c0666956625a611f4c469` | FROZEN §2 |
| features.txt | md5 `18531b0072d79a6127b5a4aea082d2c7`, sha256 `eb0f980d069b05a38d3e5a76d826274a3faaba312c012c99afd8e3dc825e576a` | FROZEN §2 |
| equiv_fixture.npz | md5 `b59137e921895ac4ceef3c415da94b71`, sha256 `42704b0c69b9aa8660d1d36af5738a9965efb944f9b3f92d2633a0f362dae285` | FROZEN §2 |
| Repo copies | `models/r4b/` (the five boosters, features.txt, the fixture), hashes equal to the rows above (#673) | #673 |
| harness/specs/r4-b.py | md5 `6121a64775870521effbf7c34f4d00b8`, sha256 `e7761004e588911264d149555ccf2cbe04cb2f043ccb0a9f4566f090ba0f7c08` | FROZEN §1 |
| harness/specs/S1.json (parent) | md5 `c976e08d6252c2f5e7674d1b19203ca3`, sha256 `477bae8b9c9d39cd15c287c4d626a4ce6ab94e8c9f361b1d2e44a874c6092d92` | FROZEN §1 |
| 02_build.py (post-order-fix) | md5 `535ac7062ab0eb15c2d08bceb9175710` | FROZEN §1 |
| judge/final_fit.py, judge/fit_lgb.py | md5 `ff229a86cdcd8f03c020017a8c0498b6`, `568b2ad97ebea3718b0e1149bb11f4e3` (record only; never re-run) | FROZEN §2 |
| lightgbm | **4.7.0** (numpy 2.5.3 in the scoring venv) | FROZEN §2; PLAN §2 |
| Score | raw mean over seeds 1..5 of `Booster.predict(X float64)` | FROZEN §1 |
| `tools/r4b_features.py` (#672) | `<SHA256 at merge of #672>` **TO FILL** | |
| `tools/r4b_frozen.py`, `tools/r4b_score.py`, `tools/r4b_book.py` (#673) | `<SHA256 at merge of #673>` **TO FILL** (one per file) | |
| `tools/r4b_layout.py` (adapter) | `<SHA256 at merge of layout>` **TO FILL** | PLAN §7 task 4 |
| `tools/r4b_count.py` and its wrapper | `<SHA256 at merge of count>` **TO FILL** | PLAN §7 task 6 |
| `tools/r4b_read.py` and its wrapper | `<SHA256 at merge of read>` **TO FILL** (recorded in the Window record before the read, with QP OK on its head) | PLAN §7 task 7 |
| PumpSwap fee tiers | `FEE_TIERS` of `tools/r4b_frozen.py` (= `tools.paper_curve_math.PUMPSWAP_SOL_FEE_TIERS` = harness `hlib.TIERS`); sha256 of its canonical JSON `<TIERS sha256>` **TO FILL** | QP §5 |
| Decoder blobs | walk 2's job ref (`forward_v_join` PINNED_BLOBS, `pins --ref`) `<blob ids>` **TO FILL** | PLAN §4 |
| Input views, tries | every input view's sha256 and the `data/tries.jsonl` entry for r4-b `<TO FILL>` | DEC-021 Am.5; PLAN §4 |
| rb2 reproduction keys | sha256 of the 5 rb2 prediction keys and of `results/r4-b-rb2` trades `<TO FILL>` | QP §2 |

**Book constants [pinned]** (QP §5; `tools/r4b_frozen.py`): K = 10; 0.25 SOL (250,000,000 lamports) per position; hour marks (3,600 s); ties to the lower `mid`; no minimum score, no hysteresis, no stops; universe 10 min to 48 h after graduation, traded in the last 10 min; PDA pool; event V.

**Cost constants [pinned]:** section 5.

### 2.3 Equivalence proofs (recorded before registration)

| Proof | What | Result | Status |
| --- | --- | --- | --- |
| **E-a** | Port fed l3 `ut/` hours 2026-09-23T00..09-25T06 reproduces the fixture X (617 rows, `mid`, `T`, `sd` equal, X float32 byte-equal) | md5(X bytes) **`3e9bc6225377927682d80b9441391a96`** = fixture [measured, #672] | done (#672, unmerged) |
| **E-b** | The 5 boosters on fixture X reproduce seed1..5 and `bag_score` | max abs diff 0.0; 617 rows, 52 with bag > 0 [measured, #673] | done (#673, unmerged) |
| **E-c** | The xs engine, fed the rebuilt walk-forward bag (float32 ranking), reproduces the rebuilt **`results/r4-b-rb2`** trades (n **2,986**) | trades table (harness columns, `%.9g`) text-identical, md5 **`54052f798013a71e3a9f40e0d6830ac1`**; exits rank 2,956, gap 20, end 10; the four deciding cells equal the r4b-verify REPORT at 6 decimals [measured, #673] | done (#673, unmerged) |
| **E-d** | md5 decision-equivalence replay on clean-view explore-0814 w7→w4, `[08-14T12, 08-21T12)`, walker layout (static-map V, seg_end 08-28T12): I and X bytes equal l3 `feat/<day>.npz`; Qe/Be/spot/ssb/nearby equal `cache/l3/dec`; marks equal `cache/l3/marks`; PDA-vs-V-band mint list empty (or md5 taken without them); event-V injection test gives the same md5 | `<md5s>` **TO FILL** | not run |

**FROZEN:123 is amended [pinned].** FROZEN:123's target "reproduces `results/r4-b/trades.csv.gz` (n 3,002)" **cannot be done** (QP §2: the 10-08 cache is deleted). The reproduction target is **`results/r4-b-rb2` (n 2,986), reproduced from the 5 rb2 prediction keys (sha256-pinned, section 2.2), md5 `54052f798013a71e3a9f40e0d6830ac1` of the trades table** (#673 E-c). The 10-08 `results/r4-b` file (n 3,002) stays on disk as a record and is not a target.

**m_grads as-of impact, exploration (QP §2 asked for it before registration) [measured, #672, report-only, in-sample for the boosters]:** 184,485 hour-mark rows, 761 marks; rows where `m_grads` changes 1,847 (1.0%); max |Δ bag score| 0.011352, mean over changed rows 0.000421; 2 rows flip the score's sign; top-10 members changed per mark: **0 at all 761 marks**. Approximation disclosed in #672: graduation set = l3 meta members and first-print slot = `meta.s0`, not a PDA derivation; non-member graduations not assessed.

### 2.4 Reproduction gap: the development book is one noise draw (QP §2, disclosure)

- Prints with a null `tx_index` tie in `order by mid, slot, txi, evi`, and DuckDB orders the tie differently on each run (`/data/mal/hunt-1008/swarm-1013/w2-r4b-repro/REPORT.md` §1). #673 also found duplicate `(mid, slot, txi, evi)` keys in `ut/` hours before 09-22 (e.g. 195 in 2026-09-10T05).
- **The development book is a single noise draw.** Between runs, **700 entries were only in the old run and 684 only in the new.**
- Between runs, **maxDD went from 0.087 to 0.183 of peak**, **the worst 7 days from −0.31 to −0.99 SOL**, and **fresh-0903 from +0.78 to −0.14** [measured, QP §2].
- **The fixture covers only 09-25, which has 0 tie groups**, so E-a and E-b cannot see the tie problem.
- The forward score comes from md5-frozen boosters, so the gap does not bias the forward read (QP §2). It does mean every development number in section 10.4 is one draw of a noisy pipeline.

## 3. Forward builder rules [pinned]

These bind the adapter, builder, count job and read tool. They are QP §2's rules unless marked otherwise.

### 3.1 m_grads as of the decision

A graduation counts toward `f_m_grads_1h` and `f_m_grads_10m` at decision time T only if **mbt < T and its canonical PDA pool's first print has slot < sd(T)** (`grads_rule = "asof_pda_first_print"`, #672). Raw migration events are not counted; never-printed pools stay out.

### 3.2 Total order of prints

Rows are ordered totally by **(slot, tx_index NULLS LAST, event_index, file_row_number)** (`forward_order`, #672). The precount reports the number of null-`tx_index` tie groups (`null_txi_tie_groups`) and of slots mixing null and non-null `tx_index` (`mixed_null_txi_slots`). **QP to confirm** whether a count > 0 of mixed slots refuses the read; this draft makes it report-only. Exploration had 0 mixed slots over all 36 l3 days [measured, #672].

### 3.3 Slot time and k

- **Rounding:** **np.round** (half to even; at 200 ms slots 1.3 s = 6.5 slots → 6), `K_ROUNDING = "round"` (QP §2; #673). k = np.round(lat / slot_s), for lat = 1.3 s and 1.9 s. Not switched on results.
- **slot_s per UTC day** (QP §2: "pin slot_s per day"): slot_s(date) = 3600·H / Σ SPH_h over the date's H hours, with SPH_h from `cap_pick_score.measure_hour_sph` (PLAN §1). An hour with unmeasurable SPH is a refusal. **QP to confirm** this day aggregation (PLAN §1 and §5 measure SPH per decision hour).
- **Exit lag:** ceil(0.55 s / slot_s − 1e-9) slots, asserted ≥ 2 (3 at 200 ms).

### 3.4 Universe and hours

- **Pools created at or after 2026-10-16T01:00Z only** (QP §2), so no forward-1002 hour (and no D2 hour) is ever read. Membership: PDA match and V0 in [17.5e9, 17.7e9] lamports (PLAN §1; the share outside the band is reported).
- **Directory.** Only `/data/mal/blocks/forward-1016` (the Forward walk 2 row). Never `forward-1002` or `forward-1002ev`, including any file there named for an hour at or after 2026-10-16T01.
- **Feature-buffer hours** `[2026-10-16T01, W0)` are read for features, membership and the clock only, never counted, and only after this file merges. **2026-10-16T01 and 2026-10-16T02 are used as feature buffer** (the 2026-10-16 breach disclosure, section 10.3, requires saying so). Since W0 ≥ 2026-10-18T01, every pool in the universe has at most its first 48 h before W0, so the since-seed features have full history at W0.
- **History before 2026-10-16T01 [PLAN §8 item 3; QP to confirm].** For a token created before 2026-10-16T01 whose pool was created after it, create-derived constants (`f_c_*` from bonding history, creator history) are NaN where their inputs lie before 2026-10-16T01; creator history (`f_cre_*`) uses walk-2 creates only. This is a train/serve skew (training saw full history) and is disclosed, not corrected.
- **Decisions in the first 600 s of the pool [QP to confirm].** To meet the D3 condition (section 3.6), a decision row with T < block_time(s0) + 600 s is dropped before ranking (s0 = the first print of the PDA pool). The count of dropped rows is reported. FROZEN's grid starts at graduation + 600 s, so this can only remove rows.

### 3.5 LP Deposit/Withdraw [QP to confirm]

The decoder does not see PumpSwap LP Deposit/Withdraw (PLAN §8 item 5). **Rule:** a pool with such an event is **excluded from the point of the event**: no buy at any mark after the event, and a held position is sold at the next hour mark as a forced exit, priced on the tape like a rank exit. Detection, past-only: at a decision with slot sd, an LP event is detected when, for consecutive prints i, i+1 of the pool with slot < sd, the pre-trade reserves of print i+1 differ from the post-trade reserves implied by print i by more than `<tolerance>` **TO FILL** (or from a decoded LP instruction if the decoder gains one). Entry and mark states use each print's own pre-trade reserves, so they stay exact (PLAN §8 item 5). Excluded pools and forced exits are counted and reported.

### 3.6 The H5 / D3 domain (QP ruling, quoted verbatim; supersedes JUDGE-3.md:248)

DEC-030 §3 excludes the H5 domain from push-family books "unless quant-proof rules otherwise in writing before registration". Quant-proof's written ruling (QP §4(b)), verbatim:

> "QP's ruling: feature-only use by the frozen scorer at decisions ≥ s0+600 s is not D3 use. This holds only if no feature is a synthetic class or BOOST flag, and the read reports nothing restricted to [s0, s0+360)."

- **This ruling supersedes JUDGE-3.md:248** ("... the since-seed features still carry pool-open information, so it also stays out of walk 2.").
- **Conditions, as this file applies them:** (a) decisions at T ≥ block_time(s0) + 600 s only (section 3.4); (b) the 63 names in `features.txt` contain no synthetic-class feature and no BOOST flag [inferred from the names; `f_c_v` is the pool's V0 constant, **QP to confirm** it is not a BOOST flag]; (c) the read reports no quantity restricted to `[s0, s0+360)`; (d) no synthetic class is joined to any r4-b outcome.
- Because QP ruled this is not D3 use, the alpha stays in slot 5; it does not go to the H5 family (DEC-030 §3).

## 4. Data, window, stop rule, count job

### 4.1 Window

- **Counted window:** entry marks in `[W0, E − 1 h]`; hours read `[2026-10-16T01, E + 1 h)` (feature buffer before W0, counted from W0, exit horizon after E).
- **Blocks:** 24 h blocks from W0; block b holds attempts with entry mark T0 in `[W0 + 24h·b, W0 + 24h·(b+1))`.
- **N = 20, A = 125, cap 21 days, exit horizon 1 h** (QP §3 and QP-FU §3: "N=20, A=125, cap 21 d still recommended"). These are stricter than DEC-030 §1a's floors (N ≥ 7, A ≥ 100, cap ≤ 21 d), as §1a allows a book to declare before W0.
- **Expected attempts** [inferred from FROZEN §6 and PLAN §4]: about 103 per day, so about 2,000 at E; N binds, not A.

### 4.2 Attempts and the count job

- **Attempt:** a buy triggered at an hour mark T0 in `[W0, E − 1 h]` for a top-10 token not held at T0, guarded buys included.
- **Count job** (`tools/r4b_count.py`, an hourly host-side MiScusi job, 12 GB cap; PLAN §4): opens only allowlisted, sealed, strict-verified, sha256-matched hours; writes per mark live tokens, decision rows, finite scores, attempt timestamps, blocks with attempts, attempts total, `stop_reached`, the md5 of the mark's decision lines, and the precount items of section 8.1, to `/data/mal/r4b/counts.jsonl`. It never loads a row at slot ≥ sd(t) except slot and block_time for the clock, and computes no landing state, guard, fill, exit or P&L. **It is outcome-blind and is not a "read"; it does not freeze k** (DEC-030 §1).
- **Open item, QP to confirm before merge (PLAN §4 "OPEN QP RULING (blocking)").** Ranking at mark t needs features from prints before t, and for tokens held since t − 1 h those are post-entry prices (HL rule on post-entry columns). If quant-proof rules that the count job may not compute ranks, `EXP027_STOP_RULE` is replaced **before merge** by a fixed-calendar stop E = W0 + 480 h, with N ≥ 20 and A ≥ 125 checked inside the read (fewer is NOT_DECIDABLE). At about 103 attempts a day both stops give the same E unless a block is empty.
- **The stop instant** is recorded in the ledger sub-entry (Appendix A) when the count job reports it, from the count file only.

### 4.3 The read and its deadline

- **One read, no interim looks** (DEC-030 §1a). It runs after E + 1 h is sealed and verified, the read tool is merged with QP OK on its head (sha256 in the Window record), E1 is recorded (section 5.1), and the section 5.3 record is in the ledger or its fallback applies.
- **Deadline:** `EXP027_DEADLINE`. A read not completed by then is NOT_DECIDABLE, and the window is spent (checklist §1 d).
- Fixed-instant steps (the count job, the read) run as host-side MiScusi jobs, never as session crons alone.

## 5. Cost stack (deciding, both latencies)

### 5.1 Deciding items [pinned]

| Item | Deciding value | Source |
| --- | --- | --- |
| Send fee | **505,000 lamports per send**, on the buy and the sell; a guarded buy pays one | FROZEN §4; `EXP027_SEND_FEE_LAMPORTS`; 160,000 only under section 5.3 |
| Rent | **0** per filled trip under section 5.2 (i)-(iii); 2,039,280 if (i) is unmet | QP-FU §1a; `EXP027_RENT_DECIDING` |
| Entry | **1.3 s and 1.9 s, both gating**; x = sd + k, state before the first print with slot ≥ x + 1, END bound | FROZEN §4; #673 `mint_states` |
| Guard | buy reverts if exec price > **1.15 ×** decision spot: P&L −one send, no rent, no haircut, not "filled" | FROZEN §4; #673 |
| Exit | the mark state at clock_slot(t) + exit lag + 1 (section 3.3), END bound, at least 2 slots; past the loaded hours, the derived final state (`FINAL_FEE` 0.0125) | FROZEN §4; PLAN §5 |
| Pricing | PumpSwap PRE-trade reserves incl. per-print event V; fee tier on (Q incl. V, B) per side; own buy re-injected; own sell impact | FROZEN §4 |
| Live haircut | tokens received / 1.002608 (entry +26.08 bps) and proceeds × (1 − 0.0016) (sell −16 bps), the VERIFY formula as #673 implements it | DEC-021 §1, Am.1 §4 |
| E1 penalty | each filled trip loses the **larger** of the haircut and max(0, −5·r̄), r̄ = E1's `pnl_gap_lamports_live_minus_sim` mean at 0.05 SOL scaled linearly to 0.25 SOL (×5) **[QP to confirm the scaling, PLAN §8 item 8]**; E1 record `<path, sha256, n>` **TO FILL before the read** | DEC-021 Am.1 §4; DEC-030 §3 |
| Flat fail leg | 0.85·pnl − 0.15·send | DEC-021 §1 |
| Pressure fail leg | p = σ(c + 0.8·ln(1+ssb) + 0.35·ln(1+nearby/1e9)), c refit per cell so mean p over the cell's fills = 0.289; P&L = (1−p)·pnl − p·send; ssb = buys in slot x; nearby = buy SOL over slots [x − round(2/slot_s), x] | DEC-021 §1; #673 |
| Strand class (iii) | section 5.2 | QP-FU §1a |

### 5.2 Rent: the deciding stack is pinned without rent (QP-FU §1a, conditions verbatim)

Quant-proof ruled "Rent: YES, the deciding stack is pinned without rent", on the evidence that the sell and `close_token_account` run in one transaction (61 of 61 probe trips refunded, net 0; 19 more calibration trips net 0). The conditions, verbatim:

> - (i) The EXP pins the sha256 of the sell builder that would trade r4-b, and requires the close in the same transaction. Without it, rent comes back into the deciding stack.
> - (ii) The rent leg stays as report-only.
> - (iii) Rent can strand on three kinds of position. Each is charged the full stake, the buy send, no sell send, and 2,039,280 rent:
>   - an unpriced exit;
>   - a base mint with a freeze authority or a Token-2022 extension that blocks close (for example withheld transfer fees);
>   - simulated proceeds below one send fee.
>   The forward builder flags the first two from data known at decision time.

- **(i) as pinned here.** Sell builder: `<path>` sha256 `<SHA256 of the sell builder that would trade r4-b>` **TO FILL before merge**; it must perform the sell and the ATA close in one transaction. If this is not filled in the merged head, `EXP027_RENT_DECIDING`'s second clause applies (2,039,280 per filled trip, deciding) and the power in section 9 is the "with rent" row.
- **(iii) as pinned here.** A strand-class trip's deciding P&L = −250,000,000 − one send − 2,039,280. **QP to confirm** the data source for the freeze-authority / Token-2022 flag in the walk-2 layout (no mint-account field is listed in PLAN §1's layout); if no source exists before merge, the read refuses (it does not guess). QP estimates about 5.1% of fills are rug-class, so (iii) costs about 0.04 pp per trade.
- **(ii)** The 2,039,280-per-filled-trip leg is reported, never deciding.

### 5.3 Conditional 160k send leg (QP-FU §2, exactly)

The deciding send fee becomes **160,000 lamports per send** (155,000 priority + 5,000 base) **only if all** hold:

1. **DEC-019 Amendment 2 PASS** (DEC-019:235): **Test A and Test B both hold** (A: landing lower bound > −5 pp; B: buy-lag upper bound < +0.5 slot).
2. The PASS is read from **the fee-arm's final verdict record, pinned by path and sha256** (`<path>`, `<sha256>`, **TO FILL when it exists**).
3. It is read **by someone who has opened no r4-b outcome**.
4. It is **recorded in the ledger (Forward walk 2 Status cell, this book's sub-entry) before r4-b's read lock is written.**

**Fallback:** a FAIL, a NOT_DECIDABLE, a missing record, or a verdict after the read start all leave the send fee at **505,000**. There is no partial version.

**Lag shift [pinned].** Under the 160k leg, both entry latencies take the fee-arm's measured buy-lag shift: Δ = **Test B's one-sided 95% upper bound** (slots) of mean(test) − mean(control) (QP chose the upper bound). k = np.round(lat / slot_s + max(0, Δ)) for lat = 1.3 s and 1.9 s. **QP to confirm** the floor at 0 (a negative bound is not credited).

**Disclosure:** the fee-arm measured buys right after graduation, and carrying that over to a book that does not race is an assumption.

### 5.4 Report-only legs

55,000 per send; the rent leg (2,039,280 per filled trip); 4.0 s entry; 5-slot exit; no-fail; the l3 frozen-model xs10 for comparison; drop-best-mint; the share of positions losing ≥ 90% (rugs); daily-mean CI90 lo, days positive, maxDD, the worst 3 days; the window's two halves (FROZEN's slot-era halves bar, FROZEN:104, does not apply to walk 2 and is replaced by this report-only item, PLAN §5); the trade-level and 10,000-draw bootstrap p (DEC-030 §1: day clustering breaks them).

## 6. Decision

### 6.1 Cells

Four cells: {flat 15%, pressure scale 1} × {1.3 s, 1.9 s}, all on the deciding stack of section 5.

### 6.2 Decision statistic [pinned]

**The one-sided day-level t over 24 h blocks from W0, empty blocks dropped, df = W − 1; the larger p of the 4 cells decides; pass iff that p ≤ 0.025/k** (`EXP027_DECISION_P`, `EXP027_ALPHA`).

- Per cell: m_b = mean SOL per attempt in block b; W = number of blocks with at least one attempt; sd = sample SD of the W block means (ddof 1); t = mean(m_b) / (sd / √W); p = P(T_{W−1} ≥ t) (DEC-021 Amendment 1 §5, as DEC-030 §1 and §1a cite it; `cap_pick_score.t_sf`).
- **Disclosure / QP to confirm:** #673's `day_t` is fed **block sums** (SOL per block), not per-attempt block means. The read tool must pass per-attempt block means as above, or quant-proof rules before merge that block sums meet DEC-030 and this bullet is changed before merge. QP's power tables and the in-sample day t (σ_d 0.869205 SOL) were computed on day totals [inferred from the units].

### 6.3 Gate and binding terms (all on all 4 cells) [pinned]

1. n ≥ 100 attempts.
2. ≥ 5 distinct UTC dates with attempts, a majority of them positive (date totals).
3. **Trade CI90 lower bound > 0**: 1,000 bootstrap draws, seed 1, 5th percentile of the resample means, **over the trades ordered by the key (T0 ascending, mid ascending)** (the order `xs_book` returns), with `np.random.default_rng(1)` and one `rng.integers(0, n, n)` call per draw (#673 `boot_lo`). The order key is part of the pin.
4. Total SOL > 0 after removing the top 3 trades.
5. Total SOL > 0 after dropping the best 24 h block (from W0).
6. Total SOL > 0 after dropping the best UTC date.
7. Day-level p ≤ 0.025/k (section 6.2).

The CI90 clause is unadjusted and on top (DEC-030 §1). The Bonferroni level only tightens the gate.

### 6.4 Verdicts

- **PASS:** no section 7.1 condition holds, and items 1–7 hold on all 4 cells. Reported as "forward simulated paper; not money made; leads only to the DEC-018 path and the owner's yes".
- **FAIL:** no section 7.1 condition holds, and any item fails on any cell.
- **NOT_DECIDABLE:** section 7.1.

## 7. NOT_DECIDABLE and the kill rule

### 7.1 NOT_DECIDABLE (p = 1, stays in k; the window is spent)

1. E has not occurred by `EXP027_CAP_END` (DEC-030 §1a).
2. Fewer than 100 trades on either fail-model leg at the read (DEC-030 §1a), with no extension.
3. The read is not completed by `EXP027_DEADLINE`.
4. A precount or read-tool refusal of section 8 that cannot be cleared outcome-blind (one outcome-blind re-walk of a bad hour is allowed before the read; its result stands).
5. More than 5% of the window's hours are bad after that re-walk (EXP-022 §10), or any hour in `[W0, E + 1 h)` is missing (FROZEN §4 refusal; stricter).
6. An A3 structure-monitor halt (`tools/pump_structure_monitor.py`, the six flags of EXP-022 §11) fires between W0 and E.
7. A pressure gap: any counted trade lacks its pressure-leg P&L (DEC-014, line 70).
8. E1 is not recorded with n ≥ 20 before the read (DEC-021 Am.1 §4).
9. The read's decision md5 for any mark differs from the count job's (PLAN §4).
10. An hour outside the allowlist is opened by the read (EXP-025 R-terms; checklist §1 j).

**Seals never turn a FAIL into NOT_DECIDABLE.** A breach (section 11) leaves the verdict as computed and labels the read compromised.

### 7.2 Kill rule

A FAIL **retires r4-b**: no retune, no refit, no change to K, stake or threshold, no re-read on any hours, no rescue hours (FROZEN:125; DEC-030 §3). A NOT_DECIDABLE also spends the window; r4-b is never re-filed on walk 2. A re-filing of any variant is a new book under a new EXP and, in slot 5, counts toward the 3-book cap.

## 8. Precount and read-tool refusals

### 8.1 Precount (count job, outcome-blind; re-checked by the read) — refuse if

- any hour in `[2026-10-16T01, E + 1 h)` is not sealed, strict-verified (`exp012_forward.hour_problems(strict=True)`) or sha256-matched, or its parsed rows differ from `verify.jsonl` `content[sub].rows`;
- the PDA match is < 95% of non-Mayhem completes;
- fewer than 100 hour marks have ≥ 10 live tokens;
- a UTC date has graduations but 0 decisions;
- an hour has unmeasurable SPH.

It also reports (report-only): null-`tx_index` tie groups and mixed slots (section 3.2), the V0-band share, rows dropped by the s0 + 600 s rule, LP-excluded pools, and live tokens / rows / finite scores per mark.

### 8.2 Read tool — refuse unless

- each pinned line matches once and this file is clean against HEAD;
- every code sha256 and model md5 of section 2.2 matches; E-a..E-d md5s are recorded; the decoder blobs equal walk 2's job ref;
- the E1 record exists with n ≥ 20; input-view sha256s and the `data/tries.jsonl` entry exist;
- only allowlisted hours are opened; a bad line refuses;
- an O_EXCL lock; `/data/mal/r4b/READS.jsonl` with started / completed / aborted; no second run; no CLI override; the verdict goes to stderr before any file write;
- fills join outcomes only after E + 1 h is sealed.

**Catalog.** The walk-2 Owner cell is "reserved", so `mal_catalog.check_read` denies the read today. Either sub-entry support is added to the catalog (a guard change that re-runs the formal refusal tests of EXP-024, EXP-025, `exp012_forward` and `forward_v_join`), or a pinned allowlist is used as EXP-022 §12 does (PLAN §4). **TO FILL before merge:** which.

## 9. Power (report-only; QP's simulations)

Conventions: each cell is P(full pass): all 4 cells, the larger p decides, the full gate plus both drop terms, 2,000 day resamples, seed 1. Effect s is a share of the deciding-stack net mean.

**QP ruling §3 (stack then with rent), k = 1:**

| Effect | W=7 | W=10 | W=14 | W=21 |
|---|---|---|---|---|
| 100% | .0945 | .1555 | .2335 | .3435 |
| 50% | .0470 | .0655 | .0780 | .0975 |
| central 37.9% | .0380 | .0530 | .0570 | .0730 |
| 0% (false pass) | .0200 | .0300 | .0250 | .0245 |

At k = 2, W = 21: .2225 at 100%, .0635 at 50%. The drop terms change nothing.

**QP follow-up §1b (the pinned stack: no rent, 505k sends), k = 1 / k = 2:**

| s | W=7 | W=10 | W=14 | W=20 | W=21 |
|---|---|---|---|---|---|
| 100% | .1865/.1005 | .2975/.1935 | .4560/.3255 | .6325/.4945 | .6625/.5175 |
| 50% | .0895/.0490 | .1460/.0870 | .2190/.1405 | .3095/.1980 | .3245/.2065 |
| 37.9% | .0790/.0420 | .1225/.0690 | .1810/.1080 | .2400/.1520 | .2495/.1575 |
| 0% (deciding edge) | .0445 | .0605 | .0700 | .0935 | .0915 |
| null | .0200 | .0300 | .0250 | .0220 | .0245 |

Means behind it: flat 2.2991% (1.3 s) and 2.2973% (1.9 s); pressure 1.7950% (1.3 s) and 1.8555% (1.9 s).

**160k leg, N = 20, no rent** (pressure means 2.0311% / 2.0916%): .7550/.6275 at 100%, .4235/.3045 at 50%, .3530/.2350 at 37.9%; null .0220. These exclude the lag shift, so they are a ceiling. N = 14, same stack: .5640 / .3010 / .2480.

**Central effect, k = 1** (QP-FU §3):

| Stack | N=14 | N=20 |
|---|---|---|
| No rent, 505k | .181 | .240 |
| No rent, 160k | .248 | .353 |
| With rent | .057 | .0725 |

**k-dependence.** k = 2 cuts every figure by about a third (QP-FU §3). Plan for k = 2 (QP §3).

**Owner-facing line.** Quant-proof's line, verbatim (QP, "Owner line", written on the first table, before the follow-up):

> "r4-b passed its exploration check on honest costs, but those are the days it was picked on, so it has zero out-of-sample trades and has not cleared the promotion gate. Even if its edge is real, a 3-week forward read passes about 7 to 34 % of the time (10 to 34 % if at least half the measured edge is real), so most likely it fails and is retired."

On the pinned stack of the follow-up (no rent, 505k, N = 20), the same sentence reads [from QP-FU §1b; **QP to confirm the wording**]: *"Even if its edge is real, a 20-day forward read passes about 24 to 63 % of the time with r4-b alone (about 15 to 49 % if a second push book registers); a book with no edge passes about 2 % of the time. Most likely it fails and is retired."*

## 10. Disclosures (DEC-030 §3; ledger rule 3)

### 10.1 EXP-025's non-owner read

EXP-025's non-owner read of `[2026-10-16T01, 2026-10-24T02)` is disclosed. Its Look 1 was NOT_DECIDABLE (R8, P5, 2026-10-16T04:51:17Z). Its end state is not yet recorded: "Look 2 refused" is recorded only after the R8 run after 2026-10-26T12:00Z (LAB_STATE Now; ledger (3a)). Until then its seals and non-owner reads stand; this file does not call EXP-025 ended. r4-b computes no C1-NF label, fill, exit or P&L and prices no C1-NF decision (EXP-025 §4 seal), and joins no synthetic class to any outcome.

### 10.2 The C1-NF shadow and monitor

The C1-NF shadow (MiScusi #658) and the monitor (#662) are running over walk-2 hours. **This file does not read their output**, and nor does any r4-b tool.

### 10.3 The 2026-10-16 low-severity breach

On 2026-10-16 a builder's scan read row TYPES and MINT COUNTS of `migrations-2026-10-16T01` and `migrations-2026-10-16T02` (forward-1002 directory; hours inside the Forward walk 2 row; ledger changelog, 2026-10-16, W = 2026-10-16T18:56Z). Quant-proof ruled no read compromised. **Those hours are before W0 and are never counted.** They are used as feature buffer (section 3.4), read from `forward-1016` only, and that scan's output is never reused.

### 10.4 Exploration evidence is in-sample for selection

- **Zero out-of-sample trades.** Every figure is from exploration days that were in-sample for selection: **the best of 6 l3 variants, about 25 hunts on this window, deflated Sharpe 0.6224** on the deciding 1.9 s stack (FROZEN:175; QP §header). Under R1 there is no backward screen (DEC-030 §2, R1 "Cost"); the walk-2 read alone carries the winner's-curse risk.
- **In-sample, the day-level test already fails** at 0.025: pressure 1.3 s t 1.9048, p 0.034; pressure 1.9 s p 0.0276 (QP §1, 27 traded days, after dropping 09-11 and mint 16894).
- **Deciding-stack in-sample cells** (with rent, dropped sample n 2,879) [measured, #673 = r4b-verify REPORT]: mean % / total SOL / CI90 lo SOL per trade / ex-top-3 / ex-best-day / day t — flat 1.3 s 1.605794 / 11.557703 / 0.001337 / 8.303159 / 8.988099 / 2.112063; flat 1.9 s 1.603981 / 11.54465 / 0.00133 / 8.290106 / 8.97681 / 2.110699; pressure 1.3 s 1.215049 / 8.745313 / 0.000836 / 5.971927 / 6.565793 / 1.901402; pressure 1.9 s 1.275503 / 9.18043 / 0.000969 / 6.40146 / 7.009824 / 2.003766.
- **fresh-0903** is −2.304792 SOL over 740 trades (QP §1, deciding stack); FROZEN reports −0.80917 SOL (deciding 1.9 s, 10-08 run), and the harness stack moved +0.78 → −0.14 between runs (section 2.4). Three figures, three stacks or runs; all negative or near zero on the deciding stack.
- **maxDD 3.149798 SOL**, more than the 2.5 SOL open (QP §1).
- **The noise-draw disclosure** of section 2.4.
- **Multiplicity:** DEC-014 m. This EXP adds 1 to m (Appendix A): r4-b's development read Aug–Sep exploration dates, counted conservatively as reading the 27 non-P1 dates.
- **Rugs:** 5.13% of positions lost ≥ 90% on the no-fail leg in development (FROZEN §7).
- **The 10-02 upgrade and 10-13 redeploy** are not in development.

### 10.5 Capital

The book holds up to **2.5 SOL open** (10 × 0.25 SOL). The owner's capital was 1.29 SOL (QP §5), so a live trial would need owner capital beyond that; DEC-020 governs size. **A smaller K or stake is a new book** (QP §5), never a variant of this one.

### 10.6 No claim

Nothing in this file says r4-b makes money. A PASS would be forward simulated paper, not money made, and leads only to the DEC-018 path and the owner's yes.

## 11. Seal during the window

- **Before the read**, no person, agent or job computes, opens or prints an r4-b outcome for any mark at or after W0: landing state, guard result, fill, exit state, proceeds, P&L, mean, CI, day or block sign, win or loss, from any source (walk 2, the tip tape, the live listener, RPC, a paper runner).
- **Allowed before the read:** the count job's outputs (section 4.2); hour counts (sealed, verified, bad); the precount; the A3 monitor's flags; the fee-arm verdict read of section 5.3 by a non-r4-b-outcome reader.
- **A breach** is recorded here, dated, and the read is reported compromised. It never turns a FAIL into a NOT_DECIDABLE. A compromised read cannot support a live request.

## 12. Source conflicts and how this file resolves them

| Conflict | Resolution |
| --- | --- |
| Checklist §1 c (stop at ≥ 100 trades on ≥ 5 dates, cap W0 + 336 h) vs DEC-030 §1a (≥ N blocks with attempts and ≥ A attempts, cap ≤ 21 d) | **DEC-030 wins** (PLAN §8 item 7). N = 20, A = 125, cap 21 d. |
| Checklist §2 (Holm across k) vs DEC-030 §1 (Bonferroni 0.025/k) | **DEC-030 wins**: Bonferroni. |
| Checklist §1 d (deadline E + 48 h) | Not in conflict with DEC-030; adopted. |
| PLAN §5 recommends ceil for k vs QP §2 np.round | **QP wins**: np.round (#673 already pins it). |
| PLAN §1/§5 SPH per decision hour vs QP §2 "slot_s per day" | Day value pinned (section 3.3), **QP to confirm** the aggregation. |
| PLAN §8 item 3 (H0 = 10-16T03) vs QP §2 (pools ≥ 10-16T01) | **QP wins**: 10-16T01, with the breach-hour disclosure (section 10.3). |
| #673 `day_t` on block sums vs DEC-021 Am.1 §5 (per-attempt block means) | DEC text pinned (section 6.2); **QP to confirm**. |
| FROZEN universe from graduation + 600 s vs QP D3 condition "decisions ≥ s0+600 s" | s0 + 600 s drop rule (section 3.4); **QP to confirm**. |
| PLAN §5 haircut product approximation vs #673 VERIFY formula | #673 formula pinned (section 5.1). |
| FROZEN:123 target n 3,002 vs rebuilt n 2,986 | Amended to rb2 (section 2.3), per QP §2. |
| FROZEN:104 halves bar | Report-only on walk 2 (section 5.4); QP's power tables carry no halves bar. |
| QP owner line (first table) vs follow-up stack | Both shown (section 9). |
| Timestamps: QP ruling "~20:30Z", follow-up "~21:00Z" vs `date -u` 19:58Z at the start of this writing | Recorded only; the ruling texts were read from the file as found. |

## 13. TO FILL

**Before merge:**
1. Code sha256: `<SHA256 at merge of #672>`, `<SHA256 at merge of #673>` (three files), `<SHA256 at merge of layout>`, `<SHA256 at merge of count>` (section 2.2).
2. E-d md5s (section 2.3).
3. rb2 prediction-key sha256s and rb2 trades sha256 (section 2.2).
4. TIERS sha256; decoder blob ids; input-view sha256s and the `data/tries.jsonl` entry.
5. The sell builder path and sha256 (section 5.2 (i)); else rent is deciding.
6. The LP-event detection tolerance (section 3.5).
7. The strand-class (iii) freeze-authority / Token-2022 data source (section 5.2).
8. The catalog route: sub-entry support or pinned allowlist (section 8.2).
9. The DEC-030 owner line and route; `EXP027_ROUTE` set accordingly (section 0.2).
10. Job #1036's extension (due about 2026-10-22T19:21Z) and a walker credit cap that covers to 2026-11-16T01 (PLAN §8 item 10), recorded in the Window record.
11. Quant-proof's rulings on every "QP to confirm" item: sections 3.2, 3.3, 3.4 (history, s0 + 600 s), 3.5, 3.6 (`f_c_v`), 4.2 (count job ranks), 5.1 (E1 scaling), 5.3 (lag floor), 6.2 (block means), 9 (owner line wording).

**Before the read (Window record, dated):**
12. `<SHA256 at merge of read>` with QP OK on its head.
13. The E1 record (path, sha256, n ≥ 20).
14. The fee-arm verdict record (path, sha256) if section 5.3 applies, recorded in the ledger before the read lock.

## Appendix A. Proposed ledger edit (Forward walk 2 Status cell; DEC-030 §2a)

DEC-030 is unmerged, so `docs/HOLDOUT_LEDGER.md` is **not edited in this draft**. Once DEC-030 merges with the owner's line, this text is added to the Forward walk 2 row's Status cell **in this PR**, with the bracketed values filled from the merge record, and this PR does not merge without it.

> **EXP-027 (r4-b, push-family book 1, DEC-030 slot 5), <merge date> ([EXP-027](../EXP/EXP-027-r4b-walk2-prereg.md); counted-window sub-entry under Rules item 5, not an owner):** W0 `<W0>` (the first full UTC hour after the merge at `<merge instant, date -u>`; never moved); stop rule: the first 24 h block boundary from W0 with at least 20 blocks holding an attempt and at least 125 attempts (`EXP027_STOP_RULE`, outcome-blind count job `tools/r4b_count.py`); window cap `<the earlier of W0 + 21 d and 2026-11-16T00>`; exit horizon 1 h (the window with its exit horizon ends by `<cap + 1 h>`, at or before 2026-11-16T01); k at registration `<k>`; route `<R1 | R2>`. Feature-buffer hours `[2026-10-16T01, W0)` are read for features only and never counted (2026-10-16T01 and T02 under the 2026-10-16 breach disclosure). Reads `/data/mal/blocks/forward-1016` only; universe pools created at or after 2026-10-16T01. Adds 1 to m (**m >= 16**; 18 on DEC-030 §2's count of 17). Stop instant: *(added when the stop rule fires)*. Section 5.3 fee-arm record: *(added before the read lock, or "none: 505k")*. First push-family read instant (freezes k): *(added when it happens)*.

Changelog line to add with it:

> - <date> (W = <date -u>; [EXP-027](../EXP/EXP-027-r4b-walk2-prereg.md), DEC-030 slot 5; no row's data was opened): **EXP-027 (r4-b) registered as push-family book 1 on Forward walk 2**, counted-window sub-entry in the Status cell (Rules item 5). W0 `<W0>`, cap `<cap>`, exit horizon 1 h, k `<k>`. m +1 (m >= 16). The Owner cell stays reserved.

## Window record

*(empty until merge; the manager appends dated lines from the merge record, the count file and the job records only)*

## Sources

- DEC-030 draft and DEC-021 Amendment 6 draft (branch `claude/dec030-walk2-push-slot`); HOLDOUT_LEDGER Rules item 5 (same branch) and the Forward walk 2 row; the 2026-10-16 changelog entries on `main`.
- `/data/mal/hunt-1008/push-1016/WALK2-PREREG-CHECKLIST.md`; `/data/mal/hunt-1008/laya-opt/FROZEN-r4-b.md`; `/data/mal/hunt-1008/push-1016/hunt6/r4b-verify/QP-RULING-1016.md` (both rulings); `/data/mal/hunt-1008/push-1016/hunt6/r4b-readtool/PLAN.md`; `/data/mal/hunt-1008/JUDGE-3.md:248`.
- PR #672 and PR #673 bodies; `tools/r4b_book.py` and `tools/r4b_frozen.py` at #673's head.
- EXP-022 (§0, §10, §11 structure), EXP-025 (§4 seal, breach amendment), EXP-026 (§0 structure), DEC-014 (line 70), DEC-019 Amendment 2 (Tests A and B), DEC-021 Amendment 1 §4–§5.
- No sealed data, walk-2 hour, forward-paper P&L, or canary, shadow or probe outcome record was opened to write this file.
