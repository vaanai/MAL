QP: CHANGES at 7470b9ea542092a3b3a82da4ae66f14e4e8a8607

# QP RULING 2026-10-21 (repin): PR #780, program-upgrade review of the 10-19 pump/PumpSwap redeploys, and H5 a2 registration
Written 2026-10-21T18:38:51Z (`date -u`, mal-research-0), PR head 7470b9ea542092a3b3a82da4ae66f14e4e8a8607.


Desk only. I worked in my own worktree /tmp/claude-1000/qp780 at 7470b9e. I opened the review doc, `program-upgrade-1021/out/*.json`, s06_parity.py, QP-RULING-1021-a3.md and the #778 diff (grep only). I ran one offline split (`qp_split.py`, scratch, not committed) over the cached raw blocks in /data2/mal/scratch/a3-repin-1021/{raw,raw_pre}. That split covers sample A and P only. It made no RPC call and computed no price, outcome or P&L. I opened no ledger, bots.json, shadow, fill or P&L file. No book has cleared anything.

## Numbers I add (my split, sample A post / P pre, blocks only; the PR's totals include B)
- Txs carrying WSOL-only PumpSwap trades: truncated logs on **209 / 5,771 (3.62%)** post vs **11 / 2,723 (0.40%)** pre.
- WSOL-pool PumpSwap SellEvents seen only as CPI (invisible to `records_from_logs`): **384 / 3,157 post vs 0 / 1,040 pre**. All 384 sit in **1 pool** (`34tiphuqXUgkH9c2KCrFqCiyuhz58HEbhQBk61ub46bU`) and **1 slot bucket of about 1 h** (192 txs). Without that pool, WSOL truncation is 17 / 5,579 (0.30%) vs 0.40% pre.
- WSOL-pool BuyEvents seen only as CPI: 3 / 3,313 post vs 1 / 1,699 pre. Bonding TradeEvents seen only as CPI: 182 in 41 txs post vs 90 in 22 pre.
- Non-zero trailing u64, post (A only, 124 of the PR's 131): **0 on txs whose PumpSwap trades are WSOL-only**. 62 are on non-WSOL-only txs, 2 on mixed txs (ambiguous) and 1 on a tx with no amm trade. **All 59 non-zero pump TradeEvents carry a non-SOL `quote_mint` in the event** (Pren1FvFX6J3.. 22, PreweJYECqtQ.. 22, PrekqLJvJ3qV.. 14, Pre8AREmFPto.. 1).
- Two errors in the PR text:
  - (i) "2.6% vs 1.0% of sampled txs truncated" is wrong. Those figures are N1 events per sampled tx (604/23,471 = 2.57%; 96/9,827 = 0.98%). The truncated-tx share is 379/23,471 = **1.61%** vs 56/9,827 = **0.57%**.
  - (ii) "None is new on 10-19 in kind" is wrong. CPI-only WSOL PumpSwap SellEvents went from 0 to 384.

## Q1. Does the layout change fail the review? VERDICT: YES, it FAILS C3 as written. It can pass under C3' below, and only if every C3' item is met.
My C3 says: "Any layout change found means the review fails." A layout change was found: 8 trailing bytes on TradeEvent, BuyEvent and SellEvent (and WithdrawEvent) since slot 458888140. I am not reading the text down to "docs only", and I am not reading it down to "the decoder still works". The Q3 rule I proposed fails it too: R3(d) requires layout lengths equal to a pinned set. So the review as submitted does not meet C3. Prefix parity is strong: 0 inequalities on 14,677 post and 0 on 6,263 pre, across the change. But the rule asked for more than parity.

C3' replaces C3 for this review and for later ones. I am writing it now, before any a2 clock, with no a1 or a2 outcome in view. Only structure counts have been seen. A layout change is acceptable only if ALL of the following hold:
- a. Prefix parity of the frozen path against the independent IDL decode: 0 inequalities, pre and post. **Met.**
- b. The change is only a trailing block whose byte count is pinned per event type: {TradeEvent, BuyEvent, SellEvent, WithdrawEvent: 8}, and 0 for every other event. #778's `trailing` pins must hold exactly this set, and anything else must be a HARD fail. I could not tell from a grep whether #778 pins [8] or [0, 8]. Use [8] only, or [0, 8] with a test.
- c. The added field is 0 on every H5-universe event in the sample: SOL-quoted bonding trades and WSOL-quoted PumpSwap trades. My split says this is **met (0 non-zero)**. It must come from a committed script with output in `out/`, not from my scratch. In #778's daily check, a non-zero value on a WSOL- or SOL-quoted trade event must be a HARD halt for H5 books, because its meaning would then be unknown inside the universe.
- d. Token-2022 transfer fees. Quote side: WSOL (So111..112) is a mint of the legacy SPL Token program and cannot carry a Token-2022 TransferFeeConfig, so the new `transfer_fee.rs` / `QuoteMintNotWritable` path cannot apply to H5's quote. The PR must state this with the account owner it fetched. Base side is NOT checked. The PR must fetch the base mints of the post-sample CreatePool / migration pools (73 / 28) and report, for each, the owner program and whether it has a TransferFeeConfig extension. Required: 0 with a non-zero fee among the WSOL-quoted pools. If any has one, the arena paper engine's fill economics are wrong for that coin, and the review fails.
- e. The meaning of the field is not required. The non-zero values occur only on non-SOL quotes, they start after the 17:47Z write, and they fit a quote-side transfer-fee amount. That reading is a hypothesis. Label it as one.

## Q2. The truncated-log rise. VERDICT: no systemic bias shown in H5's universe. The bias is episodic and must be disclosed and audited.
- The headline rise is real but mislabelled (see above). Inside H5's universe, the rise in the sample is **one pool in one hour**. Without that pool, WSOL truncation is 0.30% post vs 0.40% pre. That is one sample of about 1.5% of slots, so it does not show what happens in other hours.
- Trigger misses are shared with live trading: a live trader on the same logsSubscribe feed has the same blind spot. So a missed trigger is not a paper-vs-live bias. The real risk is pricing. In a pool-hour with hidden sells, the paper engine's mark and exit come from the last visible event. That is optimistic, because hidden sells move price down.
- a2 must disclose, in the registration record: the corrected rates above, the one-pool episode, and that `records_from_logs` cannot see CPI-only events.
- Pre-declared now, outcome-blind, for the a2 read: a counts-only audit that lists the counted attempts whose pool had any successful tx with `Log truncated` between entry and exit. Use getSignaturesForAddress(pool) and getTransaction meta. If more than 5% of counted attempts are affected (the same level as the exclusion cap), the read is NOT_DECIDABLE. Otherwise the count is disclosed. The audit runs before any outcome is opened.

## Q3. a2 registration. VERDICT: NO-GO now; GO once these items are done, in this order
1. **#780 revised and merged:** fix errors (i) and (ii); add the committed WSOL/SOL split (C3'c) and the base-mint Token-2022 check (C3'd); write C3' into the review doc as the standard applied; remove the "LOUD"/verdict wording that implies a pass. Then QP OK on the new exact head, a reviewer verdict on the PR, and a merge. The re-pin itself (pins diff limited to the two program entries) is fine as it stands.
2. **#778 merged:** trailing pins as in C3'b; a HARD halt on a non-zero added field on WSOL/SOL-quoted events (C3'c); the multi-write enumeration of Q4. QP OK on its exact head and a reviewer verdict.
3. **DEC-031 amendment (Q3 R1-R7) merged** with R3(d) pointing at #778's pinned trailing set and R4's FAIL instant defined as the EARLIEST unreviewed programdata write (Q4). QP OK and reviewer verdict on its final head. Without it, a2 runs under the current A3 rule (QP-1021-a3 C9: likely to halt again).
4. QP-1021-a3 conditions still open:
   - C5: an official daily A3 run on the merged pins with all six flags clear and none unevaluated, and the clock after it.
   - C6: the daily schedule restored. #1300 was a catch-up run, not the schedule.
   - C7: the Am.9 note for v2-a2.
   - C8: register only after the Q1 `halt`. Q1's instant is unchanged (flag instant 2026-10-20T07:11:27Z).
   - The kill-review-week check.
5. The a1 end record (#781) changes: the disclosed post-redeploy span starts at **2026-10-19T15:46:16Z (1792424776000)**, not 17:47:02Z. That is 15 h 25 min 11 s to the flag. Report the attempts decided inside it as counts only.

synthetic_share_high: 7/20 = 0.350 is not above 0.35, so it is not a halt as pinned. I will not round it into one, and I will not round it away. It rose from 2/19 (0.105) to 7/20, with n of about 20. Do not re-run A3 to draw a different sample. The C5 run decides: if it is above 0.35 there, the clock does not start.

## Q4. Pin design. VERDICT: YES, change it. Enumerate the writes, not a per-day hash.
A per-day programdata hash would still merge two writes on the same day (15:46Z and 17:47Z). The monitor and #778 must do three things:
- Page getSignaturesForAddress(programdata) back to the pinned slot. If the pinned slot is not reached, fail closed. PumpSwap paging failed in this review, which is exactly that case.
- Treat every successful upgrade write as its own redeploy, each with its own instant and its own R3 check.
- Pin the programdata sha256 together with the per-event trailing-byte set.

Under R4, a FAIL ends the window at the earliest write that has not been reviewed. Every write gets its own review: for 10-19 that means both writes, and the 15:46Z PumpSwap write is still not observed. Disclose that.

## Not checked
- What the field means. The PumpSwap 15:46Z write.
- Whether logsSubscribe truncates the same way as getBlock.
- #778 beyond a grep.
- Base-mint extensions (C3'd, owed by the PR).
- Hours outside the sample.
