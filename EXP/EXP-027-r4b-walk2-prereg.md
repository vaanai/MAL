# EXP-027: r4-b on Forward walk 2 (push-family book 1, DEC-030 slot 5), pre-registration

**Draft. Not registered. Nothing has run.** Written 2026-10-16 (`date -u` 2026-10-16T19:58:25Z at the start of writing). To write it, no file under `/data/mal/blocks/` (forward-1002, forward-1002ev, forward-1016 or any sealed block), `/data/mal/exp012-forward`, or any shadow, canary, probe or walk output was opened. Revision 3 (2026-10-16, after `date -u` 22:53Z; consolidation after the tools merged on `origin/main` at `aef7bea`) pins the final module sha256s, the read tool's pinned lines, the E-a..E-d records, the per-program stranded rent, the Token-2022 allowlist disclosures and the H0 / W0 alignment; the two owner-dependent items are left as marked `TODO-OWNER` placeholders and the `r4b_read.py` sha256 as `TODO-after-E1-flip`. Revision 2 (2026-10-16, after `date -u` 20:08Z) applies quant-proof's third and fourth passes on draft head `30f82fb` (calendar stop, per-attempt block-mean t, null ordering and refusals, slot_s, D3, LP, strand-class source, owner sentence) and fills the #672 / #673 sha256s from `origin/main`. The inputs are text only:

