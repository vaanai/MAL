# DEC-029: EXP-026's alpha slot (DEC-021 §8's fourth slot, M1), or the owner's M2 line (draft)

| Field | Value |
| --- | --- |
| **Status** | **Draft, 2026-10-10. For the owner's dated line (section 3).** It takes effect on merge, with quant-proof's OK on its final head and the owner's line. [EXP-026](../EXP/EXP-026-h5-boostclock-v2-prereg.md) does not merge without it. No hour was read to write it. |
| **Decider** | Vaan (owner) for the slot or for M2. The Claude manager runs the rest. |
| **Date** | 2026-10-10 |
| **Builds on** | [DEC-021](DEC-021-champion-challenger.md) §8 and Amendments 2 and 3, [DEC-023](DEC-023-h5-family.md), [DEC-025](DEC-025-c1nf-family.md) §2, [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md) §7 item 6 and §9, [EXP-026](../EXP/EXP-026-h5-boostclock-v2-prereg.md) §0, §5, §10 and §11, quant-proof's CHANGES review of PR #542 at 62cc971. |
| **Amends** | Under M1 only: DEC-021 §8, by its Amendment 4 (in this PR), for EXP-026 only. |
| **Does not amend** | The promotion gate and its thresholds. EXP-022's, EXP-024's and EXP-025's alpha, looks, thresholds, windows and seals. DEC-023's split of EXP-024's slot (0.020 / 0.005). DEC-028 (the live choice). |

## 0. Why this exists

- **DEC-021 §8 needs a new DEC for another slot.** §8: "This DEC covers at most **2** walks… A third walk needs a new DEC." Amendment 2 gave slot 2 to EXP-024. Amendment 3 (through DEC-025) gave slot 3 to EXP-025, bounded the promotion-eligible October families by 0.075, and says "A fourth slot needs a new DEC."
- **EXP-026 is a promotion-eligible read.** Its PASS clears the promotion gate as CLAUDE.md writes it, and the gate's CI90 lower bound is a one-sided 5% bootstrap test.
- **"Outside the slot accounting" is not allowed.** The first EXP-026 draft's M2 proposed an amendment recording EXP-026 outside the slot accounting. Quant-proof (10-10) found that not allowed under §8, and recommended M1 under a new DEC. This is that DEC.
- **The H5 family (the DEC-023 note).** H5-BOOSTCLOCK v2 is a second rule in the H5 family. It keeps v1's trigger, entry and fills, and its exit follows BOOST's slices (EXP-026 §1, §2). EXP-026 and EXP-024 Look 2 score largely the same pools with the same trigger. The paired gap is about 0.3–3 pp against a per-trade SD of about 99 pp, so EXP-026 is, to first order, a further look at the H5 trigger, and its alpha belongs to the H5 family. EXP-024's slot is fully used (0.020 + 0.005) and is not changed, so the H5 family needs a slot of its own for EXP-026.

## 1. M1 (the default): slot 4, alpha 0.025, EXP-026 only

- **Slot 4, alpha 0.025.** Its only member is EXP-026, with one read (EXP-026 §3.2). No member is ever added to it.
- **The binding item.** EXP-026 §5 item 7: the EXP-024 §7 item 6 day-level t, computed as there, with p ≤ 0.025 under both fail legs (the larger p decides). Items 1–6 of EXP-026 §5 stay as written.
- **The bound.** Four slots of 0.025, Bonferroni across them: the promotion-eligible October families are bounded by **0.10**, not 0.075. The H5 family's share is 0.050 (EXP-024's slot 2 and this slot 4). A fifth slot needs a new DEC.
- **What it costs** [quant-proof resampling, EXP-026 §11]: under M1, P(pass) ≤ 0.002 at a true mean ≤ 0 and ≤ 0.02 for a true mean up to +8%. A FAIL is expected.

## 2. M2: only by the owner's line, and only if the owner refuses slot 4

- **What it is.** No slot is opened. EXP-026 is read on the promotion gate (EXP-026 §5 items 1–6), plus binding item 7 = the date-cluster CI90 lower bound > 0 (`common.gate`, 1,000 date resamples, seed 1, both fail legs).
- **The owner's line must state** that the October promotion-eligible error then exceeds DEC-021 §8's bound (0.075 after Amendment 3).
- **The label.** The EXP-026 report labels any M2 PASS "gate-only, outside DEC-021 §8".
- **What it costs** [quant-proof resampling, EXP-026 §11]: P(pass) about 0.005–0.03. At a realistic date effect (σ_d 15 pp; September's date-mean SD is 20.6 pp), a pass at a true mean of 0 (0.005–0.017) is about as likely as a pass at +4% (0.025–0.063). An M2 PASS has a material chance of being a false pass.
- Under M2, DEC-021 Amendment 4 is removed from the PR before merge, and no slot-4 text enters DEC-021.

## 3. The owner's line

The line below holds the owner's choice. Nothing else fills it, and until a line is there, EXP-026 does not merge.

```
OWNER_ALPHA_ROUTE_CONFIRMED:
```

The form to fill: `<date> (owner, in session, asked by <manager>). Route: M1 | M2. Question: "<verbatim>". Answer: "<verbatim>". MiScusi notebook <id>.`

The question shown to the owner must state, verbatim: "M1 opens a fourth alpha slot of 0.025 for EXP-026, which raises the bound on a false promotion across October's promotion-eligible families from 0.075 to 0.10, and adds a binding day-level t that lowers EXP-026's pass odds to at most about 0.01. M2 opens no slot, reads EXP-026 on the promotion gate only, labels any pass 'gate-only, outside DEC-021 §8', and accepts that the October error then exceeds DEC-021 §8's bound." A line whose question lacks that text does not count.

**Then, before EXP-026 merges:** its pinned line `EXP026_ALPHA_ROUTE` (EXP-026 §0) is set to the chosen route. Under M1, DEC-021 Amendment 4 merges in the same PR.

## 4. What this DEC is not

- **Not a change to EXP-024.** Its k = 1, alpha 0.020 / 0.005, looks, windows and m are unchanged. EXP-026 is not a look of EXP-024.
- **Not a live authorization.** A PASS leads only to the DEC-018 path and the owner's yes (DEC-028 Option A). DEC-021 §2, §3, §6 and §7 do not apply to EXP-026 (it is not a challenger to a champion). As in DEC-021 Amendments 1 to 3, no backward-block PASS is required.
- **Not a register.** Unlike DEC-025 §2, slot 4 has one member and never takes another.

## Sources

- DEC-021 §8 (line 73), Amendments 2 and 3; DEC-023 §2; DEC-025 §2; EXP-024 §7 and §9; EXP-026 §5, §10 and §11 (with quant-proof's resampling figures).
- Quant-proof's CHANGES review of PR #542 at 62cc971, 2026-10-10.
- No sealed data, forward-1002, forward-1002ev, walk 2, forward-paper P&L, or canary or shadow outcome record was opened to write this file.
