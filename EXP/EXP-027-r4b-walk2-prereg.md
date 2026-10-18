# EXP-027: r4-b on Forward walk 2 (push-family book 1, DEC-030 slot 5), pre-registration

**Pre-registration. It registers at its merge under section 0.1 (the Window record's `EXP027_MERGE_INSTANT` records the instant). Nothing had run at merge.** Revision 8 (2026-10-17, after QP-FINAL-675 at `9772d55`, merged with `origin/main` `aad7bd9`) applies its blocking items 1 to 3: the power figures are the E1 mode B figures, the last W0 is 2026-10-26T22, and five statements that would be false once the body is frozen are corrected. Revision 7 (2026-10-17, `date -u` 2026-10-17T04:00Z onward, merged with `origin/main` `1b93610`) fills the three open placeholders: `EXP027_E1_MODE: B` (owner 2026-10-17; DEC-021 Amendment 7, `fc8c522`), the DEC-030 owner line (route R1, owner 2026-10-17T03:52:39Z; DEC-030 §4, merged `fe8586f`, with DEC-021 Amendment 6 as the push slot) and `EXP027_SHA256_R4B_READ` (`tools/r4b_read.py` with `E1_MODE = "B"`, #691, `1b93610`); it also records EXP-026's withdrawal before W0 and the #1059 breach disclosure. No sealed file was opened for revision 7. Written 2026-10-16 (`date -u` 2026-10-16T19:58:25Z at the start of writing). To write it, no file under `/data/mal/blocks/` (forward-1002, forward-1002ev, forward-1016 or any sealed block), `/data/mal/exp012-forward`, or any shadow, canary, probe or walk output was opened. Revision 6 (2026-10-16, after `date -u` 23:10Z) applies QP-685 (`QP-685-7b89499-675-f2034ed.md`, blocking text items 1-4 and the text-side non-blocking items) and the tamper spec's Addendum 2, F-M (first-parent chain, value formats, line endings, placeholder at registration, exact `config.key`, `--check-window` dry run, fetch first); the three placeholders were unchanged then. Revision 4 (2026-10-16, after `date -u` 23:08Z) applies quant-proof's pre-review of draft head `eaa33d7` (`QP-PREREVIEW-675-eaa33d7.md`: 11 blocking items and 4 rulings, plus its non-blocking text items) and the window-record tamper spec (`EXP027-TAMPER-SPEC.md`); the three placeholders were unchanged then. Revision 3 (2026-10-16, after `date -u` 22:53Z; consolidation after the tools merged on `origin/main` at `aef7bea`) pins the final module sha256s, the read tool's pinned lines, the E-a..E-d records, the per-program stranded rent, the Token-2022 allowlist disclosures and the H0 / W0 alignment; the two owner-dependent items and the `r4b_read.py` sha256 were left as marked placeholders (filled in revision 7). Revision 2 (2026-10-16, after `date -u` 20:08Z) applies quant-proof's third and fourth passes on draft head `30f82fb` (calendar stop, per-attempt block-mean t, null ordering and refusals, slot_s, D3, LP, strand-class source, owner sentence) and fills the #672 / #673 sha256s from `origin/main`. The inputs are text only:

| Input | sha256 at writing |
| --- | --- |
| [DEC-030](../DEC/DEC-030-walk2-push-slot.md) (merged `fe8586f`, #664), DEC-021 Amendment 6 (the push slot; merged `fe8586f`) and DEC-021 Amendment 7 (the E1 correction for EXP-027, Option B; merged `fc8c522`, #687) | (git, `origin/main` at `1b93610`) |
| `/data/mal/hunt-1008/push-1016/WALK2-PREREG-CHECKLIST.md` | `26cfd7f4bf22dba43b39744eaaba449f73f3fce30335e61ecdb61d07542fbce7` |
| `/data/mal/hunt-1008/laya-opt/FROZEN-r4-b.md` (cited `FROZEN:<line>`) | `cc626dde5c3e68222b1fcf066db19a6ce0a412a2e91507e941effd10d0aebc49` |
| `/data/mal/hunt-1008/push-1016/hunt6/r4b-verify/QP-RULING-1016.md`: ruling, follow-up, third pass and fourth pass (cited `QP §n`, `QP-FU §n`, `QP-3`, `QP-4 §n`) | `bf12e075f72087d984954b84e4806a522add905f48183391d809d5f3883904a5` (ruling and follow-up, revision 1); `209759e63fe0e62d2a46699ac1f5a88a510ecb9239033b2cf75b18cad28d69fa` (third and fourth passes appended, stamps corrected; revision 2 of this EXP); **revision-3 value, current: `0f419df62c723731251595887bf786adb83afeff612a28f3c1b1f4215c37f938`** |
| `/data/mal/hunt-1008/push-1016/hunt6/r4b-readtool/PLAN.md` (cited `PLAN §n`) | **revision-3 value, current: `5e364b63d3c79f6d49c8d430427029d6b871e4004516846130fa9044a5323b2b`** (superseded: `bb6a6876ad39fe35e8cce4b92cd7f7cfd840cf320248ff47ab998ab9f5ca3552`) |
| `/data/mal/hunt-1008/push-1016/hunt6/r4b-readtool/QP-PREREVIEW-675-eaa33d7.md` (quant-proof's pre-review of this PR at `eaa33d7`, saved verbatim; cited `QP-PRE`) | `420176c76056caebf807969e0c4d9184f05ced961367e47ad62cf4f7782dcd3b` |
| `/data/mal/hunt-1008/push-1016/hunt6/r4b-readtool/EXP027-TAMPER-SPEC.md` (the window-record mechanism with Addendum 1; section 0.5) | `6053552a2b298643290df719ae13b6e905c04809f7b8bb21b775b9d9f8e229da` (with Addendum 2, items F-M; supersedes `c76b7b93…`) |
| `/data/mal/hunt-1008/push-1016/hunt6/r4b-readtool/QP-REREVIEW-675-8923a78.md` (QP re-review of this file at `8923a78`, saved verbatim) | `100950dda7246e286beea455d0cac49cb9dbe05eb1f76afefcc42a96bfc72c75` |
| `/data/mal/hunt-1008/push-1016/hunt6/r4b-readtool/QP-685-7b89499-675-f2034ed.md` (QP on #685 at `7b89499` and on this file at `f2034ed`, saved verbatim; cited `QP-685`) | `b3f5632d11177ae3aa46d9ed807898667b114b07c158c74ee9ad3aec4233fe27` |
| `/data/mal/hunt-1008/JUDGE-3.md` (line 248 only) | `e54fa0ae0053c6480b00105a0c110ddc6d985471061a22e5b27af9540b5752bb` |
| PR #672 (features, E-a; merged `cf1eaac`) and PR #673 (core, E-b, E-c; merged `ea01143`): PR bodies and source on `origin/main` | (git; sha256s in section 2.2) |
| [EXP-022](EXP-022-cap-pick-part1-prereg.md), [EXP-025](EXP-025-c1nf-part1-prereg.md), [EXP-026](EXP-026-h5-boostclock-v2-prereg.md), [DEC-014](../DEC/DEC-014-holdout-ledger-and-multiplicity.md), [DEC-019](../DEC/DEC-019-execution-probe.md) Am.2, [DEC-021](../DEC/DEC-021-champion-challenger.md), [HOLDOUT_LEDGER](../docs/HOLDOUT_LEDGER.md) | (git, `origin/main` at `aad7bd9`) |

This file is docs only. The builder, scorer, xs engine, layout adapter, count job and read tool are separate PRs (section 2.2). Nothing in this file says or implies that r4-b makes money. **r4-b has zero out-of-sample trades and has not cleared the promotion gate** (QP §header). **A FAIL is the most likely outcome** (section 9).

| Field | Value |
| --- | --- |
| **ID** | `EXP-027-r4b-walk2-prereg` |
| **Status** | **Pre-registration.** It registers at its merge under section 0.1 (the Window record's `EXP027_MERGE_INSTANT` records the instant). Nothing had run at merge. |
| **Declared (UTC)** | 2026-10-16 (draft) |
| **Family and slot** | Push-family **book 1** of [DEC-030](../DEC/DEC-030-walk2-push-slot.md) (merged `fe8586f`; route R1), DEC-021 §8's fifth slot (Amendment 6, merged `fe8586f`), family alpha 0.025, Bonferroni 0.025/k. |
| **Parent** | `FROZEN-r4-b.md` (laya-opt judge, 2026-10-08 ~23:25Z): the l3 hourly top-10 cross-sectional book on S1's 4-h LightGBM, 5-seed bag. Quant-proof's VERIFY ruling of 2026-10-16 and its follow-up (`QP-RULING-1016.md`). |
| **Rules** | DEC-014 (a), (b); DEC-021 §1, §4, §8, Amendments 1 (§4, §5 definitions), 5, 6 (the push slot) and 7 (the E1 correction, Option B); DEC-030 §1, §1a, §2 (R1), §2a, §3; DEC-019 Amendment 2 (the 160k condition only); HOLDOUT_LEDGER Rules item 5 and the Forward walk 2 row. |
| **Hypothesis** | The frozen r4-b book (section 2) has mean SOL per attempt > 0 at 0.25 SOL per position on post-W0 walk-2 hours, under both fail models at both entry latencies (1.3 s and 1.9 s), on the deciding cost stack (section 5), and clears the promotion gate and the day-level test at 0.025/k (section 6). |
| **Kill condition** | A FAIL (section 6) retires r4-b: no retune, no refit, no change to K or stake, no re-read (section 7.2). |
| **Expected outcome** | **FAIL.** On the pinned stack (per-attempt block-mean t, no rent, auto-stranded, 505k, E1 mode B), at N = 20 blocks, QP puts P(full pass) at .1650 at the central effect (37.9%) and .5390 if all of the measured edge is real (k = 1); .1070 and .4105 at k = 2. With no edge, P(pass) is .0195 (k = 1) and .0110 (k = 2) (section 9). |
| **Tools** | Builder `tools/r4b_features.py` (#672), pins/scorer/book `tools/r4b_frozen.py`, `tools/r4b_score.py`, `tools/r4b_book.py` (#673), adapter `tools/r4b_layout.py`, count job `tools/r4b_count.py`, read `tools/r4b_read.py` (PLAN §2, §7). |

**Labels.** [measured]: copied from a cited file that computed it; exploration arithmetic is measured arithmetic, not evidence of edge. [inferred]: reasoned. [pinned]: a design value this file fixes; not evidence. Items once marked "QP to confirm" were ruled on by quant-proof's third and fourth passes (QP-3, QP-4) and are written here as ruled (section 13 lists them).

## 0. Status, registration, route and pinned lines

### 0.1 When this file registers

It registers only when **all** of these hold, and it never registers otherwise:

1. **DEC-030 has merged** with the owner's dated line in its section 4 (**done**: DEC-030 §4 records route R1, owner 2026-10-17T03:52:39Z, MiScusi q_6FkDYV4cPVnknQ, which restates q__jVaqpxB937kEg; merged `fe8586f`), quant-proof's OK on DEC-030's exact final head, and a reviewer verdict on the DEC-030 PR.
2. **This file merges** with quant-proof's OK on **this file's exact final head** and a **reviewer verdict on this PR**. The merge is a separate call with `--match-head-commit`.
3. The ledger edit of Appendix A is in this PR's final head (DEC-030 §2a: "The edit is made in that book's own pre-registration PR, which does not merge without it").
4. Every **TO FILL** item of section 13 marked "before merge" is filled in the final head, including `EXP027_E1_MODE` (section 5.1) and the `r4b_read.py` sha256 (section 2.2).
5. The merge instant is **not before 2026-10-18T02:00:00Z** and **not after 2026-10-26T21:59:59Z** (section 0.3).

### 0.2 Route (the owner's line in DEC-030 section 4 is recorded: R1)

This file is written for **route R1**, which the owner chose (DEC-030 §4: 2026-10-17T03:52:39Z, MiScusi q_6FkDYV4cPVnknQ / q__jVaqpxB937kEg; merged `fe8586f`) (DEC-030 §2, R1: no backward-block PASS for push-family books; DEC-021 Amendment 5's "only through §2" overridden for push-family books).

> **Not taken (route R1 is the owner's line); kept for the record.**
> - **Under R2:** this file **cannot register until r4-b has PASSED a one-shot read on a reserved, unread backward block** under the promotion gate at p < 0.025/m with **m = 17** (DEC-030 §2, R2; DEC-014 per-block terms: that block's Part 1 merged first, one block kept unspent). That needs its own Part 1 (not this file), and the first push-family PR must list all 17 sources of m in the ledger or raise m. The pinned line `EXP027_ROUTE` is then changed to R2 before merge, and the PASS record is cited here by path and sha256. Note: the three candidate blocks predate the 2026-10-02 upgrade and the 2026-10-13 redeploy (DEC-030 §2).
> - **Under NO SLOT:** this file **does not register**. DEC-030 does not merge, slot 5 is not opened, and r4-b has no registrable read on walk 2. The file is then closed as withdrawn before counting, with no hour read.

### 0.3 W0 and the earliest merge

- **W0 [pinned].** The first UTC hour whose start is at or after the registration commit's committer time (the merge instant; the first full UTC hour after the merge), floored at 2026-10-18T03 (`EARLIEST_W0`) (DEC-030 §1a: "the first full UTC hour after its pre-registration merges ..., never moved"). Hours before W0 are never counted.
- **W0 must be ≥ 2026-10-18T03:00Z (`EARLIEST_W0`).** The count job's create history starts at **H0 = 2026-10-16T03** (`tools/r4b_count.H0`, `tools/r4b_read.H0`), so the 48 h universe ramp completes at 2026-10-18T03 and no earlier mark has a complete universe (QP on #678, R6). This replaces the draft's 10-18T01, which assumed pools from 10-16T01 (section 12). **DEC-030 has no provision for a W0 later than the first full hour after merge**, so a later W0 cannot be declared. Therefore: **this file must not merge before 2026-10-18T02:00:00Z.** A merge at or after that instant (and before 03:00:00Z) gives W0 = 2026-10-18T03; the read tool refuses a W0 before `EARLIEST_W0`. **The tool recomputes W0** from the registration commit's committer time by the definition above and refuses when it differs from `EXP027_W0`, or when `EXP027_W0` is absent at read time; a late `EXP027_W0` line therefore cannot move W0. **The merge window is [2026-10-18T02:00:00Z, 2026-10-26T21:59:59Z].**
- **Block alignment (advice, not a rule).** QP §3 recommends W0 at 00:00Z so the 24 h blocks are UTC dates. That happens only if the merge falls in the hour before a midnight (earliest: merge in `[2026-10-18T23:00Z, 2026-10-19T00:00Z)` gives W0 = 2026-10-19T00). If the merge falls elsewhere, the blocks start at W0's hour; the gate's date items still count UTC dates (section 6).
- **Last W0 [pinned].** The stop needs at least 20 blocks with attempts by the cap, so that E + 3 h (the end of the allowlist `[H0, E + 3 h)`, whose last hour opened is E + 2 h) is at most 2026-11-16T01, the end of the Forward walk 2 row, **W0 ≤ 2026-10-26T22** and so E ≤ 2026-11-15T22. If this file has not merged by 2026-10-26T21:59:59Z, it is withdrawn before counting; a re-filing is a new file on later hours.
- **Withdrawal** is possible only before W0. After W0 this book is never withdrawn, re-scoped or delayed except by a section 7.1 NOT_DECIDABLE condition.
- **Window record.** The merge instant (`EXP027_MERGE_INSTANT`) and W0 (`EXP027_W0`) are appended before W0, as dated lines in the Window record, the last section of this file, by the mechanism of section 0.5 (from the merge record only), and in the ledger sub-entry. The cap and E are derived from W0 and need no line. **k is not appended at registration** (the next bullet of section 0.4).

### 0.4 Pinned lines

```
EXP027_BOOK_ID: r4-b
EXP027_FAMILY: DEC-030 slot 5, push-family book 1
EXP027_ROUTE: R1
EXP027_COUNT_START: W0 = the first UTC hour whose start is at or after the registration commit's committer time, floored at 2026-10-18T03 (EARLIEST_W0; H0 = 2026-10-16T03); never moved; the tool recomputes it and refuses on a mismatch with EXP027_W0
EXP027_LAST_W0: 2026-10-26T22
EXP027_STOP_RULE: calendar stop E = W0 + 480 h; the read requires at least 20 blocks from W0 holding an attempt and at least 125 attempts, otherwise NOT_DECIDABLE with no extension
EXP027_EXIT_HORIZON_H: 1
EXP027_CAP_END: cap = E (= W0 + 480 h, at or before 2026-11-15T22)
EXP027_DEADLINE: E + 48 h
EXP027_ALPHA: 0.025/k, Bonferroni, k frozen at the first push-family read
EXP027_DECISION_P: the largest p over the 4 cells (flat, pressure) x (1.3 s, 1.9 s) of the one-sided day-level t over 24 h blocks from W0, empty blocks dropped, df = W - 1
EXP027_SEND_FEE_LAMPORTS: 505000, or 160000 only under section 5.3
EXP027_RENT_DECIDING: 0 per filled trip under section 5.2 conditions (i)-(iii); the per-program rent of section 5.2 per filled trip (2074080 for Token-2022 or an unknown program, 2039280 for an SPL Token mint) if condition (i) is unmet
EXP027_WALK_DIR: /data/mal/blocks/forward-1016
EXP027_SELL_BUILDER: tools/probe_live.py@b188a40bbaa358e3b3a1e3fdf01dace12fba80aca4e3327b11db08280285bce3
EXP027_DECODER_REF: 578a63eac199c9175438e1ec651d1537944a89c5
EXP027_TRIES_ENTRY: r4b_exp027_family
EXP027_E1_MODE: B
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
EXP027_SHA256_R4B_READ: 0563de0495c0bbd6ec2a085ddfa5b795307bb24e91df543d467cdd9651297581
```

**Pinned lines the read tool also requires, added to the Window record after merge (not here).** `tools/r4b_read.py` `REQUIRED_KEYS` also lists `EXP027_W0`, `EXP027_K`, `EXP027_INPUT_VIEWS`, `EXP027_MINT_ACCOUNTS`, `EXP027_FEE_ARM` and `EXP027_A3_RECORD`. In mode B there is no `EXP027_E1_RECORD` (the tool refuses it). `EXP027_DECODER_REF` and `EXP027_TRIES_ENTRY` are in the block above because what they name exists today (QP-PRE, ruling 3): `EXP027_DECODER_REF` is the git ref of walk 2's job #1036 (LAB_STATE "Walk 2 running"), and `python -m tools.forward_v_join pins --ref 578a63eac199c9175438e1ec651d1537944a89c5` reports all three decoder blobs equal to `PINNED_BLOBS` (exit 0, checked 2026-10-16); the successor walk-2 job of section 4.4 must run the same three blobs; `EXP027_TRIES_ENTRY` is the `config.key` of the r4-b family line this PR adds to `data/tries.jsonl` (section 2.2). The six lines left name things that do not exist before merge (W0 comes from the merge record, k from the first read instant; the mint-account file, the A3 record, the input views and the fee-arm record are written later). Each is appended, once, to the **Window record**, by the mechanism of section 0.5. The read tool refuses until they exist.

- **Format.** Each line matches `^<KEY>: (.*\S)\s*$` exactly once in this file, as written (`tools/r4b_read.exp_lines`). A value that is a placeholder (a to-do marker in place of the value): it must be replaced before merge, and the read tool refuses a placeholder (a sha256 pin is checked against `HEX64`, an md5 against `HEX32`, the E1 mode against the tool's constant). The read tool refuses unless each does and unless this file is clean against HEAD. The values never change after merge. A Window-record line has the form `KEY: value (appended YYYY-MM-DDTHH:MMZ)` (section 0.5), so a value read from the Window record is the text before the final ` (appended …)`.
- **Meaning.** `E` is the stop instant, fixed by the calendar (QP fourth pass §5, "STOP_MODE: calendar"). The last entry mark is E − 1 h; every open position is sold at the E mark; hours are read to E + 1 h (the exit horizon). The cap equals E. If, at the read, fewer than 20 blocks hold an attempt or fewer than 125 attempts are counted, the book is NOT_DECIDABLE (p = 1, stays in k), with no extension. Since W0 ≤ 2026-10-26T22, E ≤ 2026-11-15T22, the exit horizon ends by 2026-11-15T23 and the allowlist [H0, E + 3 h) ends by 2026-11-16T01, the row's end (DEC-030 §1a, §2a). This calendar stop is outcome-blind by construction. It is DEC-030 §1a's stop rule with this book's declared N = 20, A = 125 and cap W0 + 480 h (stricter than §1a's floors N ≥ 7, A ≥ 100, cap ≤ 21 d): the first boundary with 20 blocks holding attempts cannot come before W0 + 480 h, and the cap is that boundary.
- **k.** Under DEC-030 §1, k is the number of push-family books registered when the first push-family read starts (k = 1 if r4-b is alone then; 2 or 3 if more books have registered). After that instant k never changes. A FAIL or NOT_DECIDABLE push book stays in k at p = 1. **`EXP027_K` is appended once, at the first push-family read instant and before that read's lock, from the ledger's count of push-family books registered at that instant. The k at registration is ledger information only, not a pinned line** (QP-PRE, blocking 6): pinning it at registration would let a second book that registers before the read change the tool's alpha while the "appended once" rule blocked any correction.

### 0.5 Window record: how it is appended and how tampering shows (EXP027-TAMPER-SPEC)

Pinned lines of this file's body never change after registration. The only later change is an **append to the Window record**, and the read tool detects anything else.

- **Layout.** `## Window record` is the **last section** of this file. It sits after Sources and nothing follows it. The **body prefix** is the file's bytes up to the start of the line `## Window record`, not including that line.
- **Values recorded at registration.** At registration, which is the squash merge of this PR into main, the manager records two values in the HOLDOUT_LEDGER EXP-027 sub-entry and in a MiScusi notebook entry (the EXP-026 "rule block sha256" precedent): `EXP027_REG_COMMIT <sha>`, the main commit that first added this file; and `EXP027_PREFIX_SHA256 <sha256>`, the sha256 of the body prefix at that commit. They are ledger values, not lines of this file (the file cannot contain the hash of its own prefix plus a commit that contains it). **The manager writes them in a reviewed ledger commit to main after the registration merge and before W0**; neither value exists before the merge, so the ledger text this PR merges carries placeholders (Appendix A). The tool finds each value wherever its token appears in the ledger (for example in the Status cell or the changelog bullet): ``(?<![A-Za-z0-9_])EXP027_REG_COMMIT[ :]+`?([0-9a-f]{40})(?![0-9a-f])`?``, and the same for `EXP027_PREFIX_SHA256` with 64 hex characters (and the same `(?![0-9a-f])`). **The manager fills the values in exactly the form of Appendix A** (``EXP027_REG_COMMIT <sha>`` with the token, one space, the value): forms such as ``EXP027_REG_COMMIT`: sha`` or ``EXP027_REG_COMMIT = sha`` do not match and would refuse the read (QP-685). The ledger file must be committed and clean against HEAD (check 9). There must be at least one occurrence of each, and all occurrences must agree; otherwise the read refuses.
- **Append format.** One line per commit: **exactly one added line, no blank line, no removal**. Each line is dated with `date -u` and matches `^EXP027_[A-Z0-9_]+: .+ \(appended \d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z\)$`. **Line endings:** each appended line ends with a single LF (no CR), and the file ends with LF at every commit. A CR fails the format regex; a missing final LF is an end-of-file newline change and refuses. Neither can be repaired afterwards (a repair needs a removal), so both are permanent. **Value formats (a wrong format refuses the read for good):** `EXP027_W0: YYYY-MM-DDTHH`, for example `EXP027_W0: 2026-10-18T03 (appended 2026-10-18T02:41Z)` (`2026-10-18T03:00Z` is refused); `EXP027_K`: a bare positive integer; `EXP027_A3_RECORD` and `EXP027_MINT_ACCOUNTS`: exactly `path@sha256`; `EXP027_INPUT_VIEWS`: `;`-separated `path@sha256`; `EXP027_FEE_ARM`: `NONE` or `path@sha256`; absolute paths preferred. `EXP027_MINT_ACCOUNTS` carries the `path@sha256` of the post-E members file only; its job id and RPC credits go to the ledger and the MiScusi notebook. Each key is appended **once**. Each line goes to main in **its own commit**, with a reviewer, and the commit's committer time must be earlier than the `started` instant of the read.
- **Closed key list.** Every line appended under `## Window record` uses this format and one of these keys, each once: `EXP027_MERGE_INSTANT`, `EXP027_W0`, `EXP027_K`, `EXP027_INPUT_VIEWS`, `EXP027_MINT_ACCOUNTS`, `EXP027_FEE_ARM`, `EXP027_A3_RECORD`, `EXP027_WALK2_JOB_2`, `EXP027_WALK2_JOB_3` (successor walk-2 jobs: job id, git ref, credits), `EXP027_MEMBERS_PREW0` (job id, sha256, credits), `EXP027_POWER_160K` (path@sha256), `EXP027_BREACH_1` … `EXP027_BREACH_9`, `EXP027_DEADLINE_ND`. Any other record goes only to the ledger and the MiScusi notebook. At registration the text after the heading is only the one italic placeholder paragraph (lines that start and end with `*`) or blank lines; anything else at the registration commit refuses (tamper spec I). A malformed line refuses the read, and that refusal binds (section 7.1.4).
- **Checks the read tool performs** (each failure refuses before any sealed byte is opened):
  1. **Registration commit.** reg = the last line of `git log --first-parent --diff-filter=A --format=%H origin/main -- EXP/EXP-027-r4b-walk2-prereg.md`. It must equal the ledger's `EXP027_REG_COMMIT`, and HEAD must descend from it (`git merge-base --is-ancestor`).
  2. **EXP at HEAD.** The EXP blob at HEAD equals the blob at `origin/main` (and the file is clean against HEAD).
  3. **Body prefix.** The sha256 of the body prefix at HEAD equals the ledger's `EXP027_PREFIX_SHA256` and the body prefix of the blob at reg.
  4. **Additions only.** `git diff <reg> HEAD -- <EXP path>` has added lines only, none removed or changed, and every addition comes after the `## Window record` line. An end-of-file newline change also refuses (the file ends with LF at every commit).
  5. **One line per commit, closed key list, first-parent chain.** Commits are taken along main's first-parent chain (`git log --first-parent --format='%H %ct %P' reg..HEAD -- <path>`), each judged by its diff against its first parent (`git diff --unified=0 --no-renames <c>^1 <c> -- <path>`); a commit with an empty diff is skipped, and a merge commit is not refused for being a merge (an unrelated merge commit cannot refuse the read). **Append PRs are squash-merged.** Every such commit that touches this file adds exactly one non-empty line (no blank line, no removal), in the format above, with a key of the closed list; no key appears twice. Any other key or a malformed line refuses.
  6. **Commit times.** Every such commit's committer time is earlier than the read's `started` instant. The `started` record in `READS.jsonl` records this file's sha256 and the reg commit.
  7. **Exact tries key.** The tries check matches `EXP027_TRIES_ENTRY` against a line's exact `config.key` only, not a substring of the line, and not a top-level `key` or `data_key`.
  8. **W0 recomputed.** The tool computes W0 from the committer time of reg (section 0.3) and refuses on a mismatch with `EXP027_W0`, or when `EXP027_W0` is absent at read time.
  9. **Ledger values.** `EXP027_REG_COMMIT` and `EXP027_PREFIX_SHA256` are read from the ledger wherever the token appears; at least one occurrence each, all agreeing. The ledger file must be committed and clean against HEAD.
- **What may be appended, and when** (QP-PRE ruling 3): W0 and the merge instant, before W0 (the tool recomputes W0 and refuses on a mismatch, this file's check 8: W0 as defined in section 0.3); `EXP027_K`, at the first push-family read, before that read's lock; the fee-arm record, before the read lock; the mint-account file (the post-E `members` run, built from the structure-only member list), the A3 record and the input views, after E and before the lock. All of these come from structure or job records, never from an outcome.
- **Dry run.** `tools/r4b_read.py --check-window` runs the same window guard with `started` = now, opens no sealed data and exits non-zero on any problem. **The manager runs it on every append PR before merging it, and after the post-merge ledger commit**, because a format mistake is permanent and would otherwise show only at read time. Its output has two groups: **VIOLATIONS** (exit 1: any rule break in what is already there) and **PENDING** (exit 0, listed: keys or ledger values not yet written, for example `EXP027_W0` before it is appended, or the ledger values before the post-merge ledger commit). The read itself treats PENDING as a refusal.
- **Fetch first.** The read job's ops step is `git fetch origin` then `git checkout --detach origin/main` (the read tool enforces HEAD == origin/main); the tool trusts the local `origin/main` ref, and the ledger is the only anchor it checks. A force-push that rewrote main and the ledger together would pass the tool. The out-of-band anchors are the MiScusi notebook entry (the two registration values) and **branch protection against force-push on main**, which closes the gap (an ops request, not a tool check).
- **Tool side.** The tool side (checks 1-9, `--check-window`) is on `origin/main` (`tools/r4b_read.py` sha256 `0563de0495c0bbd6ec2a085ddfa5b795307bb24e91df543d467cdd9651297581`, #691 `1b93610`, `E1_MODE = "B"`). Done: QP OK 6229278039 and Reviewer OK 6229294536 on #691 at 8ae2386, whose `tools/r4b_read.py` is byte-identical to the pin.

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
| `tools/r4b_read.py` (read tool, #678 `8e274b7`; allowlist #683 `ff2e398`; per-program rent #684 `aef7bea`; tamper checks and exact tries key; E1 mode B, #691 `1b93610`) | sha256 `0563de0495c0bbd6ec2a085ddfa5b795307bb24e91df543d467cdd9651297581` (`EXP027_SHA256_R4B_READ`), `E1_MODE = "B"`. A later change to the tool moves this pin and needs a new QP delta pass. `aef7bea` has `8d579194d0f2f63cf8bec3479f41ed4959874467a7c4933d42b1503354f787b4` and is **not** the pin. `pinned_file` resolves a repo-relative path against the repository root (#685; QP-685), so the working directory does not matter | `sha256sum` on `origin/main` at `1b93610` (2026-10-17) |
| PumpSwap fee tiers | `FEE_TIERS` of `tools/r4b_frozen.py` (= `tools.paper_curve_math.PUMPSWAP_SOL_FEE_TIERS` = harness `hlib.TIERS`); sha256 of its canonical JSON (`tools/r4b_read.fee_tiers_sha256`, `[list(t) for t in FEE_TIERS]`, separators `,` `:`) `0f33121627ac7647562a097de4d7386312bc3747ad6a4688935bc5f7cee9b208` (`EXP027_TIERS_SHA256`) | QP §5 |
| Decoder blobs | walk 2's job ref (`forward_v_join` PINNED_BLOBS, `pins --ref`): `EXP027_DECODER_REF` `578a63eac199c9175438e1ec651d1537944a89c5` (job #1036's git_ref; blobs `observe/trade_decode.py` `238942a6…`, `observe/trade_store.py` `ea4e11ed…`, `tools/pump_history_backfill.py` `9a8bebb3…`, all equal) | PLAN §4; LAB_STATE "Walk 2 running" |
| Input views | every input view's `path@sha256` (`EXP027_INPUT_VIEWS`, `;`-separated): a Window-record line (section 0.4) | DEC-021 Am.5; PLAN §4 |
| Tries | `EXP027_TRIES_ENTRY` `r4b_exp027_family`: the line this PR adds to `data/tries.jsonl` (`config.key` `r4b_exp027_family`, `data_key` `608c62c3e2fa006b`, `variant_n` 1, role exploration, written with `tools.mal_result.append_try`, `ts_utc` 2026-10-16). It is one family line: 6 l3 variants, about 25 hunts on this window, the frozen book `results/r4-b-rb2`; the individual variants are not separately logged. Data blocks: the six exploration-pool ranges of the r4-b walk-forward test days (FROZEN:38), which exclude the reserved `[2026-08-28T12, 2026-09-03T12)` and EXP-009's `[2026-09-15T12, 2026-09-18T23)`; mal-research-0 | DEC-021 Am.5 (every configuration logged, families pre-registered) |
| `tools/pumpswap_tx.py` | sha256 `179a88cf1f76779c35af02ed49e7213834c93466841dee2bdd563763105697ba` (record; it builds `sell_instructions`, which `tools/probe_live.py` calls; the code pin is `EXP027_SELL_BUILDER`) | `sha256sum` on `origin/main` at `aef7bea`; QP-PRE ruling 1 |
| rb2 reproduction keys (records; nothing decides on them; the files are under `/data/mal/hunt-1008/laya-opt/` and are not on `origin/main`) | `results/r4-b-rb2/trades.csv.gz` sha256 `24c6d8a0c351be3d7efdbdbeb4bd71c190c07f486a6b8ce9df1112a6e280f63f`; `harness/specs/r4-b-rb2.py` sha256 `6a432674f5eea9e061b000512eac71cad6d385cb842491c90afface37873ed73`. The 5 prediction keys of the walk-forward (seed 1 = S1's cache `439cb1a3801bf051ff9c`, `summary.json` / `r4-b-rb2.py`; the other four in the order of r4b-verify `DECLARE.md`) and the sha256 of each `cache/preds/<key>.npz`: `439cb1a3801bf051ff9c` `598c1c627bf25a6bf5bea42d6ec337c8322289a315baf87b8e6d18d49bd04e6b`; `060d4f04dfa8bb63f237` `9d871839bf68da5632afcee3f5acf810938f74f6a3fa902dd62542c3e818a2dd`; `651385169a622bb4761c` `15aeaecb4e8b5b464550e9e554131888bc27574dbb8de6010b04dd96db8befb5`; `935cdc89ee2687ce9a77` `acb693007ef976b539bdac1bcf5b4e822fdf75ef0b8829c1a1780baae1cb483c`; `38c3d14f741f9f205094` `2a9268443f35cb49c9917e2d45c016fa3e53cfc557997e30e7fddf9c0c9b47e8` (computed 2026-10-16). The target itself is pinned by the E-c md5 `54052f79…` | QP §2; QP-PRE blocking 9 |

**Book constants [pinned]** (QP §5; `tools/r4b_frozen.py`): K = 10; 0.25 SOL (250,000,000 lamports) per position; hour marks (3,600 s); ties to the lower `mid`; no minimum score, no hysteresis, no stops; universe 10 min to 48 h after graduation, traded in the last 10 min; PDA pool; event V.

**Cost constants [pinned]:** section 5.

### 2.3 Equivalence proofs (recorded before registration)

| Proof | What | Result | Status |
| --- | --- | --- | --- |
| **E-a** | Port fed l3 `ut/` hours 2026-09-23T00..09-25T06 reproduces the fixture X (617 rows, `mid`, `T`, `sd` equal, X float32 byte-equal) | md5(X bytes) **`3e9bc6225377927682d80b9441391a96`** = fixture [measured, #672] | done (#672, merged `cf1eaac`) |
| **E-b** | The 5 boosters on fixture X reproduce seed1..5 and `bag_score` | max abs diff 0.0; 617 rows, 52 with bag > 0 [measured, #673]. `EXP027_E_B_MD5` = md5 of `equiv_fixture.npz['bag_score']` **as little-endian float64, C-order, 617 values**: `e4056fb11902481dbf60c2f14f07f7c9`. The data type must be named: a float32 cast gives `d4b553c4…` [measured by QP, QP-PRE]. This md5 is a property of the pinned fixture; the evidence that the five boosters reproduce it is `tools.r4b_score.check_fixture()` (all `*_equal` True, max abs diff 0.0), added in #673 at `ea01143` | done (#673, merged `ea01143`) |
| **E-c** | The xs engine, fed the rebuilt walk-forward bag (float32 ranking), reproduces the rebuilt **`results/r4-b-rb2`** trades (n **2,986**) | trades table (harness columns, `%.9g`) text-identical, md5 **`54052f798013a71e3a9f40e0d6830ac1`**; exits rank 2,956, gap 20, end 10; the four deciding cells equal the r4b-verify REPORT at 6 decimals [measured, #673] | done (#673, merged `ea01143`) |
| **E-d** | md5 decision-equivalence replay on clean-view explore-0814 w7→w4, `[08-14T12, 08-21T12)`, walker layout (static-map V from `pool_v_0909.json`, seg_end 08-28T12), `grads_rule` "frozen", order (mid, slot, txi, evi, frn): I and X bytes equal l3 `feat/<day>.npz`; Qe/Be/spot/ssb/nearby equal `cache/l3/dec`; marks equal `cache/l3/marks`; PDA-vs-V-band mint list empty; event-V injection test gives the same md5 | **Scoped PASS, raw FAIL**; record and scope sentence in section 2.3.1. `EXP027_E_D_MD5` = **md5 of the E-d report file** `93a38d6ac19b7d7f49a787026b7655ec` (scoped PASS, raw FAIL); **not a decision md5**. The binding E-d content is the scope sentence and the per-day non-tie md5 table of section 2.3.1. The read tool only checks that the E-a..E-d values are well-formed md5s; they are records. `full3/report.json` was made read-only (`chmod a-w`, 2026-10-16) and its sha256 is pinned in section 2.3.1 | done (#681, merged `64231f4`; MiScusi job #1082 at `e504803`) |

**FROZEN:123 is amended [pinned].** FROZEN:123's target "reproduces `results/r4-b/trades.csv.gz` (n 3,002)" **cannot be done** (QP §2: the 10-08 cache is deleted). The reproduction target is **`results/r4-b-rb2` (n 2,986), reproduced from the 5 rb2 prediction keys (sha256-pinned, section 2.2), md5 `54052f798013a71e3a9f40e0d6830ac1` of the trades table** (#673 E-c). The 10-08 `results/r4-b` file (n 3,002) stays on disk as a record and is not a target.

#### 2.3.1 E-d record and scope [pinned]

**Scope sentence (quant-proof, PR #681 comment 6225999636).** At `e504803` the r4-b port is md5-equivalent to l3 on 2026-08-14..18 (explore-0814, MiScusi job #1082, `/data/mal/hunt-1008/push-1016/hunt6/r4b-equiv-d/full3/report.json`, sha256 `70ded6de33fa96a3ca55410d9b1ccd7567627eb80a006a5bd87bd2f0745c8898`, rc = 0) for every row of mints outside the null-`tx_index` tie set. **Raw full-row equivalence FAILS and is not claimed; tie mints are not covered.** The script and wrapper are `scripts/research/r4b_equiv_d.py` sha256 `46ea9a63ba2cc70a08e74f30509ed9b01260a0442fd41cbeb756a77307e68f23` and `scripts/research/r4b-equiv-d.sh` sha256 `ecd2b49edafc934409d1ac5a3f8cd02e1c3762dd03bc9d7eed33be94be3539e0` (both last changed at `64231f4`, the merge of #681; `sha256sum` on `origin/main` at `aef7bea`). Job #1082 ran the wrapper, `scripts/research/r4b-equiv-d.sh /data/mal/hunt-1008/push-1016/hunt6/r4b-equiv-d/full3` (the wrapper's own usage; it runs the script under `systemd-run --user --scope -p MemoryMax=20G -p MemorySwapMax=0 nice -n 10 timeout 3600` with `R4B_EQUIV_D_CAP=21474836480`; the exact command line of the job is in the MiScusi job record, which this file did not re-read). `r4b_equiv_d_ties.py`, which QP cited at `1f22e47`, is not on main: its tie-group logic was folded into the main script at `e504803`.

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

**Tie-mint definition (QP-PRE blocking 1; QP on #681 at `1f22e47`).** A tie group is a `(mid, slot, txi, evi)` group in l3's `ut/` with more than one row and distinct content `(isbuy, sol, tok, q, b, th)`. Recomputed from l3's own `ut/` there are **434 mints** and 1,090 groups (5,673 rows), sizes 2 to 90, every group with `txi = −1`; the sorted list of the 434 mids has sha256 `9a0fd734ca1817d581e5f309baa4b9c00ba3b8dbacec10568de1e36dd1baa902`. **274 tie mints, 117,476 rows (12.4%) are excluded** from the non-tie comparison (the per-day excluded rows in the table above).

**Whole-day md5s, port vs l3 (raw; they differ, as the raw FAIL says), with the row-diff counts** [measured, report.json `features.days`]:

| day | X port | X l3 | dec port | dec l3 | marks port | marks l3 | X / dec / marks rows differing |
|---|---|---|---|---|---|---|---|
| 2026-08-14 | `269e45d2d358998b503a223dc8cfd664` | `8c1ab0669ae47f528a9b80161105dd4d` | `22f0071e8fd260687dde6fe7d33ea917` | `5ff8a6852898759643b57fd6defb8cc4` | `9b0a329a323eb245aecb82d2253b90f3` | `da8fa7969f7abff8760709b439202abd` | 205 / 29 / 53 |
| 2026-08-15 | `b7b6d6eb74596fddd48a89cdbdea9606` | `0ff1b1cf40a2800ca9fb37d7ddbc6bc1` | `4936f605bffedef22f691ffe3de492e2` | `890397bf18d98f7fb58a34cfdd25828b` | `f52da019fb7793ace04354dfc7c62ee1` | `521462d5b335d975ac56e3c5c32167f0` | 220 / 53 / 33 |
| 2026-08-16 | `84ba8ab9a5feb17ce3a16981a28de26a` | `d73e416771fb494b022240701107100c` | `4547c2b96b563fd69082c746a7375ba5` | `6a5d89906fbb62cb621ddbc950474190` | `59463ab03563d0528b0582d9f7697887` | `a4bd7da967ecd24beb3fae6c86ddcbb8` | 174 / 23 / 7 |
| 2026-08-17 | `858a9f069ae5ee218282fd64801bfaec` | `c12b52b6ecc5d42cf7a77fc8c6c19675` | `885f266c87dc130a0966c557e3dd7430` | `2e4c45c8394479b9432a2e4a5a3f4e1c` | `0c6a7e0970bb573325f29055dc59add2` | `381dca5530d0db28f637011bf34ef78e` | 311 / 35 / 24 |
| 2026-08-18 | `e6d3e412c61cb970b64026ac18ea9dc5` | `b5f12002ef84030bf6867fee2daa21be` | `38b4018008d9ea6b681888b3e4976a62` | `aa798641d95ae34c61d126ad6daad3de` | `26d0ade28375b6bdacdd31cc242711d7` | `f990b846315b00b9a23972166b2e9e57` | 173 / 32 / 64 |

**Raw (not claimed).** On the whole of l3 the X, dec and marks md5s differ every day: 1,083 X rows, 172 dec rows and 181 marks rows (of 950,729), on 75 differing mints (14, 18, 11, 19, 13 per day), every one inside the tie set. Cause: l3 sorted with `order by mid, slot, txi, evi` only and DuckDB returns tied rows in a different order from run to run (the same cause as the n 3,002 vs 2,986 gap, section 2.4). The port breaks ties by `frn`, which is total.

**Disclosure (QP, #681).** Training labels and features (r4-b's model, the VERIFY book and every in-sample number) carry that order noise; the forward port is deterministic in frn order; no systematic shift is expected [inferred, not measured]; not measured on the other training blocks.

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

**As-of mint filters and the jw clamp [pinned] (#679, QP comment 6224294297).** In forward mode the read calls the builder with `grads_rule="asof_pda_first_print"` and `mint_filters="asof"` and the jw clamp on (`jw = min(jw, j)`: a later print with a null `block_time` cannot enter a window); the defaults of `tools/r4b_features.py` are `"frozen"` (training, fixture, E-a, E-d) and are never used for a forward decision. The frozen mint filters (fewer than 3 prints, any reserve ≤ 0) look at the whole tape and would be whole-tape lookahead in a forward mode; the as-of version uses pre-mark prints only. An entrypoint test pins the flags at the read's call sites. **Disclosure (QP on #679 at `65b2d69`, condition 2):** in exploration the as-of mint filters change little: **0 of 184,485 hour-mark rows, 7 of 5,557,453 2-min grid rows (frozen-only), 0 top-10 changes at 761 marks.** This is a property of the exploration tape, not a measured forward effect (every exploration mint has at least 42 prints). In forward data the frozen filters may flatter, and the as-of version avoids that. **Report-only in the read:** as-of-only and frozen-only row counts per date and per mark, how many as-of-only rows entered a top-10, and the P&L of those trades, shown separately and never removed. **Read-side items (QP on #679, conditions 1 and 3):** the read manifest records both forward flags (`grads_rule` and `mint_filters`), and the read reports, per hour, the member prints with a null `block_time` (report-only). QP checked every feature for other whole-tape lookahead (none), and `return_state` never feeds a forward decision.

### 3.2 Total order of prints

Rows are ordered totally by **(slot, coalesce(tx_index, −1), event_index, file_row_number)**: null `tx_index` sorts **first** within the slot, exactly as training (`01_extract.py`: `coalesce(tx_index,-1)`), and `file_row_number` is the **one tie-breaker** (QP-4 §7; QP-3 on #674; `forward_order` as merged in `cf1eaac`). The precount reports null-`tx_index` tie groups (`null_txi_tie_groups`). **A mixed (mint, slot) count > 0** (a mint's slot holding both null and non-null `tx_index`) **refuses the read** (section 8.1). Exploration had 0 mixed slots over all 36 l3 days [measured, #672].

### 3.3 Slot time and k

- **Rounding:** **np.round** (half to even; at 200 ms slots 1.3 s = 6.5 slots → 6), `K_ROUNDING = "round"` (QP §2; #673). k = np.round(lat / slot_s), for lat = 1.3 s and 1.9 s. Not switched on results.
- **Disclosure, k tie at exactly 200 ms.** At `slot_s` exactly 0.200 s, 1.3 / 0.2 = 6.5 exactly, so k = 6 (half to even), and 1.9 / 0.2 evaluates in float64 to 9.499999999999998, so **k = 9** (the exact half would round to 10). Both are what the tool does (`k_for`); a `slot_s` slightly off 0.200 moves k by one slot, and the measured `slot_s` per date (below) is what is used. Not corrected, not tuned.
- **slot_s per UTC date** (QP-4 §2): slot_s(date) = **3600 / median over the date's hours of SPH_h**, with SPH_h from `cap_pick_score.measure_hour_sph` (PLAN §1). k uses np.round. An hour with unmeasurable SPH is a refusal.
- **Exit lag:** ceil(0.55 s / slot_s − 1e-9) slots, asserted ≥ 2 (3 at 200 ms).

### 3.4 Universe and hours

- **Pools created at or after H0 = 2026-10-16T03:00Z only** (QP on #676/#678: the count job's create history starts at 10-16T03; QP §2's original bound was 10-16T01), so no forward-1002 hour (and no D2 hour) is ever read, and neither 2026-10-16T01 nor T02 is opened by the read. Membership: PDA match and V0 in [17.5e9, 17.7e9] lamports (PLAN §1; the share outside the band is reported). **Mayhem (QP on #676):** mints with no create row get `mayhem = null`, which coalesces to false; they still cannot become members, because Mayhem V0 is 0, outside the V0 band (`inband_mayhem` = 0 on every exploration day). **A pool with missing V0 is a non-member** (QP-3; QP-4 §7); the read refuses only if the missing-V0 share exceeds 2% on a read date (section 8.1). **A member with any null-V (`vq`) print refuses the read** (section 8.1).
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

- **Counted window:** entry marks in `[W0, E − 1 h]`; hours read `[2026-10-16T03, E + 3 h)` (the allowlist of section 3.4: feature buffer from H0 before W0, counted from W0, the exit horizon after E, and 2 extra trade hours).
- **Blocks:** 24 h blocks from W0; block b holds attempts with entry mark T0 in `[W0 + 24h·b, W0 + 24h·(b+1))`.
- **Calendar stop E = W0 + 480 h; N = 20, A = 125 checked inside the read; cap = E; exit horizon 1 h** (QP-4 §5 "STOP_MODE: calendar"; QP §3 and QP-FU §3 for N, A). These are stricter than DEC-030 §1a's floors (N ≥ 7, A ≥ 100, cap ≤ 21 d), as §1a allows a book to declare before W0.
- **Expected attempts:** about 103 per day [inferred from FROZEN §6 and PLAN §4]; exploration attempts per day were 20 / 115 / 140 (min / median / max), CV 0.293 [measured, QP-4 §1]. So about 2,000 at E; N binds, not A.

### 4.2 Attempts and the count job

- **Attempt:** a buy triggered at an hour mark T0 in `[W0, E − 1 h]` for a top-10 token not held at T0, guarded buys included. In this file "trade" = "attempt" = one `xs_book` entry, guarded buys included. The held set is rank-only (`xs_book` ranks and never looks at guards or fills), so N and A are outcome-blind.
- **Count job** (`tools/r4b_count.py`, an hourly host-side MiScusi job, 12 GB cap; PLAN §4). Because the stop is by calendar (QP-4 §5), the count job **computes no features, scores, ranks or attempts** (this settles PLAN §4's open ruling on post-entry prices of held tokens: they are never loaded before the read). It opens only allowlisted, sealed, strict-verified, sha256-matched hours and writes, to `/data/mal/r4b/counts.jsonl`, hour health (sealed, verified, bad), the structure precount items of section 8.1, and per mark the decision rows `(mint, T, sd)` with their md5. It never loads a price, side or reserve column of a row at slot ≥ sd(t) (only slot and block_time for the clock), and computes no landing state, guard, fill, exit or P&L. **It is outcome-blind and is not a "read"; it does not freeze k** (DEC-030 §1).
- **Count job start.** The hourly count job runs on walk-2 hours **only after this file has merged**, so that no reader runs on walk 2 before registration. It is chained from W0, passes `--through-hour` = the latest sealed and verified hour, and never `--keep-scratch`. It refuses unless `counts.jsonl` has a line for every hour in `[H0, E]` and the extra hours are loaded (`extra_hours_loaded`); the read tool refuses a `counts.jsonl` that lacks any hour of `[H0, E]` (QP on #678).
- **N and A** (blocks with attempts, attempts) are computed **inside the read**. Fewer than 20 or fewer than 125 is NOT_DECIDABLE, with no extension.
- **The stop instant** E = W0 + 480 h is written in the ledger sub-entry at registration (Appendix A); the read records the N and A it found.

### 4.3 The read and its deadline

- **One read, no interim looks** (DEC-030 §1a). It runs after E + 1 h is sealed and verified, the read tool is merged with QP OK on its head (sha256 pinned in section 2.2), the E1 mode of section 5.1.1 is pinned (B), and the section 5.3 record is in the ledger or its fallback applies.
- **Deadline:** `EXP027_DEADLINE` (E + 48 h). A read not completed by then is NOT_DECIDABLE, and the window is spent (checklist §1 d). The tool enforces it: a read that starts, or forms its verdict, at or after E + 48 h is NOT_DECIDABLE. **Manager rule (pinned):** if a start after E + 48 h would also hit another refusal, the tool writes nothing; in that case the manager records NOT_DECIDABLE at the deadline as `EXP027_DEADLINE_ND` in the Window record and in the ledger, with the refusal reasons, and does not rerun. The record is the manager's dated line, not a tool output.
- Fixed-instant steps (the count job, the read) run as host-side MiScusi jobs, never as session crons alone.

### 4.4 Walk 2 coverage: job #1036 and its successor (operational, not a merge condition)

Recorded 2026-10-16 (QP-PRE ruling 4). Job #1036 (the walk-2 walker, `forward-walk2.sh`) was extended on 2026-10-16 ~22:59Z to the 14-day MiScusi maximum and now ends ~2026-10-29T19:20Z. Credits: 217,548 of 11,825,000 used through 2026-10-16T21 (cap sufficient to 2026-11-16). Because 14 days is MiScusi's maximum, **successor walk-2 jobs (the same script, resuming from its checkpoint) must be running before ~2026-10-29T19:20Z** to cover `[W0, E + 3 h)` (E is at most 2026-11-15T22; E + 3 h at most 2026-11-16T01); a second is needed if W0 is after about 2026-10-23T16Z, because 14 days is the maximum (a job started before 2026-10-29T19:20Z ends by about 2026-11-12T19:20Z, and E + 3 h can be as late as 2026-11-16T01). They are host-side MiScusi jobs (chained or scheduled), not session crons. If an hour in `[W0, E + 3 h)` is missing, the read is NOT_DECIDABLE under section 7.1.5 or 8.1. The job id, git ref and credits of each successor go in the Window record as dated lines, `EXP027_WALK2_JOB_2` and, if needed, `EXP027_WALK2_JOB_3`.

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
| E1 penalty | **mode `EXP027_E1_MODE: B`** (section 5.1.1): per filled, non-stranded trip max(haircut, `E1_FIXED_LAMPORTS` 1,920,110 lamports). No E1 record | DEC-021 Amendment 7 (`fc8c522`); DEC-030 §3 |
| Flat fail leg | 0.85·pnl − 0.15·send | DEC-021 §1 |
| Pressure fail leg | p = σ(c + 0.8·ln(1+ssb) + 0.35·ln(1+nearby/1e9)), c refit per cell so mean p over the cell's fills = 0.289; P&L = (1−p)·pnl − p·send; ssb = buys in slot x; nearby = buy SOL over slots [x − round(2/slot_s), x] | DEC-021 §1; #673 |
| Strand class (iii) | section 5.2; auto-stranded is the deciding version | QP-FU §1a; QP-3 |

### 5.1.1 E1 mode: B (owner 2026-10-17; DEC-021 Amendment 7)

E1 choice disclosure: the chooser knew #1059's breached result (faa3192 r̄ +35,993, which gives a zero penalty under (b)). Option B uses only the pre-seal n = 8 figure. If Option A is chosen, this read is reported compromised. Option B was chosen (owner 2026-10-17; DEC-021 Amendment 7).

QP ruled (2026-10-16 ~21:3xZ) that E1 cannot run for EXP-027: the tip tape follows the Forward walk row's seals (ledger 2026-10-15 entry, "never a back door") and E1 would price post-s0 reserves of pools in `[10-02T15, 10-16T01)` (D2) and probably the first 360 s of migrate entries (D3). The owner chose **Option B** on 2026-10-17 (MiScusi q_p-l6lP4dr97IYQ, which replaced q_Z3dGvhO1wJ87wg; notebook n__wAwHxj9c9Oc2Q), recorded as **DEC-021 Amendment 7** (merged `fc8c522`, #687). `EXP027_E1_MODE: B` equals the tool's constant (`E1_MODE = "B"`, #691 `1b93610`).

- **B (chosen).** Each filled, non-stranded trip loses max(haircut, `E1_FIXED_LAMPORTS`), **`E1_FIXED_LAMPORTS` = 1,920,110 = 5 × 384,022**. The charge is a pinned constant in the tool; no E1 record exists and `EXP027_E1_RECORD` is refused. Provenance (DEC-021 Amendment 7, "Provenance of 384,022"): the mean live-minus-sim gap on build faa3192's closed round trips is −384,022 lamports per trade, **n = 8** (median −209,670; the mean is −3,072,179 / 8 = −384,022.375, published as the integer 384,022), at **0.05 SOL = 50,000,000 lamports** stake, scaled ×5 to 0.25 SOL. Source file `ARTIFACTS/lab/probe-calibration-2026-10-06.md`, section "faa3192, per trade (live pnl vs tape-sim pnl, lamports)", sha256 `3143869e0e61b4c85053a8b8873c41f5b537098ae057a678a66ba338b94cc12b` (at origin/main `a8b7c08`); first stated at DEC-021:54. **n = 8 is disclosed.** Power at N = 20, k = 1 at 100 / 50 / 37.9 / 0 % of the effect: .5390 / .2245 / .1650 / .0195 (k = 2: .4105 / .1380 / .1070 / .0110). Source: `/data/mal/hunt-1008/push-1016/hunt6/r4b-readtool/power5.py@6af504b139a01d24a3de441332bd5a97ba68379f34cf090afd090444fc59cbe9` and its output `/data/mal/hunt-1008/push-1016/hunt6/r4b-readtool/power5.out@47916559bf862169ca397e0922fa44a75419799c512e6d43b5ecf9a4f74c19c3`.
- **A (not chosen).** Haircut only. Had A been chosen, this read would be reported compromised (the disclosure above).
- **record** (not used): an E1 record with n >= 20; not runnable (QP ruling above).

Section 9's deciding table is computed on the draft's stack; under B the figures are the B table of section 9 (source `/data/mal/hunt-1008/push-1016/hunt6/r4b-readtool/power5.py@6af504b139a01d24a3de441332bd5a97ba68379f34cf090afd090444fc59cbe9`, output `/data/mal/hunt-1008/push-1016/hunt6/r4b-readtool/power5.out@47916559bf862169ca397e0922fa44a75419799c512e6d43b5ecf9a4f74c19c3`).

### 5.2 Rent: the deciding stack is pinned without rent (QP-FU §1a, conditions verbatim)

Quant-proof ruled "Rent: YES, the deciding stack is pinned without rent", on the evidence that the sell and `close_token_account` run in one transaction (61 of 61 probe trips refunded, net 0; 19 more calibration trips net 0). The conditions, verbatim:

> - (i) The EXP pins the sha256 of the sell builder that would trade r4-b, and requires the close in the same transaction. Without it, rent comes back into the deciding stack.
> - (ii) The rent leg stays as report-only.
> - (iii) Rent can strand on three kinds of position. Each is charged the full stake, the buy send, no sell send, and 2,039,280 rent *(the figure is superseded for Token-2022 and unknown-program mints by the per-program rent below; the quote is kept verbatim)*:
>   - an unpriced exit;
>   - a base mint with a freeze authority or a Token-2022 extension that blocks close (for example withheld transfer fees);
>   - simulated proceeds below one send fee.
>   The forward builder flags the first two from data known at decision time.

- **(i) as pinned here.** Sell builder: **`tools/probe_live.py`**, sha256 `b188a40bbaa358e3b3a1e3fdf01dace12fba80aca4e3327b11db08280285bce3` (`EXP027_SELL_BUILDER`, `path@sha256`, relative to the repository root at `origin/main`). Its sell path builds `tx.sell_instructions(...)` plus `close_token_account(base_ata, user, base_tp)` into **one** message (`Message.new_with_blockhash(ixs, ...)`), where `base_tp` is the mint's own token program, so a Token-2022 account is sold and closed in the same transaction. The probe trips ran on earlier builds (`8a6849b`, `faa3192`, `7004b16`) whose sell path has the same sell + close single message (`faa3192:684`, `8a6849b:628`); #662 (`db4af22`, 2026-10-16T16:48Z, after the probe) changed only the priority source, and the pinned bytes also include #422 (`e93731e`, 2026-10-06T19:02Z). **The pinned file is therefore not the file that produced the 61-of-61 refunds.** **Binding on a live trial:** a live r4-b trial must sell through this pinned path, or through a builder proven by test to sell and close in one transaction for both token programs. Otherwise the rent-0 deciding stack does not carry over to that trial. The sell builds its instructions with `tools/pumpswap_tx.py` (sha256 recorded in section 2.2). `pinned_file` resolves a relative path against the repository root (#685). **The manager's merge of this file is that confirmation** (a different builder would replace the path and sha256; the file must perform the sell and the ATA close in one transaction). **Evidence for in-tx close on Token-2022:** `ARTIFACTS/lab/probe-final-2026-10-07.md:234` found no token accounts remaining under either the Token or the Token-2022 program (`getTokenAccountsByOwner`, slot 454261472). **Token program of the probe trips [inferred, not checked trip by trip]:** the 1,513,840 lamports charged on all 61 trips is the 170-byte Token-2022 ATA minimum (QP on #684), so the 61 trips were Token-2022 ATAs, assuming the rent rate did not change between 2026-10-04 and 2026-10-16. If the pin is not valid at merge, `EXP027_RENT_DECIDING`'s second clause applies (the per-program rent per filled trip, deciding), and power falls to about .0725 at the central effect (QP-FU §3, N = 20, k = 1, computed on block sums).
- **(iii) as pinned here.** A strand-class trip's deciding P&L = −250,000,000 − one send − the stranded rent of the mint's token program, below (no sell send). **Auto-stranded is the deciding version** (QP-3): simulated proceeds below one send fee count as stranded, and the threshold follows the deciding send fee (505,000, or 160,000 under section 5.3); the no-auto version is report-only (#673 `round_trip(auto_stranded=True)`). An unpriced exit is stranded. **Freeze authority and Token-2022 extensions: source pinned (QP-4 §7).** A host-side MiScusi job (`tools/mint_account_fetch.py`) fetches **each member mint account once** by RPC (`getAccountInfo`, jsonParsed), records `fetch_ok`, `owner_program`, the freeze authority and the Token-2022 extensions, and writes them with the `fetch_slot` to a sha256-pinned file (`EXP027_MINT_ACCOUNTS`, a Window-record line). A mint with a freeze authority or a close-blocking extension (for example withheld transfer fees) is strand-class. **A failed fetch makes the mint strand-class; it is never a refusal.** This is a **structure read made by RPC**, not a walk-2 outcome; its RPC credits are recorded (job, credits, calls) in the ledger and the MiScusi notebook, not in the Window record (`EXP027_MINT_ACCOUNTS` carries the `path@sha256` only). Any report-only `members` run before W0 is recorded there too, as `EXP027_MEMBERS_PREW0` (job id, sha256, credits). QP estimates about 5.1% of fills are rug-class, so (iii) costs about 0.04 pp per trade.
- **(ii)** The report-only rent leg (per filled trip, priced by the mint's token program as below) is reported, never deciding.

**Rent by token program [pinned] (QP verdict on #684 at `2b3d537`, comment 6226181818, merged `aef7bea`).** Stranded and report-only rent depend on the mint's token program.

- **Token-2022, and any mint whose program is unknown: 2,074,080 lamports** = (170 + 128) × 6960, a Token-2022 ATA with ImmutableOwner. "Unknown" means a failed fetch, a malformed record, another owner, or a mint missing from the mint-account file.
- **A successfully fetched SPL Token mint: 2,039,280 lamports** = (165 + 128) × 6960.

Both figures are higher than the rent the chain charges now. On 2026-10-16, `getMinimumBalanceForRentExemption` returned 1,513,840 for 170 bytes and 1,488,440 for 165 bytes; the live probe also paid exactly 1,513,840 per buy on all 61 trips (`ARTIFACTS/lab/probe-final-2026-10-07.md:163`). **The higher figures were kept on purpose: they are conservative, and this read never re-bases them downward.** What that costs: each stranded Token-2022 position is charged 560,240 lamports over chain rent (0.224% of stake); over the old 2,039,280 figure the difference is 34,800 lamports, about 0.0007 pp per trade at QP's 5.1% rug-class share; the report-only rent leg charges 2,074,080 per filled Token-2022 trip and so overstates rent by up to 560,240 per trip. **Deciding rent stays 0 per filled trip under (i)-(iii).** The constants `RENT_STRANDED_T22` and `RENT_STRANDED_SPL` are in `tools/r4b_read.py`; `tools/r4b_frozen.py` keeps 2,039,280 (the E-c replay uses it; E-c md5 `54052f798013a71e3a9f40e0d6830ac1` is unchanged).

**Token-2022 allowlist and mint-account disclosures [pinned] (QP on #682 and #683).**

1. **Allowlist and its provenance.** `TOKEN2022_EXT_ALLOWLIST = {metadataPointer, tokenMetadata}`, fixed in `tools/r4b_read.py` and **fixed at W0**: after W0 it never changes, and any other extension type is denied by default (a Token-2022 mint is not strand-class only if every extension is in the set, there is no freeze authority, and the fetch succeeded). Provenance: an outcome-blind census of N = 300 exploration mints (`tools/mint_account_fetch.py`, mode census, 300 `getAccountInfo` calls, about 300 Helius credits, 0 errors, fetch slots 457794410–457794548): 295 are Token-2022 with exactly `metadataPointer` + `tokenMetadata`, 5 are classic SPL Token, 0 have a freeze authority, 0 have a mint authority. Files under `/data/mal/hunt-1008/push-1016/hunt6/r4b-readtool/mint-census/`: `summary.json` sha256 `5b8c4f16e190b2b7b1a9fed4a0386ef25ca96b0e4ed405c9410f94da7521e6f1`, `mints.jsonl` `faf269ff3a52ec3791ef1931c4cf6226589a0ac780a89c119eebf749f3ffc6d1`, `input_mints.txt` `b308e72774979d4784256b203940150cf85f51a6afe38e301c67f0286228e2fd`. A pre-W0 report-only `members` run may revise the allowlist (pre-W0 only, with QP) if a material share shows a new extension or a freeze authority.
2. **The `r4b_read.py` sha256 pin is taken at or after the allowlist's merge (#683 `ff2e398`)**, here `0563de0495c0bbd6ec2a085ddfa5b795307bb24e91df543d467cdd9651297581` (section 2.2; taken at `1b93610`, after the E1 mode B flip, #691).
3. **The read reports** the strand-class share, the failed-fetch share, and the Token-2022 vs SPL Token strand split, all from the post-E members fetch.
4. **Post-E fetch timing.** The binding `members` run is after E, once, on the full member list for `[H0, E]` (member list from the structure-only layout build: mint and `mbt` only), as a MiScusi job into a fresh directory; its sha256 is pinned in `EXP027_MINT_ACCOUNTS` (`path@sha256`) and its job id and credits are recorded in the ledger and the MiScusi notebook. A fetch after the decision **would miss a freeze authority revoked between decision and fetch**; for pump mints that is nil (no freeze authority is ever set), and it is stated rather than assumed away.
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
5. Any allowlisted hour is still bad after the one outcome-blind re-walk → NOT_DECIDABLE (§7.1.4); the 5% allowance of EXP-022 §10 does not apply. Any hour in `[W0, E + 1 h)` that is missing is also NOT_DECIDABLE (FROZEN §4 refusal; stricter).
6. An A3 structure-monitor halt (`tools/pump_structure_monitor.py`, the six flags of EXP-022 §11) fires between W0 and E.
7. A pressure gap: any counted trade lacks its pressure-leg P&L (DEC-014, line 70).
8. E1: under mode `record`, no E1 record with n ≥ 20 before the read (DEC-021 Am.1 §4); under mode A or B (section 5.1.1) this clause does not apply.
9. The read's decision-row md5 (`(mint, T, sd)`) for any mark differs from the count job's (PLAN §4).
10. An hour outside the allowlist is opened by the read (EXP-025 R-terms; checklist §1 j).

**Which refusals bind.** A count-job or read-tool refusal whose only reasons are unsealed or unverified hours, for an hour that later sealed and strict-verified, does not bind. Every other refusal binds (§7.1.4) (QP on #676, comment 6224082249 (b)).

**Seals never turn a FAIL into NOT_DECIDABLE.** A breach (section 11) leaves the verdict as computed and labels the read compromised.

### 7.2 Kill rule

A FAIL **retires r4-b**: no retune, no refit, no change to K, stake or threshold, no re-read on any hours, no rescue hours (FROZEN:125; DEC-030 §3). A NOT_DECIDABLE also spends the window; r4-b is never re-filed on walk 2. A re-filing of any variant is a new book under a new EXP and, in slot 5, counts toward the 3-book cap.

## 8. Precount and read-tool refusals

### 8.1 Precount (count job, outcome-blind; re-checked by the read) — refuse if

- any hour in the allowlist `[2026-10-16T03, E + 3 h)` is not sealed, strict-verified (`exp012_forward.hour_problems(strict=True)`) or sha256-matched, or its parsed rows differ from `verify.jsonl` `content[sub].rows`;
- the PDA match is < 95% of non-Mayhem completes;
- fewer than 100 hour marks have ≥ 10 live tokens;
- a UTC date has graduations but 0 decisions;
- an hour has unmeasurable SPH;
- **a mixed (mint, slot) count > 0**: any mint whose slot holds both null and non-null `tx_index` prints (QP-4 §7);
- **a v0_missing share > 2% on any read date**, the denominator being non-Mayhem, PDA-matched pools with an s0 (QP-3; QP-4 §7). Below that, a pool with missing V0 is a non-member;
- **a member with any null-`vq` print** (null event V on a member pool's print; QP-3; QP-4 §7);
- **a create-row gap: on any complete counted UTC date, more than `NO_CREATE_MAX_SHARE` = 0.06 of member mints have no create row** (`tools/r4b_count.py`; QP ruling 1 on #676: exploration days reach 0.0513; a pinned constant, never tuned on results). Their create-derived constants are NaN downstream;
- `counts.jsonl` lacks any hour of `[H0, E]`, or `extra_hours_loaded` is off (section 4.2).

One tie-breaker throughout: (slot, coalesce(tx_index, −1), event_index, file_row_number) (section 3.2).

It also reports (report-only): null-`tx_index` tie groups (section 3.2) and, per date, the null-`txi` prints on members, the tie groups and tie mints, and the decision rows, top-10 picks and attempts on tie mints (never gating; section 10.7), the V0-band share, the v0_missing share per date, rows dropped by the s0 + 600 s rule, and live tokens and decision rows per mark. Reserve-discontinuity counts (section 3.5) are reported inside the read only.

### 8.2 Read tool — refuse unless

- each pinned line matches once, no value is a placeholder, and this file is clean against HEAD (tracked and equal to HEAD); **and the tamper checks of section 0.5 pass** (registration commit and ancestry, EXP blob equal to `origin/main`'s, body-prefix sha256 equal to the ledger value, additions only inside the Window record, one line per commit in the stated format, commit times before `started`, exact `config.key` tries match, first-parent chain, formats and line endings; the `--check-window` dry run is a manager step, section 0.5);
- every code sha256 and model md5 of section 2.2 matches; E-a..E-d md5s are recorded; the decoder blobs equal walk 2's job ref;
- `EXP027_E1_MODE` equals the tool's `E1_MODE` (and, in mode `record` only, the E1 record exists with n ≥ 20); `EXP027_WALK_DIR` equals `/data/mal/blocks/forward-1016` and is the directory read; the sell-builder and A3-record files match their `path@sha256`; input-view sha256s and the `data/tries.jsonl` entry exist; the mint-account file of section 5.2 (iii) exists and matches its pinned sha256;
- only allowlisted hours `[2026-10-16T03, E + 3 h)` are opened; a bad line refuses; the E + 48 h deadline holds (section 4.3);
- an O_EXCL lock; `/data/mal/r4b/READS.jsonl` with started / completed / aborted; no second run; no CLI override; the verdict goes to stderr before any file write;
- fills join outcomes only after E + 1 h is sealed.

**Catalog.** The walk-2 Owner cell is "reserved", so `mal_catalog.check_read` denies the read today. **Pinned allowlist, no catalog change** (QP on #678 Q6, comment 6224659304), conditional on R5 (`EXP027_WALK_DIR`) and the allowlist `[2026-10-16T03, E + 3 h)`. `r4b_read.py` does not call `mal_catalog`.

## 9. Power (report-only; QP's simulations)

Conventions: each cell is P(full pass): all 4 cells, the larger p decides, the full gate plus both drop terms, 2,000 day resamples, seed 1. Effect s is a share of the deciding-stack net mean; the missing part comes off in SOL per trade.

**Deciding table: the pinned stack (per-attempt block-mean t, no rent, auto-stranded, 505k, E1 mode B), N = 20** (QP-FINAL-675 at `9772d55`; source `/data/mal/hunt-1008/push-1016/hunt6/r4b-readtool/power5.py@6af504b139a01d24a3de441332bd5a97ba68379f34cf090afd090444fc59cbe9`, output `/data/mal/hunt-1008/push-1016/hunt6/r4b-readtool/power5.out@47916559bf862169ca397e0922fa44a75419799c512e6d43b5ecf9a4f74c19c3`):

| Effect | k=1 | k=2 |
|---|---|---|
| 100% | .5390 | .4105 |
| 50% | .2245 | .1380 |
| central 37.9% | .1650 | .1070 |
| null | .0195 | .0110 |

**Option A stack (not pinned; record)** (per-attempt block-mean t, no rent, auto-stranded, 505k, haircut only; QP-4 §1):

| Effect | k=1 | k=2 |
|---|---|---|
| 100% | .6575 | .5265 |
| 50% | .3245 | .2265 |
| central 37.9% | .2535 | .1660 |
| null | .0195 | .0110 |

**E1 mode.** The pinned mode is B (section 5.1.1), so the first table decides what is quoted; the Option A table is kept for the record. Power is report-only and decides nothing.

**k-dependence.** k = 2 cuts every figure by about a third (QP-FU §3; the pinned table: .1650 → .1070 at the central effect). Plan for k = 2 (QP §3).

**160k leg.** The 160k figures have **not yet been recomputed on per-attempt means**; QP-4 §6: "The 160k figures must be rerun on means before they are quoted." None is quoted here. If that leg is later quoted, the rerun's `path@sha256` goes in the Window record as `EXP027_POWER_160K`.

**Superseded tables (kept for the record, not for decisions).** All were computed with the day t on block sums:
- QP §3 (stack then with rent), k = 1, W = 7 / 10 / 14 / 21: 100% .0945 / .1555 / .2335 / .3435; 50% .0470 / .0655 / .0780 / .0975; 37.9% .0380 / .0530 / .0570 / .0730; null .0200 / .0300 / .0250 / .0245.
- QP-FU §1b (no rent, 505k, no auto-stranded), W = 20, k = 1 / k = 2: 100% .6325 / .4945; 50% .3095 / .1980; 37.9% .2400 / .1520; null .0220.
- QP-3 (no rent, auto-stranded, 505k), N = 20, k = 1 / k = 2: 100% .6160 / .4825; 50% .2985 / .1855; 37.9% .2255 / .1460; null .0220 / .0125.

**Owner-facing sentence (QP-FINAL-675, exact):**

> "r4-b has zero out-of-sample trades and has not cleared the promotion gate; its exploration result comes from the days it was picked on. On the pinned stack (505,000-lamport sends, rent refunded, stranded positions charged in full, and an E1 charge of at least 1,920,110 lamports per filled trip), a 20-day forward read passes about 16.5 % of the time at the judge's shrunk estimate of the edge and about 54 % if the whole measured edge is real (about 10.7 to 41 % if a second push book registers). A book with no edge passes about 2 % of the time. A FAIL retires the book, and unless nearly all of the measured edge is real, FAIL is the likely outcome."

**Superseded (Option A stack): the QP-4 §6 sentence, exact:**

> "r4-b has zero out-of-sample trades and has not cleared the promotion gate; its exploration result comes from the days it was picked on. On the pinned stack (505,000-lamport sends, rent refunded, stranded positions charged in full), a 20-day forward read passes about 25 % of the time at the judge's shrunk estimate of the edge and about 66 % if the whole measured edge is real (about 17 to 53 % if a second push book registers). A book with no edge passes about 2 % of the time. A FAIL retires the book, and unless nearly all of the measured edge is real, FAIL is the likely outcome."

## 10. Disclosures (DEC-030 §3; ledger rule 3)

### 10.1 EXP-025's non-owner read

EXP-025's non-owner read of `[2026-10-16T01, 2026-10-24T02)` is disclosed. Its Look 1 was NOT_DECIDABLE (R8, P5, 2026-10-16T04:51:17Z). Its end state is not yet recorded: "Look 2 refused" is recorded only after the R8 run after 2026-10-26T12:00Z (LAB_STATE Now; ledger (3a)). Until then its seals and non-owner reads stand; this file does not call EXP-025 ended. r4-b computes no C1-NF label, fill, exit or P&L and prices no C1-NF decision (EXP-025 §4 seal), and joins no synthetic class to any outcome.

### 10.2 The C1-NF shadow and monitor

The C1-NF shadow (MiScusi #658) and the monitor (#662) are running over walk-2 hours. **This file does not read their output**, and nor does any r4-b tool.

### 10.3 The 2026-10-16 low-severity breach

On 2026-10-16 a builder's scan read row TYPES and MINT COUNTS of `migrations-2026-10-16T01` and `migrations-2026-10-16T02` (forward-1002 directory; hours inside the Forward walk 2 row; ledger changelog, 2026-10-16, W = 2026-10-16T18:56Z). Quant-proof ruled no read compromised. **Those hours are before W0 and are never counted.** They are used as feature buffer (section 3.4), read from `forward-1016` only, and that scan's output is never reused.

**The #670 breach, as the ledger records it** (changelog 2026-10-16, W = 2026-10-16T18:56Z): walk-2 hours 2026-10-16T01 and T02 were never counted and are not opened by the read or the count job; the scan's output is never reused.

### 10.3a The #1059 breach and EXP-026 (D2/D3)

The ledger records (changelog 2026-10-16, W = 2026-10-17T00:12:01Z) that MiScusi job #1059 ran the EXP-022 §8 E1 command on the tip tape at 2026-10-16T07:25:46Z and breached D2 (and D3 by fail-closed reading); its output is void as E1 for any gate. **EXP-027 only discloses it** (no pool in any counted window was priced) and is compromised only if it takes Option A; it takes Option B. E1 choice disclosure: the chooser knew #1059's breached result (faa3192 r̄ +35,993, which gives a zero penalty under (b)). Option B uses only the pre-seal n = 8 figure. If Option A is chosen, this read is reported compromised. Option B was chosen (owner 2026-10-17; DEC-021 Amendment 7).

EXP-026 (H5-BOOSTCLOCK v2) was **withdrawn before W0 on 2026-10-17** (EXP-026 Withdrawal record; ledger changelog 2026-10-17). It never named or counted any hour, so it did not overlap walk-2 hours with this book and no overlap disclosure is needed.

### 10.4 Exploration evidence is in-sample for selection

- **Zero out-of-sample trades.** Every figure is from exploration days that were in-sample for selection: **the best of 6 l3 variants, about 25 hunts on this window, deflated Sharpe 0.6224** on the deciding 1.9 s stack (FROZEN:175; QP §header). Under R1 there is no backward screen (DEC-030 §2, R1 "Cost"); the walk-2 read alone carries the winner's-curse risk.
- **In-sample day-level t, two stacks.** With rent and on block sums, the test fails at 0.025: pressure 1.3 s t 1.9048, p 0.034; pressure 1.9 s p 0.0276 (QP §1, 27 traded days, after dropping 09-11 and mint 16894). On the pinned stack (per-attempt block means, no rent, auto-stranded, 505k), pressure 1.3 s gives t 2.9459, p 0.0034 (QP-4 §1). Both are in-sample for selection and are not evidence.
- **In-sample cells on the VERIFY stack incl. C8** (505k, with rent, no auto-strand, day t on calendar dates; not the pinned stack; dropped sample n 2,879; C8 changes nothing in the dropped cells) [measured, #673 = r4b-verify REPORT]: mean % / total SOL / CI90 lo SOL per trade / ex-top-3 / ex-best-day / day t — flat 1.3 s 1.605794 / 11.557703 / 0.001337 / 8.303159 / 8.988099 / 2.112063; flat 1.9 s 1.603981 / 11.54465 / 0.00133 / 8.290106 / 8.97681 / 2.110699; pressure 1.3 s 1.215049 / 8.745313 / 0.000836 / 5.971927 / 6.565793 / 1.901402; pressure 1.9 s 1.275503 / 9.18043 / 0.000969 / 6.40146 / 7.009824 / 2.003766.
- **fresh-0903** is −2.304792 SOL over 740 trades (QP §1, deciding stack); FROZEN reports −0.80917 SOL (deciding 1.9 s, 10-08 run), and it moved +0.78 → −0.14 between runs (section 2.4, stack as in the repro report). Three figures, three stacks or runs; all negative or near zero on the deciding stack.
- **maxDD 3.149798 SOL**, more than the 2.5 SOL open (QP §1).
- **The noise-draw disclosure** of section 2.4.
- **Multiplicity:** DEC-014 m. This EXP adds 1 to m (Appendix A): r4-b's development read Aug–Sep exploration dates, counted conservatively as reading the 27 non-P1 dates.
- **Rugs:** 5.13% of positions lost ≥ 90% on the no-fail leg in development (FROZEN §7).
- **The 10-02 upgrade and 10-13 redeploy** are not in development.

### 10.4a Exit pricing and invalid states (QP on #679, #680)

- **Exit price when the state goes ≤ 0 [pinned].** A held token whose exit state is invalid (reserve ≤ 0, tokens ≥ the pool's base, non-finite) is **a total loss of stake, priced stranded** (full stake, buy send, no sell send, the per-program rent of section 5.2); no last-valid-bid option.
- **Exit cadence [pinned].** The book exits at **hour marks only** (`UNIVERSE_EXITS_AT_MARKS_ONLY`): a token that leaves the universe between marks is sold at the next mark by the rank / gap rule (`xs_book`), never in between.
- **C8 / K_TOL [pinned].** An exit is `exit_invalid` (priced stranded) when Qx·Bx < Qe·Be·(1 − K_TOL), `K_TOL = 1e-3` (the constant-product k of a pool does not fall). The worst exploration drift was 2.92e-4; 1e-3 and 1e-2 flag the same single trip. **The rule was set after seeing exploration counts but not tuned to them:** 8 exploration trips sit below 1 − 1e-6 and 1 below 1 − 1e-3 (listed in QP's comment 6224978004). The read reports `exit_invalid` by cause (C2 state, C7, C8) in every cell and, report-only, the count of exits with k-ratio in [1 − 1e-3, 1 − 1e-6).
- **Mint 16894 (post-hoc; QP on #680, OK at `d1f3f0c`, comment 6225091977).** Every trip of mint 16894 in the undropped book (8 trips, 2026-09-11..13; flat P&L = 0.85·pnl − 0.15·send before C8, 505,000 send; T0 and T1 are hour marks; all exits `rank`) [measured, `trades_deciding_raw.parquet` of r4b-verify]:

  | T0 | T1 | exit reason | flat P&L 1.3 s (SOL) | flat P&L 1.9 s (SOL) | k ratio |
  |---|---|---|---|---|---|
  | 09-11T22:00Z | 09-11T23:00Z | rank | +0.164852 | +0.153648 | not below 1 − 1e-3 |
  | 09-12T00:00Z | 09-12T01:00Z | rank | −0.021928 | −0.022158 | not below 1 − 1e-3 |
  | 09-12T06:00Z | 09-12T08:00Z | rank | +0.035737 | +0.035927 | not below 1 − 1e-3 |
  | 09-12T09:00Z | 09-12T11:00Z | rank | −0.012694 | −0.012694 | **0.853789** |
  | 09-12T23:00Z | 09-13T00:00Z | rank | +0.012589 | +0.012589 | not below 1 − 1e-3 |
  | 09-13T02:00Z | 09-13T03:00Z | rank | +0.011478 | +0.011478 | not below 1 − 1e-3 |
  | 09-13T04:00Z | 09-13T06:00Z | rank | +0.049124 | +0.049124 | not below 1 − 1e-3 |
  | 09-13T10:00Z | 09-13T11:00Z | rank | +0.008116 | +0.009236 | not below 1 − 1e-3 |

  (The year is 2026. The k ratio is recorded only for the trip C8 flags; the other seven are not flagged at `K_TOL = 1e-3`, i.e. their ratio is not below 0.999; their exact ratios were not extracted.) **One trip of mint 16894 (flat −0.0127 SOL before C8) fails the k invariant (ratio 0.853789, cause not established).** It is not the "+5.4 SOL runner" of an earlier QP text; QP withdrew those words. Mint 16894 was **dropped in the VERIFY baseline after seeing it**: **C8 does not justify VERIFY's drop of 16894; that drop stays an after-the-fact selection choice.** The undropped totals are labelled report-only. QP's power table excludes the mint. In the forward read there is no drop: a mint 16894-like exit is `exit_invalid` and stranded by the rules above.
- **Part C with C8 on [measured, `scripts/research/r4b_equiv_c.py` at `aef7bea`, run 2026-10-16, with and without C8 on Part C].** Part C (the auto-stranded deciding stack, report only) runs with C8 on. Undropped, C8 strands 1 trip per cell (mint 16894; stranded count 145 → 146 of 2,986): total SOL change flat 1.3 s −0.203778, flat 1.9 s −0.203778, pressure 1.3 s −0.173778, pressure 1.9 s −0.174117; CI90 lower bound per trade flat 1.3 s 0.00365 → 0.00356, pressure 1.3 s 0.002747 → 0.002676. The dropped cells are unchanged. These are in-sample, report-only figures; the undropped figures include a date and a mint that VERIFY chose to drop after seeing them.
- **Entry-state guard.** An invalid entry state is not priced as a fill (#680 `ef5107c`; an exploration-era bug had priced an invalid entry at +97 SOL).

### 10.5 Capital

The book holds up to **2.5 SOL open** (10 × 0.25 SOL). The owner's capital was 1.29 SOL (QP §5), so a live trial would need owner capital beyond that; DEC-020 governs size. **A smaller K or stake is a new book** (QP §5), never a variant of this one.

### 10.6 No claim

Nothing in this file says r4-b makes money. A PASS would be forward simulated paper, not money made, and leads only to the DEC-018 path and the owner's yes.

### 10.7 Null-`tx_index` tie groups in the forward data

The forward order key is total (section 3.2). The read reports, per date and report-only, never gating: null-`txi` prints on members, tie groups and tie mints, and decision rows, top-10 picks and attempts on tie mints (E-d found tie mints are 10.3–14.3% of decision rows in exploration, section 2.3.1). It also reports the mixed (mint, slot) count (a refusal if > 0, section 8.1).

## 11. Seal during the window

- **Before the read**, no person, agent or job computes, opens or prints an r4-b outcome for any mark at or after W0: landing state, guard result, fill, exit state, proceeds, P&L, mean, CI, day or block sign, win or loss, from any source (walk 2, the tip tape, the live listener, RPC, a paper runner).
- **Allowed before the read:** the count job's outputs (section 4.2); the mint-account structure fetch and census of section 5.2 (iii) (RPC only, no walk-2 outcome); hour counts (sealed, verified, bad); the precount; the A3 monitor's flags; the fee-arm verdict read of section 5.3 by a non-r4-b-outcome reader.
- **A breach** is recorded here, dated, as the next unused key `EXP027_BREACH_1` … `EXP027_BREACH_9` (one line each), and the read is reported compromised. It never turns a FAIL into a NOT_DECIDABLE. A compromised read cannot support a live request.

## 12. Source conflicts and how this file resolves them

| Conflict | Resolution |
| --- | --- |
| Checklist §1 c (stop at ≥ 100 trades on ≥ 5 dates, cap W0 + 336 h) vs DEC-030 §1a (≥ N blocks with attempts and ≥ A attempts, cap ≤ 21 d) | **DEC-030 wins** (PLAN §8 item 7). Calendar stop E = W0 + 480 h with N = 20, A = 125 checked in the read, cap = E (QP-4 §5). |
| Checklist §2 (Holm across k) vs DEC-030 §1 (Bonferroni 0.025/k) | **DEC-030 wins**: Bonferroni. |
| Checklist §1 d (deadline E + 48 h) | Not in conflict with DEC-030; adopted. |
| PLAN §4 open ruling: may the count job compute ranks from post-entry prices of held tokens? | Settled by the calendar stop: the count job computes no features, scores, ranks or attempts (section 4.2). |
| PLAN §5 recommends ceil for k vs QP §2 np.round | **QP wins**: np.round (#673 pins it). |
| PLAN §1/§5 SPH per decision hour vs QP §2 "slot_s per day" | **QP-4 §2**: 3600 / median of the date's hourly SPH (section 3.3). |
| PLAN §8 item 3 (H0 = 10-16T03) vs QP §2 (pools ≥ 10-16T01) | **H0 = 10-16T03** (revision 3, QP on #676 and #678: the count job's creates start at 10-16T03, so the 48 h ramp completes at 10-18T03 and `EARLIEST_W0` = 10-18T03). Hours 10-16T01 and T02 are never opened; the breach disclosure (section 10.3) stands. Supersedes revision 2's 10-16T01 / 10-18T01. |
| #673 `day_t` on block sums vs DEC-021 Am.1 §5 (per-attempt block means) | **QP-4 §1**: per-attempt means decide; sums report-only; done, #677 `1eb5d42` (section 6.2). |
| FROZEN universe from graduation + 600 s vs QP D3 condition "decisions ≥ s0+600 s" | s0 + 600 s drop rule, confirmed by QP-4 §3 (section 3.4). |
| #672 PR text (null `tx_index` sorted LAST after a reviewer fix) vs training's coalesce(−1) | **QP-4 §7**: nulls FIRST; the merged `forward_order` (`cf1eaac`) sorts nulls first; mixed (mint, slot) > 0 refuses. |
| LP Deposit/Withdraw: QP §4(d) "exclude or refuse" vs QP-4 §4 | **QP-4 wins**: no exclusion, tolerance "none", discontinuity counts report-only in the read (section 3.5). |
| PLAN §5 haircut product approximation vs #673 VERIFY formula | #673 formula pinned (section 5.1). |
| FROZEN:123 target n 3,002 vs rebuilt n 2,986 | Amended to rb2 (section 2.3), per QP §2. |
| FROZEN:104 halves bar | Report-only on walk 2 (section 5.4); QP's power tables carry no halves bar. |
| QP owner line (first table) vs later stacks | QP-FINAL-675's exact sentence (E1 mode B figures) is used (section 9); the QP-4 §6 sentence and the earlier tables are superseded. |
| 160k power (QP-FU §2, QP-3) on block sums | Not quoted until rerun on means (QP-4 §6). |

## 13. TO FILL

**Before merge** (revisions 3 and 4 filled everything except these):
1. **Done (revision 7): `EXP027_E1_MODE: B`**, the owner's line of 2026-10-17 (DEC-021 Amendment 7, `fc8c522`); the `E1_MODE = "B"` flip is #691 (`1b93610`); the 1,920,110 provenance is in section 5.1.1.
2. **Done (revision 7): the DEC-030 owner line**: route R1, 2026-10-17T03:52:39Z, MiScusi q_6FkDYV4cPVnknQ / q__jVaqpxB937kEg (DEC-030 §4; merged `fe8586f`); `EXP027_ROUTE: R1`.
3. **Done (revision 7): `EXP027_SHA256_R4B_READ`** = `0563de0495c0bbd6ec2a085ddfa5b795307bb24e91df543d467cdd9651297581` at `1b93610`. Done: QP OK 6229278039 and Reviewer OK 6229294536 on #691 at 8ae2386, whose `tools/r4b_read.py` is byte-identical to the pin.
4. **Done:** the sell builder confirmation (section 5.2 (i)): `tools/probe_live.py` is pinned; the manager's merge of this file is the confirmation.
5. **Done:** the ledger edit of Appendix A (in this PR, 9772d55).
6. The merge instant in `[2026-10-18T02:00:00Z, 2026-10-26T21:59:59Z]`, and the manager then writes `EXP027_REG_COMMIT` and `EXP027_PREFIX_SHA256`, exactly in the Appendix A form, in a reviewed ledger commit after the merge and before W0, plus a MiScusi notebook entry (section 0.5); neither exists before the merge.

**Not a merge condition (moved out of "before merge", QP-PRE ruling 4):** the walk-2 coverage of section 4.4. Job #1036 was extended on 2026-10-16 ~22:59Z to the 14-day MiScusi maximum (ends ~2026-10-29T19:20Z); credits 217,548 of 11,825,000 used through 2026-10-16T21 (the cap covers to 2026-11-16). Successor walk-2 jobs resuming from the checkpoint must be running **before ~2026-10-29T19:20Z** (and a second, if W0 is after about 2026-10-23T16Z) to cover `[W0, E + 3 h)`; their job ids and refs are recorded in the Window record as `EXP027_WALK2_JOB_2` and `EXP027_WALK2_JOB_3`. If a missing hour results, the read is NOT_DECIDABLE under section 7.1.5 or 8.1.

**Done in revisions 3 and 4:** layout, count, features, frozen, score and book sha256s (section 2.2); E-a..E-d md5s and the E-d record, with the sha256s of the E-d script and wrapper, the tie-mint definition and list sha256, the whole-day md5s and the tie disclosure (2.3, 2.3.1); `EXP027_TIERS_SHA256`; `EXP027_WALK_DIR`; `EXP027_SELL_BUILDER` with the corrected provenance, the live-trial condition and the `pumpswap_tx.py` sha256; `EXP027_DECODER_REF` and `EXP027_TRIES_ENTRY` (the r4-b family line in `data/tries.jsonl`); the rb2 sha256s (2.2); the catalog route (8.2); per-program rent; Token-2022 allowlist and census provenance; H0 / `EARLIEST_W0` / allowlist `[H0, E + 3 h)`; `NO_CREATE_MAX_SHARE`; as-of mint filters and their impact figures; k-tie, C8 / K_TOL, the mint 16894 trip list and the Part C counts; the Mayhem and refusal-binding sentences; k pinned at the first read, not at registration (0.4); the Window-record mechanism (0.5); `book_stats` per-attempt-mean `t_block` (#677, sha256 in 2.2).

**Window record** (the last section of this file; dated, manager, from the merge record and job records only; each line `KEY: value (appended <UTC minute>)`, once, one commit per line; section 0.5): `EXP027_MERGE_INSTANT`, `EXP027_W0`, `EXP027_K` (at the first push-family read, before its lock), `EXP027_INPUT_VIEWS`, `EXP027_MINT_ACCOUNTS` (`path@sha256` of the post-E members file only; its job id and RPC credits go to the ledger and the MiScusi notebook), `EXP027_FEE_ARM` (`NONE` or `path@sha256`), `EXP027_A3_RECORD` (`path@sha256`, covering `[W0, E]`); plus `EXP027_WALK2_JOB_2` and `EXP027_WALK2_JOB_3` (successor walk-2 jobs: job id, git ref, credits; section 4.4), `EXP027_POWER_160K` (`path@sha256`, only if that leg is to be quoted; section 9), `EXP027_MEMBERS_PREW0` (the pre-W0 report-only `members` run: job id, sha256, credits), `EXP027_BREACH_1` … `EXP027_BREACH_9` (section 11) and `EXP027_DEADLINE_ND` (section 4.3). This is the closed key list of section 0.5 (mode B: no `EXP027_E1_RECORD`; the tool refuses it); any other record goes only to the ledger and the MiScusi notebook.

**Settled by QP (no longer open):** LP tolerance "none" (section 3.5); slot_s rule (3.3); null ordering and refusals (3.2, 8.1); D3 drop rule (3.4); history NaN (3.4); `f_c_v` (3.6); count-job ranks (4.2, by the calendar stop); E1 ×5 (5.1); lag floor 0 (5.3); strand-class source (5.2); block means (6.2); owner sentence (9); catalog route (8.2); sell builder (5.2); window-record mechanism (0.5).

## Appendix A. Proposed ledger edit (Forward walk 2 Status cell; DEC-030 §2a)

Applied in this PR (9772d55): the Forward walk 2 Status sub-entry and the changelog line, route R1 filled, `<after merge>` placeholders for the post-merge values. DEC-030 has merged with the owner's line (`fe8586f`); this text is added to the Forward walk 2 row's Status cell **in this PR**, with the bracketed values filled from the merge record, and this PR does not merge without it. **The text carries placeholders for `EXP027_REG_COMMIT <sha>` and `EXP027_PREFIX_SHA256 <sha256>`**: neither value can exist before the merge, so the manager fills them in a reviewed ledger commit to main after the merge and before W0 (section 0.5); the tool finds them wherever the token appears and requires all occurrences to agree.

> **EXP-027 (r4-b, push-family book 1, DEC-030 slot 5), <merge date> ([EXP-027](../EXP/EXP-027-r4b-walk2-prereg.md); counted-window sub-entry under Rules item 5, not an owner):** W0 `<W0>` (the first UTC hour whose start is at or after the registration commit's committer time `<merge instant, date -u>`, floored at 2026-10-18T03; never moved); calendar stop E = W0 + 480 h = `<E>` (`EXP027_STOP_RULE`); the read requires at least 20 blocks holding an attempt and at least 125 attempts, else NOT_DECIDABLE with no extension; window cap = E; exit horizon 1 h (the window with its exit horizon ends by `<E + 1 h>`, at or before 2026-11-16T01); k at registration `<k>` (ledger information only, not a pinned line; `EXP027_K` is appended once at the first push-family read instant, before that read's lock); route `<R1 | R2>`; `EXP027_REG_COMMIT <sha>` and `EXP027_PREFIX_SHA256 <sha256>` (section 0.5). Feature-buffer hours `[2026-10-16T03, W0)` (H0 = 2026-10-16T03) are read for features only and never counted; 2026-10-16T01 and T02 are not opened (2026-10-16 breach disclosure stands). Reads `/data/mal/blocks/forward-1016` only, hours `[2026-10-16T03, E + 3 h)`; universe pools created at or after 2026-10-16T03. Adds 1 to m (**m >= 16**; 18 on DEC-030 §2's count of 17). N and A found at the read: *(added by the read)*. Section 5.3 fee-arm record: *(added before the read lock, or "none: 505k")*. First push-family read instant (freezes k; `EXP027_K` appended from the ledger's count at that instant): *(added when it happens)*.

Changelog line to add with it:

> - <date> (W = <date -u>; [EXP-027](../EXP/EXP-027-r4b-walk2-prereg.md), DEC-030 slot 5; no row's data was opened): **EXP-027 (r4-b) registered as push-family book 1 on Forward walk 2**, counted-window sub-entry in the Status cell (Rules item 5). W0 `<W0>`, E = cap `<W0 + 480 h>`, exit horizon 1 h, k at registration `<k>` (information only). `EXP027_REG_COMMIT <sha>`, `EXP027_PREFIX_SHA256 <sha256>`. m +1 (m >= 16). The Owner cell stays reserved.

## Sources

- DEC-030, DEC-021 Amendments 6 and 7 (`origin/main` `1b93610`); HOLDOUT_LEDGER Rules item 5 (same branch) and the Forward walk 2 row; the 2026-10-16 changelog entries on `main`.
- `/data/mal/hunt-1008/push-1016/WALK2-PREREG-CHECKLIST.md`; `/data/mal/hunt-1008/laya-opt/FROZEN-r4-b.md`; `/data/mal/hunt-1008/push-1016/hunt6/r4b-verify/QP-RULING-1016.md` (both rulings); `/data/mal/hunt-1008/push-1016/hunt6/r4b-readtool/PLAN.md`; `/data/mal/hunt-1008/JUDGE-3.md:248`.
- PR #672 and PR #673 bodies; `tools/r4b_book.py` and `tools/r4b_frozen.py` at #673's head.
- EXP-022 (§0, §10, §11 structure), EXP-025 (§4 seal, breach amendment), EXP-026 (§0 structure), DEC-014 (line 70), DEC-019 Amendment 2 (Tests A and B), DEC-021 Amendment 1 §4–§5.
- No sealed data, walk-2 hour, forward-paper P&L, or canary, shadow or probe outcome record was opened to write this file.

## Window record

*(Empty until registration. The last section of this file; nothing follows it. The manager appends one line per commit, in the form `EXP027_KEY: value (appended YYYY-MM-DDTHH:MMZ)`, dated with `date -u`, from the merge record, the count file and the job records only. Mechanism and read-tool checks: section 0.5.)*
EXP027_MERGE_INSTANT: 2026-10-18T02:07:36Z (appended 2026-10-18T02:14Z)
EXP027_W0: 2026-10-18T03 (appended 2026-10-18T02:17Z)