| Input | sha256 at writing |
| --- | --- |
| [DEC-030](../DEC/DEC-030-walk2-push-slot.md) draft and DEC-021 Amendment 6 draft, branch `claude/dec030-walk2-push-slot` | (git blob on that branch) |
| `/data/mal/hunt-1008/push-1016/WALK2-PREREG-CHECKLIST.md` | `26cfd7f4bf22dba43b39744eaaba449f73f3fce30335e61ecdb61d07542fbce7` |
| `/data/mal/hunt-1008/laya-opt/FROZEN-r4-b.md` (cited `FROZEN:<line>`) | `cc626dde5c3e68222b1fcf066db19a6ce0a412a2e91507e941effd10d0aebc49` |
| `/data/mal/hunt-1008/push-1016/hunt6/r4b-verify/QP-RULING-1016.md`: ruling, follow-up, third pass and fourth pass (cited `QP §n`, `QP-FU §n`, `QP-3`, `QP-4 §n`) | `bf12e075f72087d984954b84e4806a522add905f48183391d809d5f3883904a5` (ruling and follow-up, revision 1); `209759e63fe0e62d2a46699ac1f5a88a510ecb9239033b2cf75b18cad28d69fa` (third and fourth passes appended, stamps corrected; revision 2 of this EXP) |
| `/data/mal/hunt-1008/push-1016/hunt6/r4b-readtool/PLAN.md` (cited `PLAN §n`) | `bb6a6876ad39fe35e8cce4b92cd7f7cfd840cf320248ff47ab998ab9f5ca3552` |
| `/data/mal/hunt-1008/JUDGE-3.md` (line 248 only) | `e54fa0ae0053c6480b00105a0c110ddc6d985471061a22e5b27af9540b5752bb` |
| PR #672 (features, E-a; merged `cf1eaac`) and PR #673 (core, E-b, E-c; merged `ea01143`): PR bodies and source on `origin/main` | (git; sha256s in section 2.2) |
| [EXP-022](EXP-022-cap-pick-part1-prereg.md), [EXP-025](EXP-025-c1nf-part1-prereg.md), [EXP-026](EXP-026-h5-boostclock-v2-prereg.md), [DEC-014](../DEC/DEC-014-holdout-ledger-and-multiplicity.md), [DEC-019](../DEC/DEC-019-execution-probe.md) Am.2, [DEC-021](../DEC/DEC-021-champion-challenger.md), [HOLDOUT_LEDGER](../docs/HOLDOUT_LEDGER.md) | (git, `origin/main` at `cf1eaac`) |

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
| **Expected outcome** | **FAIL.** On the pinned stack (per-attempt block-mean t, no rent, auto-stranded, 505k), at N = 20 blocks, QP puts P(full pass) at .2535 at the central effect and .6575 if all of the measured edge is real (k = 1); .1660 and .5265 at k = 2 (section 9). P(pass) with no edge .0195 (k = 1). |
| **Tools** | Builder `tools/r4b_features.py` (#672), pins/scorer/book `tools/r4b_frozen.py`, `tools/r4b_score.py`, `tools/r4b_book.py` (#673), adapter `tools/r4b_layout.py`, count job `tools/r4b_count.py`, read `tools/r4b_read.py` (PLAN §2, §7). |

**Labels.** [measured]: copied from a cited file that computed it; exploration arithmetic is measured arithmetic, not evidence of edge. [inferred]: reasoned. [pinned]: a design value this file fixes; not evidence. Items once marked "QP to confirm" were ruled on by quant-proof's third and fourth passes (QP-3, QP-4) and are written here as ruled (section 13 lists them).

## 0. Status, registration, route and pinned lines

### 0.1 When this file registers

It registers only when **all** of these hold, and it never registers otherwise:

1. **DEC-030 has merged** with the owner's dated line in its section 4 (question `q__jVaqpxB937kEg` is PENDING at writing; the owner line is `TODO-OWNER (q__jVaqpxB937kEg)`), quant-proof's OK on DEC-030's exact final head, and a reviewer verdict on the DEC-030 PR.
2. **This file merges** with quant-proof's OK on **this file's exact final head** and a **reviewer verdict on this PR**. The merge is a separate call with `--match-head-commit`.
3. The ledger edit of Appendix A is in this PR's final head (DEC-030 §2a: "The edit is made in that book's own pre-registration PR, which does not merge without it").
4. Every **TO FILL** item of section 13 marked "before merge" is filled in the final head, including `EXP027_E1_MODE` (section 5.1) and the `r4b_read.py` sha256 (section 2.2).
5. The merge instant is **not before 2026-10-18T02:00:00Z** and **not after 2026-10-26T23:59:59Z** (section 0.3).

### 0.2 Route (the owner's line in DEC-030 section 4 is PENDING)

This file is written for **route R1** (DEC-030 §2, R1: no backward-block PASS for push-family books; DEC-021 Amendment 5's "only through §2" overridden for push-family books).

> **CONDITIONAL, read before merge.**
> - **Under R2:** this file **cannot register until r4-b has PASSED a one-shot read on a reserved, unread backward block** under the promotion gate at p < 0.025/m with **m = 17** (DEC-030 §2, R2; DEC-014 per-block terms: that block's Part 1 merged first, one block kept unspent). That needs its own Part 1 (not this file), and the first push-family PR must list all 17 sources of m in the ledger or raise m. The pinned line `EXP027_ROUTE` is then changed to R2 before merge, and the PASS record is cited here by path and sha256. Note: the three candidate blocks predate the 2026-10-02 upgrade and the 2026-10-13 redeploy (DEC-030 §2).
> - **Under NO SLOT:** this file **does not register**. DEC-030 does not merge, slot 5 is not opened, and r4-b has no registrable read on walk 2. The file is then closed as withdrawn before counting, with no hour read.

### 0.3 W0 and the earliest merge

- **W0 [pinned].** The first full UTC hour strictly after this file's merge instant, by `date -u` (DEC-030 §1a: "the first full UTC hour after its pre-registration merges ..., never moved"). Hours before W0 are never counted.
- **W0 must be ≥ 2026-10-18T03:00Z (`EARLIEST_W0`).** The count job's create history starts at **H0 = 2026-10-16T03** (`tools/r4b_count.H0`, `tools/r4b_read.H0`), so the 48 h universe ramp completes at 2026-10-18T03 and no earlier mark has a complete universe (QP on #678, R6). This replaces the draft's 10-18T01, which assumed pools from 10-16T01 (section 12). **DEC-030 has no provision for a W0 later than the first full hour after merge**, so a later W0 cannot be declared. Therefore: **this file must not merge before 2026-10-18T02:00:00Z.** A merge at or after that instant (and before 03:00:00Z) gives W0 = 2026-10-18T03; the read tool refuses a W0 before `EARLIEST_W0`. **The merge window is [2026-10-18T02:00:00Z, 2026-10-26T23:59:59Z].**
- **Block alignment (advice, not a rule).** QP §3 recommends W0 at 00:00Z so the 24 h blocks are UTC dates. That happens only if the merge falls in the hour before a midnight (earliest: merge in `[2026-10-18T23:00Z, 2026-10-19T00:00Z)` gives W0 = 2026-10-19T00). If the merge falls elsewhere, the blocks start at W0's hour; the gate's date items still count UTC dates (section 6).
- **Last W0 [pinned].** The stop needs at least 20 blocks with attempts by the cap, so W0 + 480 h ≤ 2026-11-16T00, i.e. **W0 ≤ 2026-10-27T00**. If this file has not merged by 2026-10-26T23:59:59Z, it is withdrawn before counting; a re-filing is a new file on later hours.
- **Withdrawal** is possible only before W0. After W0 this book is never withdrawn, re-scoped or delayed except by a section 7.1 NOT_DECIDABLE condition.
- **Window record.** The manager appends the merge instant, W0, the cap instant and k at registration (dated, `date -u`, from the merge record only) under "Window record" at the end of this file, and in the ledger sub-entry, before W0.

### 0.4 Pinned lines

```
EXP027_BOOK_ID: r4-b
EXP027_FAMILY: DEC-030 slot 5, push-family book 1
EXP027_ROUTE: R1
EXP027_COUNT_START: W0 = the first full UTC hour strictly after this file's merge instant (date -u); never moved; refused if before 2026-10-18T03 (EARLIEST_W0; H0 = 2026-10-16T03)
EXP027_LAST_W0: 2026-10-27T00
EXP027_STOP_RULE: calendar stop E = W0 + 480 h; the read requires at least 20 blocks from W0 holding an attempt and at least 125 attempts, otherwise NOT_DECIDABLE with no extension
EXP027_EXIT_HORIZON_H: 1
EXP027_CAP_END: cap = E (= W0 + 480 h, at or before 2026-11-16T00)
EXP027_DEADLINE: E + 48 h
EXP027_ALPHA: 0.025/k, Bonferroni, k frozen at the first push-family read
EXP027_DECISION_P: the largest p over the 4 cells (flat, pressure) x (1.3 s, 1.9 s) of the one-sided day-level t over 24 h blocks from W0, empty blocks dropped, df = W - 1
EXP027_SEND_FEE_LAMPORTS: 505000, or 160000 only under section 5.3
EXP027_RENT_DECIDING: 0 per filled trip under section 5.2 conditions (i)-(iii); the per-program rent of section 5.2 per filled trip (2074080 for Token-2022 or an unknown program, 2039280 for an SPL Token mint) if condition (i) is unmet
EXP027_WALK_DIR: /data/mal/blocks/forward-1016
EXP027_SELL_BUILDER: tools/probe_live.py@b188a40bbaa358e3b3a1e3fdf01dace12fba80aca4e3327b11db08280285bce3
EXP027_E1_MODE: TODO-OWNER (q_Z3dGvhO1wJ87wg; A or B)
EXP027_TIERS_SHA256: 0f33121627ac7647562a097de4d7386312bc3747ad6a4688935bc5f7cee9b208
EXP027_E_A_MD5: 3e9bc6225377927682d80b9441391a96
EXP027_E_B_MD5: e4056fb11902481dbf60c2f14f07f7c9
EXP027_E_C_MD5: 54052f798013a71e3a9f40e0d6830ac1
EXP027_E_D_MD5: 93a38d6ac19b7d7f49a787026b7655ec
EXP027_SHA256_R4B_FROZEN: 07cdb41abef9bd9061b0e9515150cbfd848de014b267b30309c6101c15c63a0b
EXP027_SHA256_R4B_SCORE: 737e40260bf6011d15f764e08d4a1b87afd33b62262d0600e42208fcf9d50c06
EXP027_SHA256_R4B_BOOK: 9c20de4073ea22f6cf61cb9528302fa22edbc64fb16685490284c4e5fb6b7513
EXP027_SHA256_R4B_FEATURES: 40b627b4a218e84e70f7e27a9ece7789d0626f15fd8fb1cc0e6259e5b494ea62
EXP027_SHA256_R4B_LAYOUT: b83605d82b63f7131afdcde821a0035e81ba961c2108ff71af164936a362d49b
EXP027_SHA256_R4B_COUNT: 55a1c05fa1cf29b980f550489bdfce232e2f04e0c3c68952683377e2dddcbaf3
EXP027_SHA256_R4B_READ: TODO-after-E1-flip
```

**Pinned lines the read tool also requires, added to the Window record after merge (not here).** `tools/r4b_read.py` `REQUIRED_KEYS` also lists `EXP027_W0`, `EXP027_K`, `EXP027_DECODER_REF`, `EXP027_INPUT_VIEWS`, `EXP027_TRIES_ENTRY`, `EXP027_MINT_ACCOUNTS`, `EXP027_FEE_ARM` and `EXP027_A3_RECORD`, and `EXP027_E1_RECORD` only if `E1_MODE` is `"record"`. They name things that do not exist before merge (W0 and k come from the merge record; the mint-account file, the A3 record and the fee-arm record are written later). They are therefore **not** written as a line in the body above: each is appended, once, as a line of the form `KEY: value` (the `path@sha256` form for files) to the **Window record** at the end of this file, dated, in a commit before the read, so that each matches `^KEY: ` exactly once. The read tool refuses until they exist and the file is clean against HEAD. `EXP027_FEE_ARM` is `NONE` if no fee-arm record is pinned (505k).

- **Format.** Each line matches `^<KEY>: (.*\S)\s*$` exactly once in this file, as written (`tools/r4b_read.exp_lines`). A value that is `TODO-...` is a placeholder: it must be replaced before merge, and the read tool refuses a placeholder (a sha256 pin is checked against `HEX64`, an md5 against `HEX32`, the E1 mode against the tool's constant). The read tool refuses unless each does and unless this file is clean against HEAD. The values never change after merge.
- **Meaning.** `E` is the stop instant, fixed by the calendar (QP fourth pass §5, "STOP_MODE: calendar"). The last entry mark is E − 1 h; every open position is sold at the E mark; hours are read to E + 1 h (the exit horizon). The cap equals E. If, at the read, fewer than 20 blocks hold an attempt or fewer than 125 attempts are counted, the book is NOT_DECIDABLE (p = 1, stays in k), with no extension. Since W0 ≤ 2026-10-27T00, the window plus its exit horizon ends by 2026-11-16T01 (DEC-030 §1a, §2a). This calendar stop is outcome-blind by construction. It is DEC-030 §1a's stop rule with this book's declared N = 20, A = 125 and cap W0 + 480 h (stricter than §1a's floors N ≥ 7, A ≥ 100, cap ≤ 21 d): the first boundary with 20 blocks holding attempts cannot come before W0 + 480 h, and the cap is that boundary.
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
| Repo copies | `models/r4b/` (the five boosters, features.txt, the fixture): `sha256sum` on `origin/main` (`ea01143`) equals each sha256 above, all seven files | #673; checked 2026-10-16 |
| harness/specs/r4-b.py | md5 `6121a64775870521effbf7c34f4d00b8`, sha256 `e7761004e588911264d149555ccf2cbe04cb2f043ccb0a9f4566f090ba0f7c08` | FROZEN §1 |
| harness/specs/S1.json (parent) | md5 `c976e08d6252c2f5e7674d1b19203ca3`, sha256 `477bae8b9c9d39cd15c287c4d626a4ce6ab94e8c9f361b1d2e44a874c6092d92` | FROZEN §1 |
| 02_build.py (post-order-fix) | md5 `535ac7062ab0eb15c2d08bceb9175710` | FROZEN §1 |
| judge/final_fit.py, judge/fit_lgb.py | md5 `ff229a86cdcd8f03c020017a8c0498b6`, `568b2ad97ebea3718b0e1149bb11f4e3` (record only; never re-run) | FROZEN §2 |
| lightgbm | **4.7.0** (numpy 2.5.3 in the scoring venv) | FROZEN §2; PLAN §2 |
| Score | raw mean over seeds 1..5 of `Booster.predict(X float64)` | FROZEN §1 |
| `tools/r4b_features.py` (#672 `cf1eaac`; asof mint filters and jw clamp #679 `8981e3d`) | sha256 `40b627b4a218e84e70f7e27a9ece7789d0626f15fd8fb1cc0e6259e5b494ea62` (`EXP027_SHA256_R4B_FEATURES`); the `cf1eaac` value `b533668b…` is superseded | `sha256sum` on `origin/main` at `aef7bea` |
| `tools/r4b_frozen.py` (#673 `ea01143`; later PRs through #680 `ef5107c`) | sha256 `07cdb41abef9bd9061b0e9515150cbfd848de014b267b30309c6101c15c63a0b` (`EXP027_SHA256_R4B_FROZEN`); the `ea01143` value `c5a3b145…` is superseded | `sha256sum` on `origin/main` at `aef7bea` |
| `tools/r4b_score.py` (#673) | sha256 `737e40260bf6011d15f764e08d4a1b87afd33b62262d0600e42208fcf9d50c06` (`EXP027_SHA256_R4B_SCORE`) | `sha256sum` on `origin/main` at `aef7bea` |
| `tools/r4b_book.py` (#673 `ea01143`; `t_block` on per-attempt means #677 `1eb5d42`; entry/exit-invalid guards, C7, C8 #680 `ef5107c`) | sha256 `9c20de4073ea22f6cf61cb9528302fa22edbc64fb16685490284c4e5fb6b7513` (`EXP027_SHA256_R4B_BOOK`); the `ea01143` value `31a127b0…` is superseded. The per-attempt-mean `t_block` of section 6.2 is in this file | `sha256sum` on `origin/main` at `aef7bea`; QP fourth pass §1 |
| `tools/r4b_layout.py` (adapter, #674 `081e95b`) | sha256 `b83605d82b63f7131afdcde821a0035e81ba961c2108ff71af164936a362d49b` (`EXP027_SHA256_R4B_LAYOUT`) | `sha256sum` on `origin/main` at `aef7bea`; PLAN §7 task 4 |
| `tools/r4b_count.py` (count job, #676 `3b02035`) | sha256 `55a1c05fa1cf29b980f550489bdfce232e2f04e0c3c68952683377e2dddcbaf3` (`EXP027_SHA256_R4B_COUNT`) | `sha256sum` on `origin/main` at `aef7bea`; PLAN §7 task 6 |
| `tools/r4b_read.py` (read tool, #678 `8e274b7`; allowlist #683 `ff2e398`; per-program rent #684 `aef7bea`) | **`TODO-after-E1-flip`** (`EXP027_SHA256_R4B_READ`). It changes once more: the follow-up PR that flips `E1_MODE` per the owner's answer. `aef7bea` has `8d579194d0f2f63cf8bec3479f41ed4959874467a7c4933d42b1503354f787b4` and is **not** the pin. The final value is filled here, with a QP delta pass on that head, before merge | `sha256sum` on `origin/main` after the flip PR; PLAN §7 task 7 |
| PumpSwap fee tiers | `FEE_TIERS` of `tools/r4b_frozen.py` (= `tools.paper_curve_math.PUMPSWAP_SOL_FEE_TIERS` = harness `hlib.TIERS`); sha256 of its canonical JSON (`tools/r4b_read.fee_tiers_sha256`, `[list(t) for t in FEE_TIERS]`, separators `,` `:`) `0f33121627ac7647562a097de4d7386312bc3747ad6a4688935bc5f7cee9b208` (`EXP027_TIERS_SHA256`) | QP §5 |
| Decoder blobs | walk 2's job ref (`forward_v_join` PINNED_BLOBS, `pins --ref`); `EXP027_DECODER_REF` is a Window-record line (section 0.4) | PLAN §4 |
| Input views, tries | every input view's `path@sha256` (`EXP027_INPUT_VIEWS`, `;`-separated) and the `data/tries.jsonl` entry for r4-b (`EXP027_TRIES_ENTRY`): Window-record lines (section 0.4) | DEC-021 Am.5; PLAN §4 |
| rb2 reproduction keys | sha256 of the 5 rb2 prediction keys and of `results/r4-b-rb2` trades `<TO FILL>` | QP §2 |

**Book constants [pinned]** (QP §5; `tools/r4b_frozen.py`): K = 10; 0.25 SOL (250,000,000 lamports) per position; hour marks (3,600 s); ties to the lower `mid`; no minimum score, no hysteresis, no stops; universe 10 min to 48 h after graduation, traded in the last 10 min; PDA pool; event V.

**Cost constants [pinned]:** section 5.

### 2.3 Equivalence proofs (recorded before registration)

| Proof | What | Result | Status |
| --- | --- | --- | --- |
| **E-a** | Port fed l3 `ut/` hours 2026-09-23T00..09-25T06 reproduces the fixture X (617 rows, `mid`, `T`, `sd` equal, X float32 byte-equal) | md5(X bytes) **`3e9bc6225377927682d80b9441391a96`** = fixture [measured, #672] | done (#672, merged `cf1eaac`) |
| **E-b** | The 5 boosters on fixture X reproduce seed1..5 and `bag_score` | max abs diff 0.0; 617 rows, 52 with bag > 0 [measured, #673]. `EXP027_E_B_MD5` = md5 of the float64 `bag_score` bytes (617 values) `e4056fb11902481dbf60c2f14f07f7c9`, equal for the recomputed bag and the fixture's [measured, this revision, `tools.r4b_score.check_fixture` all `*_equal` True] | done (#673, merged `ea01143`) |
| **E-c** | The xs engine, fed the rebuilt walk-forward bag (float32 ranking), reproduces the rebuilt **`results/r4-b-rb2`** trades (n **2,986**) | trades table (harness columns, `%.9g`) text-identical, md5 **`54052f798013a71e3a9f40e0d6830ac1`**; exits rank 2,956, gap 20, end 10; the four deciding cells equal the r4b-verify REPORT at 6 decimals [measured, #673] | done (#673, merged `ea01143`) |
| **E-d** | md5 decision-equivalence replay on clean-view explore-0814 w7→w4, `[08-14T12, 08-21T12)`, walker layout (static-map V from `pool_v_0909.json`, seg_end 08-28T12), `grads_rule` "frozen", order (mid, slot, txi, evi, frn): I and X bytes equal l3 `feat/<day>.npz`; Qe/Be/spot/ssb/nearby equal `cache/l3/dec`; marks equal `cache/l3/marks`; PDA-vs-V-band mint list empty; event-V injection test gives the same md5 | **Scoped PASS, raw FAIL**; record and scope sentence in section 2.3.1. `EXP027_E_D_MD5` = md5 of the report file `93a38d6ac19b7d7f49a787026b7655ec` | done (#681, merged `64231f4`; MiScusi job #1082 at `e504803`) |

**FROZEN:123 is amended [pinned].** FROZEN:123's target "reproduces `results/r4-b/trades.csv.gz` (n 3,002)" **cannot be done** (QP §2: the 10-08 cache is deleted). The reproduction target is **`results/r4-b-rb2` (n 2,986), reproduced from the 5 rb2 prediction keys (sha256-pinned, section 2.2), md5 `54052f798013a71e3a9f40e0d6830ac1` of the trades table** (#673 E-c). The 10-08 `results/r4-b` file (n 3,002) stays on disk as a record and is not a target.

#### 2.3.1 E-d record and scope [pinned]

**Scope sentence (quant-proof, PR #681 comment 6225999636).** At `e504803` the r4-b port is md5-equivalent to l3 on 2026-08-14..18 (explore-0814, MiScusi job #1082, `/data/mal/hunt-1008/push-1016/hunt6/r4b-equiv-d/full3/report.json`, sha256 `70ded6de33fa96a3ca55410d9b1ccd7567627eb80a006a5bd87bd2f0745c8898`, rc = 0) for every row of mints outside the null-`tx_index` tie set. **Raw full-row equivalence FAILS and is not claimed; tie mints are not covered.** The script and wrapper are `scripts/research/r4b_equiv_d.py` and `scripts/research/r4b-equiv-d.sh` (merged with #681 at `64231f4`; record sha256s with `sha256sum` on `origin/main`); the run is under a `systemd-run` scope with `MemoryMax` at most 24 GB.

**What passed (report.json `scoped.status` PASS, `scoped.problems` empty).** Meta: 5,321 members in both, 0 mismatches (mid, mint, pool, mslot, mbt, s0, v, day, creator hash, cbt); 0 mints with a pool other than the PDA; only-ours and only-l3 both 0. I (mid, T, sd): md5 equal on all 5 days. Non-tie X, dec and marks md5 equal on all 5 days, `differing_mints_not_in_tie_set` 0 every day, marks (mid, t) keys equal. Event-V injection: same md5 in both paths (`injection_equal` true).

**Per-day non-tie md5s (port = l3), rows kept / excluded, and the tie-mint list sha256** [measured, #681 body; the tie list is the port's own set of groups whose content differs]:

| day | I | X | dec | marks | rows kept | rows excluded | tie-mint list sha256 |
|---|---|---|---|---|---|---|---|
| 08-14 | `61cf38b7c066b5efef3a03f205b77a9d` | `eec4728b4b29d69948b84fbe7e48aa47` | `1973194377e4a3e9e2642da87076533d` | `909dda8efe35163f89df28533e8ab48e` | 96,808 | 14,349 | `c979bc32a200e00dda49359fb7a72b5a089fbca03079c96a428cd815a370b440` |
| 08-15 | `6fe3ce30145cc2f16fe1b178c188c578` | `a53d80b4288d6ca1d5673b1e8f760450` | `df9d908d6da958c106885df88831a11f` | `e6f9c006fafbdfba443f3607656edeb4` | 149,505 | 24,967 | `09777a6556b406798dab86abf3ea9870b568baedfded08b47463c0530ee8b6eb` |
| 08-16 | `bf9b939cc64eb118c6f85a261cd2d191` | `e8552062d6733e7a9a993285fd417a24` | `99b5a5a2913de02b589a325792f52b2e` | `2d5b610006439616440d7ff20c4cfc9f` | 161,550 | 18,583 | `a9a67aaf12b8c4f3fa2f16b235aea82db13538c87747266401c57f761fbcab27` |
| 08-17 | `d35111ff6242951d73e4fd984f9be4ef` | `6cfea17c10dfd886d7cc2760436f10d3` | `5edd34338d94cecb4e8208e28373b5c7` | `e97a4b771a4b65d944728d76d0ebdd72` | 216,769 | 29,268 | `4fb307177f1ad858c58abe8eca7a904dd6db3373cbea3ec2db8aa7d9c05d2eac` |
| 08-18 | `de524ffc4808527c0e82c38909b0f0a4` | `f8b4b81071405c6a57e17a742e5c18f6` | `46a6317682cce5615a7896e4da71ee72` | `d0c279fa8ac94c693d47ee988adc815a` | 208,621 | 30,309 | `ebdb7f766d6d747f44c554a054c984bd9fb065ad7e74e43e9a3df3a3facf1304` |

Non-tie rows kept: 833,253 of 950,729 decision rows (87.6%). **`txi_values == [-1]` on every day** (report.json `features.days[*].ties.txi_values`; QP finding 1: `tie_info` does not restrict itself to `tx_index` −1, so this field is part of the record): every tie group is a null-`tx_index` group. Tie mints per day: in-day 6, 22, 55, 15, 50; over each day's 80-hour window 28, 56, 66, 55, 69 (window tie groups 72, 182, 154, 101, 152). Port total md5s: I `c948e16ac06bcfde0a9cce847188c1dc`, X `769ffff6897f80a2899c3a183fae6707`, dec `605463fbf999bfedde5d2dc15cf6f041`, marks `6f191dad9eab3b790109dd2fa305a003`.

**Raw (not claimed).** On the whole of l3 the X, dec and marks md5s differ every day: 1,083 X rows, 172 dec rows and 181 marks rows (of 950,729), on 75 differing mints (14, 18, 11, 19, 13 per day), every one inside the tie set. Cause: l3 sorted with `order by mid, slot, txi, evi` only and DuckDB returns tied rows in a different order from run to run (the same cause as the n 3,002 vs 2,986 gap, section 2.4). The port breaks ties by `frn`, which is total.

**Tie-mint coverage disclosure.** Mints with a tie group are **10.3–14.3% of decision rows per day** and **7.4–8.5% of marks**; equivalence between the port and l3 is **unproven there** and no claim is made for them. For the forward read the order key is total (section 3.2), so the forward ranking is deterministic; what the port does on a tie group is "frn order inside a null-`tx_index` slot", and **the assumption that `frn` order equals chain order inside such a slot is unverified** (the scoped PASS does not depend on it). E-e (block_time and V) was not run: the D0 role refuses both.

**m_grads as-of impact, exploration (QP §2 asked for it before registration) [measured, #672, report-only, in-sample for the boosters]:** 184,485 hour-mark rows, 761 marks; rows where `m_grads` changes 1,847 (1.0%); max |Δ bag score| 0.011352, mean over changed rows 0.000421; 2 rows flip the score's sign; top-10 members changed per mark: **0 at all 761 marks**. Approximation disclosed in #672: graduation set = l3 meta members and first-print slot = `meta.s0`, not a PDA derivation; non-member graduations not assessed.

### 2.4 Reproduction gap: the development book is one noise draw (QP §2, disclosure)

- Prints with a null `tx_index` tie in `order by mid, slot, txi, evi`, and DuckDB orders the tie differently on each run (`/data/mal/hunt-1008/swarm-1013/w2-r4b-repro/REPORT.md` §1). #673 also found duplicate `(mid, slot, txi, evi)` keys in `ut/` hours before 09-22 (e.g. 195 in 2026-09-10T05).
- **The development book is a single noise draw.** Between runs, **700 entries were only in the old run and 684 only in the new.**
- Between runs, **maxDD went from 0.087 to 0.183 of peak**, **the worst 7 days from −0.31 to −0.99 SOL**, and **fresh-0903 from +0.78 to −0.14** [measured, QP §2].
- **The fixture covers only 09-25, which has 0 tie groups**, so E-a and E-b cannot see the tie problem.
- The forward score comes from md5-frozen boosters, so the gap does not bias the forward read (QP §2). It does mean every development number in section 10.4 is one draw of a noisy pipeline.

## 3. Forward builder rules [pinned]

These bind the adapter, builder, count job and read tool. They are QP §2's rules, as completed by QP-3 and QP-4, unless marked otherwise.

### 3.1 m_grads as of the decision

A graduation counts toward `f_m_grads_1h` and `f_m_grads_10m` at decision time T only if **mbt < T and its canonical PDA pool's first print has slot < sd(T)** (`grads_rule = "asof_pda_first_print"`, #672). Raw migration events are not counted; never-printed pools stay out.

**As-of mint filters and the jw clamp [pinned] (#679, QP comment 6224294297).** In forward mode the read calls the builder with `grads_rule="asof_pda_first_print"` and `mint_filters="asof"` and the jw clamp on (`jw = min(jw, j)`: a later print with a null `block_time` cannot enter a window); the defaults of `tools/r4b_features.py` are `"frozen"` (training, fixture, E-a, E-d) and are never used for a forward decision. The frozen mint filters (fewer than 3 prints, any reserve ≤ 0) look at the whole tape and would be whole-tape lookahead in a forward mode; the as-of version uses pre-mark prints only. An entrypoint test pins the flags at the read's call sites. **Disclosure:** in exploration the as-of filters change nothing (every exploration mint has at least 42 prints; 0 rows differ), so that 0 is structural; in forward data the frozen filters may flatter, and the as-of version avoids that. **Report-only in the read:** as-of-only and frozen-only row counts per date and per mark, how many as-of-only rows entered a top-10, and the P&L of those trades, shown separately and never removed. QP checked every feature for other whole-tape lookahead (none), and `return_state` never feeds a forward decision.

### 3.2 Total order of prints

Rows are ordered totally by **(slot, coalesce(tx_index, −1), event_index, file_row_number)**: null `tx_index` sorts **first** within the slot, exactly as training (`01_extract.py`: `coalesce(tx_index,-1)`), and `file_row_number` is the **one tie-breaker** (QP-4 §7; QP-3 on #674; `forward_order` as merged in `cf1eaac`). The precount reports null-`tx_index` tie groups (`null_txi_tie_groups`). **A mixed (mint, slot) count > 0** (a mint's slot holding both null and non-null `tx_index`) **refuses the read** (section 8.1). Exploration had 0 mixed slots over all 36 l3 days [measured, #672].

### 3.3 Slot time and k

- **Rounding:** **np.round** (half to even; at 200 ms slots 1.3 s = 6.5 slots → 6), `K_ROUNDING = "round"` (QP §2; #673). k = np.round(lat / slot_s), for lat = 1.3 s and 1.9 s. Not switched on results.
- **Disclosure, k tie at exactly 200 ms.** At `slot_s` exactly 0.200 s, 1.3 / 0.2 = 6.5 exactly, so k = 6 (half to even), and 1.9 / 0.2 evaluates in float64 to 9.499999999999998, so **k = 9** (the exact half would round to 10). Both are what the tool does (`k_for`); a `slot_s` slightly off 0.200 moves k by one slot, and the measured `slot_s` per date (below) is what is used. Not corrected, not tuned.
- **slot_s per UTC date** (QP-4 §2): slot_s(date) = **3600 / median over the date's hours of SPH_h**, with SPH_h from `cap_pick_score.measure_hour_sph` (PLAN §1). k uses np.round. An hour with unmeasurable SPH is a refusal.
- **Exit lag:** ceil(0.55 s / slot_s − 1e-9) slots, asserted ≥ 2 (3 at 200 ms).

### 3.4 Universe and hours

- **Pools created at or after H0 = 2026-10-16T03:00Z only** (QP on #676/#678: the count job's create history starts at 10-16T03; QP §2's original bound was 10-16T01), so no forward-1002 hour (and no D2 hour) is ever read, and neither 2026-10-16T01 nor T02 is opened by the read. Membership: PDA match and V0 in [17.5e9, 17.7e9] lamports (PLAN §1; the share outside the band is reported). **A pool with missing V0 is a non-member** (QP-3; QP-4 §7); the read refuses only if the missing-V0 share exceeds 2% on a read date (section 8.1). **A member with any null-V (`vq`) print refuses the read** (section 8.1).
- **s0 window (disclosure, QP-3).** s0 is the first print of the PDA pool in `[mslot, mslot + 7300)` slots. At 200 ms slots that is about **1,460 s**, against about **1,940–3,040 s** in training (slots ran 265–416 ms then; QP on #674, correcting its own earlier figure of 2,200–2,900 s). A pool whose first print falls later than the window has no s0 and is not a member. Not corrected.
- **Directory.** Only `/data/mal/blocks/forward-1016` (the Forward walk 2 row). Never `forward-1002` or `forward-1002ev`, including any file there named for an hour at or after 2026-10-16T01.
- **Feature-buffer hours** `[2026-10-16T03, W0)` (**H0 = 2026-10-16T03**) are read for features, membership and the clock only, never counted, and only after this file merges. **2026-10-16T01 and 2026-10-16T02 are not opened by the read or the count job** (the allowlist starts at H0); the 2026-10-16 breach disclosure (section 10.3) is still stated. Since W0 ≥ 2026-10-18T03, every pool in the universe has had its first 48 h before W0, so the since-seed features have full history at W0. **Allowlist [pinned]:** `[2026-10-16T03, E + 3h)`: counted marks `[W0, E − 1 h]`, the exit hour (E + 1 h exclusive) and the 2 extra trade hours the adapter loads after it (`EXTRA_TRADE_HOURS = 2`, `r4b_count.trade_end`). The read opens no other hour (section 7.1.10).
- **History before H0 = 2026-10-16T03 [PLAN §8 item 3; confirmed by QP-4 and QP on #678].** For a token created before 2026-10-16T03 whose pool was created after it, create-derived constants (`f_c_*` from bonding history, creator history) are NaN where their inputs lie before 2026-10-16T03; creator history (`f_cre_*`) uses walk-2 creates (from H0) only. This is a train/serve skew (training saw full history) and is disclosed, not corrected.
- **Decisions in the first 600 s of the pool [confirmed, QP-4 §3].** To meet the D3 condition (section 3.6), a decision row with T < block_time(s0) + 600 s is dropped before ranking (s0 = the first print of the PDA pool). The count of dropped rows is reported. FROZEN's grid starts at graduation + 600 s, so this can only remove rows.

### 3.5 LP Deposit/Withdraw [QP-4 §4]

The decoder does not see PumpSwap LP Deposit/Withdraw (PLAN §8 item 5). **There is no exclusion rule** (QP-4 §4): a quarter to a third of mints show reserve discontinuities, mostly tape gaps, so an exclusion would cut the universe on noise. Entry and mark states use each print's own pre-trade reserves and event V, which is exact. **The detection tolerance is "none"**: no pool is excluded or force-sold for a discontinuity. Discontinuity counts (consecutive prints where the next print's pre-trade reserves differ from the previous print's implied post-trade reserves) at **> 1e-6 and > 1%** relative are **reported inside the read only**, never before it.

### 3.6 The H5 / D3 domain (QP ruling, quoted verbatim; supersedes JUDGE-3.md:248)

DEC-030 §3 excludes the H5 domain from push-family books "unless quant-proof rules otherwise in writing before registration". Quant-proof's written ruling (QP §4(b)), verbatim:

> "QP's ruling: feature-only use by the frozen scorer at decisions ≥ s0+600 s is not D3 use. This holds only if no feature is a synthetic class or BOOST flag, and the read reports nothing restricted to [s0, s0+360)."

- **This ruling supersedes JUDGE-3.md:248** ("... the since-seed features still carry pool-open information, so it also stays out of walk 2.").
- **Conditions, as this file applies them:** (a) decisions at T ≥ block_time(s0) + 600 s only (section 3.4); (b) the 63 names in `features.txt` contain no synthetic-class feature and no BOOST flag [inferred from the names; `f_c_v` is the pool's V0 constant, confirmed by QP-4 as not a BOOST flag]; (c) the read reports no quantity restricted to `[s0, s0+360)`; (d) no synthetic class is joined to any r4-b outcome.
- Because QP ruled this is not D3 use, the alpha stays in slot 5; it does not go to the H5 family (DEC-030 §3).

## 4. Data, window, stop rule, count job

### 4.1 Window

- **Counted window:** entry marks in `[W0, E − 1 h]`; hours read `[2026-10-16T01, E + 1 h)` (feature buffer before W0, counted from W0, exit horizon after E).
- **Blocks:** 24 h blocks from W0; block b holds attempts with entry mark T0 in `[W0 + 24h·b, W0 + 24h·(b+1))`.
- **Calendar stop E = W0 + 480 h; N = 20, A = 125 checked inside the read; cap = E; exit horizon 1 h** (QP-4 §5 "STOP_MODE: calendar"; QP §3 and QP-FU §3 for N, A). These are stricter than DEC-030 §1a's floors (N ≥ 7, A ≥ 100, cap ≤ 21 d), as §1a allows a book to declare before W0.
- **Expected attempts:** about 103 per day [inferred from FROZEN §6 and PLAN §4]; exploration attempts per day were 20 / 115 / 140 (min / median / max), CV 0.293 [measured, QP-4 §1]. So about 2,000 at E; N binds, not A.

### 4.2 Attempts and the count job

- **Attempt:** a buy triggered at an hour mark T0 in `[W0, E − 1 h]` for a top-10 token not held at T0, guarded buys included.
- **Count job** (`tools/r4b_count.py`, an hourly host-side MiScusi job, 12 GB cap; PLAN §4). Because the stop is by calendar (QP-4 §5), the count job **computes no features, scores, ranks or attempts** (this settles PLAN §4's open ruling on post-entry prices of held tokens: they are never loaded before the read). It opens only allowlisted, sealed, strict-verified, sha256-matched hours and writes, to `/data/mal/r4b/counts.jsonl`, hour health (sealed, verified, bad), the structure precount items of section 8.1, and per mark the decision rows `(mint, T, sd)` with their md5. It never loads a price, side or reserve column of a row at slot ≥ sd(t) (only slot and block_time for the clock), and computes no landing state, guard, fill, exit or P&L. **It is outcome-blind and is not a "read"; it does not freeze k** (DEC-030 §1).
- **N and A** (blocks with attempts, attempts) are computed **inside the read**. Fewer than 20 or fewer than 125 is NOT_DECIDABLE, with no extension.
- **The stop instant** E = W0 + 480 h is written in the ledger sub-entry at registration (Appendix A); the read records the N and A it found.

### 4.3 The read and its deadline

- **One read, no interim looks** (DEC-030 §1a). It runs after E + 1 h is sealed and verified, the read tool is merged with QP OK on its head (sha256 in the Window record), E1 is recorded (section 5.1), and the section 5.3 record is in the ledger or its fallback applies.
- **Deadline:** `EXP027_DEADLINE`. A read not completed by then is NOT_DECIDABLE, and the window is spent (checklist §1 d).
- Fixed-instant steps (the count job, the read) run as host-side MiScusi jobs, never as session crons alone.

## 5. Cost stack (deciding, both latencies)

### 5.1 Deciding items [pinned]

| Item | Deciding value | Source |
| --- | --- | --- |
| Send fee | **505,000 lamports per send**, on the buy and the sell; a guarded buy pays one | FROZEN §4; `EXP027_SEND_FEE_LAMPORTS`; 160,000 only under section 5.3 |
| Rent | **0** per filled trip under section 5.2 (i)-(iii); the per-program rent of section 5.2 (2,074,080 Token-2022 or unknown, 2,039,280 SPL Token) if (i) is unmet | QP-FU §1a; QP on #684; `EXP027_RENT_DECIDING` |
| Entry | **1.3 s and 1.9 s, both gating**; x = sd + k, state before the first print with slot ≥ x + 1, END bound | FROZEN §4; #673 `mint_states` |
| Guard | buy reverts if exec price > **1.15 ×** decision spot: P&L −one send, no rent, no haircut, not "filled" | FROZEN §4; #673 |
| Exit | the mark state at clock_slot(t) + exit lag + 1 (section 3.3), END bound, at least 2 slots; past the loaded hours, the derived final state (`FINAL_FEE` 0.0125) | FROZEN §4; PLAN §5 |
| Pricing | PumpSwap PRE-trade reserves incl. per-print event V; fee tier on (Q incl. V, B) per side; own buy re-injected; own sell impact | FROZEN §4 |
| Live haircut | tokens received / 1.002608 (entry +26.08 bps) and proceeds × (1 − 0.0016) (sell −16 bps), the VERIFY formula as #673 implements it | DEC-021 §1, Am.1 §4 |
| E1 penalty | **mode `EXP027_E1_MODE`, `TODO-OWNER (q_Z3dGvhO1wJ87wg; A or B)`** (section 5.1.1). Scale to 0.25 SOL is fixed at 5 in code (`E1_STAKE_LAMPORTS` 50,000,000; the record cannot change it) | DEC-021 Am.1 §4; DEC-030 §3 |
| Flat fail leg | 0.85·pnl − 0.15·send | DEC-021 §1 |
| Pressure fail leg | p = σ(c + 0.8·ln(1+ssb) + 0.35·ln(1+nearby/1e9)), c refit per cell so mean p over the cell's fills = 0.289; P&L = (1−p)·pnl − p·send; ssb = buys in slot x; nearby = buy SOL over slots [x − round(2/slot_s), x] | DEC-021 §1; #673 |
| Strand class (iii) | section 5.2; auto-stranded is the deciding version | QP-FU §1a; QP-3 |

### 5.1.1 E1 mode (owner-dependent; NOT decided in this revision)

QP ruled (2026-10-16 ~21:3xZ) that E1 cannot run now: the tip tape follows the Forward walk row's seals (ledger 2026-10-15 entry, "never a back door") and E1 would price post-s0 reserves of pools in `[10-02T15, 10-16T01)` (D2) and probably the first 360 s of migrate entries (D3). The pinned line `EXP027_E1_MODE` takes one of these, by the owner's answer to `q_Z3dGvhO1wJ87wg` (decide by 2026-10-18T00Z), and the tool's `E1_MODE` constant must equal it (a one-line follow-up PR with a QP delta pass; the `r4b_read.py` sha256 of section 2.2 is taken after it):

- **A.** Haircut only; E1 (b) NOT_APPLICABLE ("not measured is not measured zero"). Power at N = 20, k = 1 at 100 / 50 / 37.9 / 0 % of the effect: .6575 / .3245 / .2535 / .0195.
- **B** (QP's recommendation). Each filled, non-stranded trip loses max(haircut, E1_FIXED_LAMPORTS), **E1_FIXED_LAMPORTS = 1,920,110 = 5 × 384,022**: the mean live-minus-sim P&L gap of the faa3192 probe calibration, n = 8, at 0.05 SOL (DEC-021:54), times the ×5 scale to 0.25 SOL. **n = 8 is disclosed.** Power: .5390 / .2245 / .1650 / .0195. B needs a dated DEC-021 amendment with the owner's approval, written outcome-blind before W0 (DEC-021:108 makes n < 20 NOT_DECIDABLE otherwise).
- **record** (the tool's current constant, `E1_MODE = "record"`): the draft's version, an E1 record with n ≥ 20; not runnable (QP ruling above).

Section 9's deciding table is computed on the draft's stack; under B the figures are the lower ones above. If the owner picks neither by the merge, the file does not merge (section 0.1 item 4).

### 5.2 Rent: the deciding stack is pinned without rent (QP-FU §1a, conditions verbatim)

Quant-proof ruled "Rent: YES, the deciding stack is pinned without rent", on the evidence that the sell and `close_token_account` run in one transaction (61 of 61 probe trips refunded, net 0; 19 more calibration trips net 0). The conditions, verbatim:

> - (i) The EXP pins the sha256 of the sell builder that would trade r4-b, and requires the close in the same transaction. Without it, rent comes back into the deciding stack.
> - (ii) The rent leg stays as report-only.
> - (iii) Rent can strand on three kinds of position. Each is charged the full stake, the buy send, no sell send, and 2,039,280 rent:
>   - an unpriced exit;
>   - a base mint with a freeze authority or a Token-2022 extension that blocks close (for example withheld transfer fees);
>   - simulated proceeds below one send fee.
>   The forward builder flags the first two from data known at decision time.

- **(i) as pinned here.** Sell builder: **`tools/probe_live.py`**, sha256 `b188a40bbaa358e3b3a1e3fdf01dace12fba80aca4e3327b11db08280285bce3` (`EXP027_SELL_BUILDER`, `path@sha256`, relative to the repository root at `origin/main`). Its sell path builds `tx.sell_instructions(...)` plus `close_token_account(base_ata, user, base_tp)` into **one** message (`Message.new_with_blockhash(ixs, ...)`), where `base_tp` is the mint's own token program, so a Token-2022 account is sold and closed in the same transaction. This is the file that produced the 61-of-61 refunds quoted above. **This choice of file is the manager's to confirm at merge** (a different builder would replace the path and sha256; the file must perform the sell and the ATA close in one transaction). **Evidence for in-tx close on Token-2022:** `ARTIFACTS/lab/probe-final-2026-10-07.md:234` found no token accounts remaining under either the Token or the Token-2022 program (`getTokenAccountsByOwner`, slot 454261472). **Not verified: the token program of each probe trip** (a trip-by-trip check was not done); this is disclosed. If the pin is not valid at merge, `EXP027_RENT_DECIDING`'s second clause applies (the per-program rent per filled trip, deciding), and power falls to about .0725 at the central effect (QP-FU §3, N = 20, k = 1, computed on block sums).
- **(iii) as pinned here.** A strand-class trip's deciding P&L = −250,000,000 − one send − the stranded rent of the mint's token program, below (no sell send). **Auto-stranded is the deciding version** (QP-3): simulated proceeds below one send fee count as stranded, and the threshold follows the deciding send fee (505,000, or 160,000 under section 5.3); the no-auto version is report-only (#673 `round_trip(auto_stranded=True)`). An unpriced exit is stranded. **Freeze authority and Token-2022 extensions: source pinned (QP-4 §7).** A host-side MiScusi job (`tools/mint_account_fetch.py`) fetches **each member mint account once** by RPC (`getAccountInfo`, jsonParsed), records `fetch_ok`, `owner_program`, the freeze authority and the Token-2022 extensions, and writes them with the `fetch_slot` to a sha256-pinned file (`EXP027_MINT_ACCOUNTS`, a Window-record line). A mint with a freeze authority or a close-blocking extension (for example withheld transfer fees) is strand-class. **A failed fetch makes the mint strand-class; it is never a refusal.** This is a **structure read made by RPC**, not a walk-2 outcome; its RPC credits are recorded (job, credits, calls) in the Window record. QP estimates about 5.1% of fills are rug-class, so (iii) costs about 0.04 pp per trade.
- **(ii)** The report-only rent leg (per filled trip, priced by the mint's token program as below) is reported, never deciding.

**Rent by token program [pinned] (QP verdict on #684 at `2b3d537`, comment 6226181818, merged `aef7bea`).** Stranded and report-only rent depend on the mint's token program.

- **Token-2022, and any mint whose program is unknown: 2,074,080 lamports** = (170 + 128) × 6960, a Token-2022 ATA with ImmutableOwner. "Unknown" means a failed fetch, a malformed record, another owner, or a mint missing from the mint-account file.
- **A successfully fetched SPL Token mint: 2,039,280 lamports** = (165 + 128) × 6960.

Both figures are higher than the rent the chain charges now. On 2026-10-16, `getMinimumBalanceForRentExemption` returned 1,513,840 for 170 bytes and 1,488,440 for 165 bytes; the live probe also paid exactly 1,513,840 per buy on all 61 trips (`ARTIFACTS/lab/probe-final-2026-10-07.md:163`). **The higher figures were kept on purpose: they are conservative, and this read never re-bases them downward.** What that costs: each stranded Token-2022 position is charged 560,240 lamports over chain rent (0.224% of stake); over the old 2,039,280 figure the difference is 34,800 lamports, about 0.0007 pp per trade at QP's 5.1% rug-class share; the report-only rent leg charges 2,074,080 per filled Token-2022 trip and so overstates rent by up to 560,240 per trip. **Deciding rent stays 0 per filled trip under (i)-(iii).** The constants `RENT_STRANDED_T22` and `RENT_STRANDED_SPL` are in `tools/r4b_read.py`; `tools/r4b_frozen.py` keeps 2,039,280 (the E-c replay uses it; E-c md5 `54052f798013a71e3a9f40e0d6830ac1` is unchanged).

**Token-2022 allowlist and mint-account disclosures [pinned] (QP on #682 and #683).**

1. **Allowlist and its provenance.** `TOKEN2022_EXT_ALLOWLIST = {metadataPointer, tokenMetadata}`, fixed in `tools/r4b_read.py` and **fixed at W0**: after W0 it never changes, and any other extension type is denied by default (a Token-2022 mint is not strand-class only if every extension is in the set, there is no freeze authority, and the fetch succeeded). Provenance: an outcome-blind census of N = 300 exploration mints (`tools/mint_account_fetch.py`, mode census, 300 `getAccountInfo` calls, about 300 Helius credits, 0 errors, fetch slots 457794410–457794548): 295 are Token-2022 with exactly `metadataPointer` + `tokenMetadata`, 5 are classic SPL Token, 0 have a freeze authority, 0 have a mint authority. Files under `/data/mal/hunt-1008/push-1016/hunt6/r4b-readtool/mint-census/`: `summary.json` sha256 `5b8c4f16e190b2b7b1a9fed4a0386ef25ca96b0e4ed405c9410f94da7521e6f1`, `mints.jsonl` `faf269ff3a52ec3791ef1931c4cf6226589a0ac780a89c119eebf749f3ffc6d1`, `input_mints.txt` `b308e72774979d4784256b203940150cf85f51a6afe38e301c67f0286228e2fd`. A pre-W0 report-only `members` run may revise the allowlist (pre-W0 only, with QP) if a material share shows a new extension or a freeze authority.
2. **The `r4b_read.py` sha256 pin is taken at or after the allowlist's merge (#683 `ff2e398`)**, here `TODO-after-E1-flip` (section 2.2).
3. **The read reports** the strand-class share, the failed-fetch share, and the Token-2022 vs SPL Token strand split, all from the post-E members fetch.
4. **Post-E fetch timing.** The binding `members` run is after E, once, on the full member list for `[H0, E]` (member list from the structure-only layout build: mint and `mbt` only), as a MiScusi job into a fresh directory; its sha256 is pinned in `EXP027_MINT_ACCOUNTS` and its credits recorded. A fetch after the decision **would miss a freeze authority revoked between decision and fetch**; for pump mints that is nil (no freeze authority is ever set), and it is stated rather than assumed away.
5. **Rent understatement unless the per-program rent applies:** it applies (#684), so the SPL-only 2,039,280 pin would have understated stranded Token-2022 rent by 34,800 lamports; that is closed by the text above. Mint authority is report-only and is not a strand rule.

### 5.3 Conditional 160k send leg (QP-FU §2, exactly)

The deciding send fee becomes **160,000 lamports per send** (155,000 priority + 5,000 base) **only if all** hold:

1. **DEC-019 Amendment 2 PASS** (DEC-019:235): **Test A and Test B both hold** (A: landing lower bound > −5 pp; B: buy-lag upper bound < +0.5 slot).
2. The PASS is read from **the fee-arm's final verdict record, pinned by path and sha256** (`<path>`, `<sha256>`, **TO FILL when it exists**).
3. It is read **by someone who has opened no r4-b outcome**.
4. It is **recorded in the ledger (Forward walk 2 Status cell, this book's sub-entry) before r4-b's read lock is written.**

**Fallback:** a FAIL, a NOT_DECIDABLE, a missing record, or a verdict after the read start all leave the send fee at **505,000**. There is no partial version.

**Lag shift [pinned].** Under the 160k leg, both entry latencies take the fee-arm's measured buy-lag shift: Δ = **Test B's one-sided 95% upper bound** (slots) of mean(test) − mean(control) (QP chose the upper bound). k = np.round(lat / slot_s + max(0, Δ)) for lat = 1.3 s and 1.9 s. The floor at 0 (a negative bound is not credited) is confirmed by QP-4.

**Disclosure:** the fee-arm measured buys right after graduation, and carrying that over to a book that does not race is an assumption.

### 5.4 Report-only legs

55,000 per send; the rent leg (per filled trip, by token program: 2,074,080 Token-2022 or unknown, 2,039,280 SPL Token); 4.0 s entry; 5-slot exit; no-fail; the l3 frozen-model xs10 for comparison; drop-best-mint; the share of positions losing ≥ 90% (rugs); daily-mean CI90 lo, days positive, maxDD, the worst 3 days; the window's two halves (FROZEN's slot-era halves bar, FROZEN:104, does not apply to walk 2 and is replaced by this report-only item, PLAN §5); the trade-level and 10,000-draw bootstrap p (DEC-030 §1: day clustering breaks them).

## 6. Decision

### 6.1 Cells

Four cells: {flat 15%, pressure scale 1} × {1.3 s, 1.9 s}, all on the deciding stack of section 5.

### 6.2 Decision statistic [pinned]

**The one-sided day-level t over 24 h blocks from W0, empty blocks dropped, df = W − 1; the larger p of the 4 cells decides; pass iff that p ≤ 0.025/k** (`EXP027_DECISION_P`, `EXP027_ALPHA`).

- Per cell: **m_b = mean SOL per attempt in block b** (the per-attempt block MEAN, DEC-021:118, Amendment 1 §5; QP-4 §1); W = number of blocks with at least one attempt; sd = sample SD of the W block means (ddof 1); t = mean(m_b) / (sd / √W); p = P(T_{W−1} ≥ t) (`cap_pick_score.t_sf`). **Block sums (SOL per block) are report-only.**
- **Code change required before the read tool's QP.** `tools/r4b_book.py` at `ea01143` feeds `day_t` the block **sums** (`t_block` in `book_stats`). `book_stats` must get a pinned `t_block` on per-attempt block means (sums kept as a report-only field) in its own PR, with tests and QP OK, before the read tool is submitted to quant-proof. The changed file's sha256 replaces the `ea01143` value in section 2.2.

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

1. At the read, fewer than 20 blocks from W0 hold an attempt, or fewer than 125 attempts are counted (`EXP027_STOP_RULE`; no extension).
2. Fewer than 100 trades on either fail-model leg at the read (DEC-030 §1a), with no extension.
3. The read is not completed by `EXP027_DEADLINE`.
4. A precount or read-tool refusal of section 8 that cannot be cleared outcome-blind (one outcome-blind re-walk of a bad hour is allowed before the read; its result stands).
5. More than 5% of the window's hours are bad after that re-walk (EXP-022 §10), or any hour in `[W0, E + 1 h)` is missing (FROZEN §4 refusal; stricter).
6. An A3 structure-monitor halt (`tools/pump_structure_monitor.py`, the six flags of EXP-022 §11) fires between W0 and E.
7. A pressure gap: any counted trade lacks its pressure-leg P&L (DEC-014, line 70).
8. E1 is not recorded with n ≥ 20 before the read (DEC-021 Am.1 §4).
9. The read's decision-row md5 (`(mint, T, sd)`) for any mark differs from the count job's (PLAN §4).
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
- an hour has unmeasurable SPH;
- **a mixed (mint, slot) count > 0**: any mint whose slot holds both null and non-null `tx_index` prints (QP-4 §7);
- **a v0_missing share > 2% on any read date**, the denominator being non-Mayhem, PDA-matched pools with an s0 (QP-3; QP-4 §7). Below that, a pool with missing V0 is a non-member;
- **a member with any null-`vq` print** (null event V on a member pool's print; QP-3; QP-4 §7).

One tie-breaker throughout: (slot, coalesce(tx_index, −1), event_index, file_row_number) (section 3.2).

It also reports (report-only): null-`tx_index` tie groups (section 3.2), the V0-band share, the v0_missing share per date, rows dropped by the s0 + 600 s rule, and live tokens and decision rows per mark. Reserve-discontinuity counts (section 3.5) are reported inside the read only.

### 8.2 Read tool — refuse unless

- each pinned line matches once and this file is clean against HEAD;
- every code sha256 and model md5 of section 2.2 matches; E-a..E-d md5s are recorded; the decoder blobs equal walk 2's job ref;
- the E1 record exists with n ≥ 20; input-view sha256s and the `data/tries.jsonl` entry exist; the mint-account file of section 5.2 (iii) exists and matches its pinned sha256;
- only allowlisted hours are opened; a bad line refuses;
- an O_EXCL lock; `/data/mal/r4b/READS.jsonl` with started / completed / aborted; no second run; no CLI override; the verdict goes to stderr before any file write;
- fills join outcomes only after E + 1 h is sealed.

**Catalog.** The walk-2 Owner cell is "reserved", so `mal_catalog.check_read` denies the read today. Either sub-entry support is added to the catalog (a guard change that re-runs the formal refusal tests of EXP-024, EXP-025, `exp012_forward` and `forward_v_join`), or a pinned allowlist is used as EXP-022 §12 does (PLAN §4). **TO FILL before merge:** which.

## 9. Power (report-only; QP's simulations)

Conventions: each cell is P(full pass): all 4 cells, the larger p decides, the full gate plus both drop terms, 2,000 day resamples, seed 1. Effect s is a share of the deciding-stack net mean; the missing part comes off in SOL per trade.

**Deciding table: the pinned stack (per-attempt block-mean t, no rent, auto-stranded, 505k), N = 20** (QP-4 §1):

| Effect | k=1 | k=2 |
|---|---|---|
| 100% | .6575 | .5265 |
| 50% | .3245 | .2265 |
| central 37.9% | .2535 | .1660 |
| null | .0195 | .0110 |

**k-dependence.** k = 2 cuts every figure by about a third (QP-FU §3; the table above: .2535 → .1660 at the central effect). Plan for k = 2 (QP §3).

**160k leg.** The 160k figures have **not yet been recomputed on per-attempt means**; QP-4 §6: "The 160k figures must be rerun on means before they are quoted." None is quoted here.

**Superseded tables (kept for the record, not for decisions).** All were computed with the day t on block sums:
- QP §3 (stack then with rent), k = 1, W = 7 / 10 / 14 / 21: 100% .0945 / .1555 / .2335 / .3435; 50% .0470 / .0655 / .0780 / .0975; 37.9% .0380 / .0530 / .0570 / .0730; null .0200 / .0300 / .0250 / .0245.
- QP-FU §1b (no rent, 505k, no auto-stranded), W = 20, k = 1 / k = 2: 100% .6325 / .4945; 50% .3095 / .1980; 37.9% .2400 / .1520; null .0220.
- QP-3 (no rent, auto-stranded, 505k), N = 20, k = 1 / k = 2: 100% .6160 / .4825; 50% .2985 / .1855; 37.9% .2255 / .1460; null .0220 / .0125.

**Owner-facing sentence (QP-4 §6, exact):**

> "r4-b has zero out-of-sample trades and has not cleared the promotion gate; its exploration result comes from the days it was picked on. On the pinned stack (505,000-lamport sends, rent refunded, stranded positions charged in full), a 20-day forward read passes about 25 % of the time at the judge's shrunk estimate of the edge and about 66 % if the whole measured edge is real (about 17 to 53 % if a second push book registers). A book with no edge passes about 2 % of the time. A FAIL retires the book, and unless nearly all of the measured edge is real, FAIL is the likely outcome."

## 10. Disclosures (DEC-030 §3; ledger rule 3)

### 10.1 EXP-025's non-owner read

EXP-025's non-owner read of `[2026-10-16T01, 2026-10-24T02)` is disclosed. Its Look 1 was NOT_DECIDABLE (R8, P5, 2026-10-16T04:51:17Z). Its end state is not yet recorded: "Look 2 refused" is recorded only after the R8 run after 2026-10-26T12:00Z (LAB_STATE Now; ledger (3a)). Until then its seals and non-owner reads stand; this file does not call EXP-025 ended. r4-b computes no C1-NF label, fill, exit or P&L and prices no C1-NF decision (EXP-025 §4 seal), and joins no synthetic class to any outcome.

### 10.2 The C1-NF shadow and monitor

The C1-NF shadow (MiScusi #658) and the monitor (#662) are running over walk-2 hours. **This file does not read their output**, and nor does any r4-b tool.

### 10.3 The 2026-10-16 low-severity breach

On 2026-10-16 a builder's scan read row TYPES and MINT COUNTS of `migrations-2026-10-16T01` and `migrations-2026-10-16T02` (forward-1002 directory; hours inside the Forward walk 2 row; ledger changelog, 2026-10-16, W = 2026-10-16T18:56Z). Quant-proof ruled no read compromised. **Those hours are before W0 and are never counted.** They are used as feature buffer (section 3.4), read from `forward-1016` only, and that scan's output is never reused.

### 10.4 Exploration evidence is in-sample for selection

- **Zero out-of-sample trades.** Every figure is from exploration days that were in-sample for selection: **the best of 6 l3 variants, about 25 hunts on this window, deflated Sharpe 0.6224** on the deciding 1.9 s stack (FROZEN:175; QP §header). Under R1 there is no backward screen (DEC-030 §2, R1 "Cost"); the walk-2 read alone carries the winner's-curse risk.
- **In-sample day-level t, two stacks.** With rent and on block sums, the test fails at 0.025: pressure 1.3 s t 1.9048, p 0.034; pressure 1.9 s p 0.0276 (QP §1, 27 traded days, after dropping 09-11 and mint 16894). On the pinned stack (per-attempt block means, no rent, auto-stranded, 505k), pressure 1.3 s gives t 2.9459, p 0.0034 (QP-4 §1). Both are in-sample for selection and are not evidence.
- **In-sample cells on the 10-16 VERIFY stack** (505k, with rent, day t on calendar dates; not the pinned stack; dropped sample n 2,879) [measured, #673 = r4b-verify REPORT]: mean % / total SOL / CI90 lo SOL per trade / ex-top-3 / ex-best-day / day t — flat 1.3 s 1.605794 / 11.557703 / 0.001337 / 8.303159 / 8.988099 / 2.112063; flat 1.9 s 1.603981 / 11.54465 / 0.00133 / 8.290106 / 8.97681 / 2.110699; pressure 1.3 s 1.215049 / 8.745313 / 0.000836 / 5.971927 / 6.565793 / 1.901402; pressure 1.9 s 1.275503 / 9.18043 / 0.000969 / 6.40146 / 7.009824 / 2.003766.
- **fresh-0903** is −2.304792 SOL over 740 trades (QP §1, deciding stack); FROZEN reports −0.80917 SOL (deciding 1.9 s, 10-08 run), and it moved +0.78 → −0.14 between runs (section 2.4, stack as in the repro report). Three figures, three stacks or runs; all negative or near zero on the deciding stack.
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
- **Allowed before the read:** the count job's outputs (section 4.2); the mint-account structure fetch of section 5.2 (iii); hour counts (sealed, verified, bad); the precount; the A3 monitor's flags; the fee-arm verdict read of section 5.3 by a non-r4-b-outcome reader.
- **A breach** is recorded here, dated, and the read is reported compromised. It never turns a FAIL into a NOT_DECIDABLE. A compromised read cannot support a live request.

## 12. Source conflicts and how this file resolves them

| Conflict | Resolution |
| --- | --- |
| Checklist §1 c (stop at ≥ 100 trades on ≥ 5 dates, cap W0 + 336 h) vs DEC-030 §1a (≥ N blocks with attempts and ≥ A attempts, cap ≤ 21 d) | **DEC-030 wins** (PLAN §8 item 7). Calendar stop E = W0 + 480 h with N = 20, A = 125 checked in the read, cap = E (QP-4 §5). |
| Checklist §2 (Holm across k) vs DEC-030 §1 (Bonferroni 0.025/k) | **DEC-030 wins**: Bonferroni. |
| Checklist §1 d (deadline E + 48 h) | Not in conflict with DEC-030; adopted. |
| PLAN §4 open ruling: may the count job compute ranks from post-entry prices of held tokens? | Settled by the calendar stop: the count job computes no features, scores, ranks or attempts (section 4.2). |
| PLAN §5 recommends ceil for k vs QP §2 np.round | **QP wins**: np.round (#673 pins it). |
| PLAN §1/§5 SPH per decision hour vs QP §2 "slot_s per day" | **QP-4 §2**: 3600 / median of the date's hourly SPH (section 3.3). |
| PLAN §8 item 3 (H0 = 10-16T03) vs QP §2 (pools ≥ 10-16T01) | **QP wins**: 10-16T01, with the breach-hour disclosure (section 10.3). |
| #673 `day_t` on block sums vs DEC-021 Am.1 §5 (per-attempt block means) | **QP-4 §1**: per-attempt means decide; sums report-only; `book_stats` must change before the read tool's QP (section 6.2). |
| FROZEN universe from graduation + 600 s vs QP D3 condition "decisions ≥ s0+600 s" | s0 + 600 s drop rule, confirmed by QP-4 §3 (section 3.4). |
| #672 PR text (null `tx_index` sorted LAST after a reviewer fix) vs training's coalesce(−1) | **QP-4 §7**: nulls FIRST; the merged `forward_order` (`cf1eaac`) sorts nulls first; mixed (mint, slot) > 0 refuses. |
| LP Deposit/Withdraw: QP §4(d) "exclude or refuse" vs QP-4 §4 | **QP-4 wins**: no exclusion, tolerance "none", discontinuity counts report-only in the read (section 3.5). |
| PLAN §5 haircut product approximation vs #673 VERIFY formula | #673 formula pinned (section 5.1). |
| FROZEN:123 target n 3,002 vs rebuilt n 2,986 | Amended to rb2 (section 2.3), per QP §2. |
| FROZEN:104 halves bar | Report-only on walk 2 (section 5.4); QP's power tables carry no halves bar. |
| QP owner line (first table) vs later stacks | QP-4 §6's exact sentence is used (section 9); the earlier line and tables are superseded. |
| 160k power (QP-FU §2, QP-3) on block sums | Not quoted until rerun on means (QP-4 §6). |

## 13. TO FILL

**Before merge:**
1. Code sha256: `<SHA256 at merge of layout>` (`tools/r4b_layout.py`, #674, QP CHANGES at `b5a2d45`) and `<SHA256 at merge of count>` (`tools/r4b_count.py` and wrapper) (section 2.2). #672 and #673 are filled from `origin/main`.
2. E-d md5s (section 2.3).
3. rb2 prediction-key sha256s and rb2 trades sha256 (section 2.2).
4. TIERS sha256; decoder blob ids; input-view sha256s and the `data/tries.jsonl` entry.
5. The sell builder path and sha256 (section 5.2 (i)); else rent is deciding.
6. The catalog route: sub-entry support or pinned allowlist (section 8.2).
7. The DEC-030 owner line and route; `EXP027_ROUTE` set accordingly (section 0.2).
8. Job #1036's extension (due about 2026-10-22T19:21Z) and a walker credit cap that covers to 2026-11-16T01 (PLAN §8 item 10), recorded in the Window record.

**Before the read tool's QP:**
9. The `book_stats` change to a pinned per-attempt-mean `t_block` (section 6.2), merged with QP OK; its `tools/r4b_book.py` sha256 replaces the `ea01143` value in section 2.2.
10. The 160k power rerun on per-attempt means, if the 160k leg is to be quoted (section 9).

**Before the read (Window record, dated):**
11. `<SHA256 at merge of read>` with QP OK on its head.
12. The E1 record (path, sha256, n ≥ 20).
13. The fee-arm verdict record (path, sha256) if section 5.3 applies, recorded in the ledger before the read lock.
14. The mint-account file of section 5.2 (iii) (job id, sha256, RPC credits used).

**Settled by QP (no longer open):** LP tolerance "none" (section 3.5); slot_s rule (3.3); null ordering and refusals (3.2, 8.1); D3 drop rule (3.4); history NaN (3.4); `f_c_v` (3.6); count-job ranks (4.2, by the calendar stop); E1 ×5 (5.1); lag floor 0 (5.3); strand-class source (5.2); block means (6.2); owner sentence (9).

## Appendix A. Proposed ledger edit (Forward walk 2 Status cell; DEC-030 §2a)

DEC-030 is unmerged, so `docs/HOLDOUT_LEDGER.md` is **not edited in this draft**. Once DEC-030 merges with the owner's line, this text is added to the Forward walk 2 row's Status cell **in this PR**, with the bracketed values filled from the merge record, and this PR does not merge without it.

> **EXP-027 (r4-b, push-family book 1, DEC-030 slot 5), <merge date> ([EXP-027](../EXP/EXP-027-r4b-walk2-prereg.md); counted-window sub-entry under Rules item 5, not an owner):** W0 `<W0>` (the first full UTC hour after the merge at `<merge instant, date -u>`; never moved); calendar stop E = W0 + 480 h = `<E>` (`EXP027_STOP_RULE`); the read requires at least 20 blocks holding an attempt and at least 125 attempts, else NOT_DECIDABLE with no extension; window cap = E; exit horizon 1 h (the window with its exit horizon ends by `<E + 1 h>`, at or before 2026-11-16T01); k at registration `<k>`; route `<R1 | R2>`. Feature-buffer hours `[2026-10-16T01, W0)` are read for features only and never counted (2026-10-16T01 and T02 under the 2026-10-16 breach disclosure). Reads `/data/mal/blocks/forward-1016` only; universe pools created at or after 2026-10-16T01. Adds 1 to m (**m >= 16**; 18 on DEC-030 §2's count of 17). N and A found at the read: *(added by the read)*. Section 5.3 fee-arm record: *(added before the read lock, or "none: 505k")*. First push-family read instant (freezes k): *(added when it happens)*.

Changelog line to add with it:

> - <date> (W = <date -u>; [EXP-027](../EXP/EXP-027-r4b-walk2-prereg.md), DEC-030 slot 5; no row's data was opened): **EXP-027 (r4-b) registered as push-family book 1 on Forward walk 2**, counted-window sub-entry in the Status cell (Rules item 5). W0 `<W0>`, E = cap `<W0 + 480 h>`, exit horizon 1 h, k `<k>`. m +1 (m >= 16). The Owner cell stays reserved.

## Window record

*(empty until merge; the manager appends dated lines from the merge record, the count file and the job records only)*

## Sources

- DEC-030 draft and DEC-021 Amendment 6 draft (branch `claude/dec030-walk2-push-slot`); HOLDOUT_LEDGER Rules item 5 (same branch) and the Forward walk 2 row; the 2026-10-16 changelog entries on `main`.
- `/data/mal/hunt-1008/push-1016/WALK2-PREREG-CHECKLIST.md`; `/data/mal/hunt-1008/laya-opt/FROZEN-r4-b.md`; `/data/mal/hunt-1008/push-1016/hunt6/r4b-verify/QP-RULING-1016.md` (both rulings); `/data/mal/hunt-1008/push-1016/hunt6/r4b-readtool/PLAN.md`; `/data/mal/hunt-1008/JUDGE-3.md:248`.
- PR #672 and PR #673 bodies; `tools/r4b_book.py` and `tools/r4b_frozen.py` at #673's head.
- EXP-022 (§0, §10, §11 structure), EXP-025 (§4 seal, breach amendment), EXP-026 (§0 structure), DEC-014 (line 70), DEC-019 Amendment 2 (Tests A and B), DEC-021 Amendment 1 §4–§5.
- No sealed data, walk-2 hour, forward-paper P&L, or canary, shadow or probe outcome record was opened to write this file.
