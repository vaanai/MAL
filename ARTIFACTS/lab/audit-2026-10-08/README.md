# 2026-10-08 profitability audit (owner-requested)

**What this is.** The owner asked for a full audit of why MAL is not profitable and what would get it there. Two files are copied verbatim from `mal-research-0`:
- `SYNTHESIS.md`, the final merge of 25 audit reports and their skeptic verdicts;
- `capv_JUDGE.md`, the judge's verdict on the CAP-PICK verification.

The 25 reports, scripts and outputs stay at `/data/mal/audit-1008/` (reports in `reports/`, scripts in `work/`, Parquet exploration tape in `tape/`). The owner's report is the claude.ai artifact "MAL Profit Audit" (https://claude.ai/artifact/Ngo8HbFVTAtWiHn5M2VrZk).

**Scope.** Exploration pools only: explore-0814, fresh-0903, exp011-0909, fast-pool-0918 and oracle-insample-0922. The audit opened no sealed block (fresh-0802, fresh-0808, fresh-0828), no EXP-009 hour, and no forward-walk or forward-paper output. Nothing here is gate evidence. Every exploration date has been mined by 12 families.

**Read in this order:** the judge, then the synthesis.
- **`capv_JUDGE.md` supersedes `SYNTHESIS.md` on CAP-PICK.**
  - Effect: the judge's inferred estimate: Aug–Sep ≈ +1.5% per attempt, live leg (≈ +1.3 flat / ≈ +1.1 pressure at 0.1 SOL); October ≈ +0.3 to +0.5% (flat ≈ +0.3), P(≤0) about 40%, below running cost at 0.1–0.25 SOL. Not +3.819%.
  - Odds of a walk-2 read: P(pass) about 10% (5–20%) with DEC-021's trade-level p; about 5–7% with the day-level p proposed in O2 [inferred, judge §2.5]. Not 25–30% (SYNTHESIS lines 225 and 543).
  - The 10 must-fix spec items are in its §4.
- **The PumpSwap reserve convention is already corrected in both files.** Tape PumpSwap rows carry PRE-trade reserves; bonding-curve rows carry POST-trade reserves. The first audit brief had it wrong. The cross-check (`reports/xcheck_reserves.md`) corrected the affected figures before the synthesis was written.

**Headline (copied from the files, not rounded):**
- **The thesis is refuted at MAL's ~1.3–1.6 s event-to-landing.** "Enter fast, sell to slower app retail":
  - bonding curve: 0 of 3,168 tradeable cells positive;
  - unselected migrations: 0 of 1,008 robust at probe fees, none at k≥3;
  - KOL copier at +5 slots: 36 of 36 cells negative;
  - FOMO retail arrives late: 0.79% / 0.81% of buy SOL by 10 s; median first buy 127 / 160 s;
  - identified app wallets entering at k3–5: −8.09% / −30.33% (Aug / Sep, pool-level, before fees).
- **The 10-01 EXP-012 PASS was an operating-point artifact.** On the same 451 rows: +6.973% at k1 START without V, against +0.145% flat at k6 END with V, lag 2 and haircut.
- **The probe lost on fees, not price.** 55 fixed-build trips: price +0.496% per trip, fees 4.486%, realized −3.990%. Priority was about 10× what early landers pay.
- **CAP-PICK is the only candidate with positive exploration evidence at a reachable operating point.** It is EXP-012 picks + seed×1.15 min_out + 300 s wall-clock cap + 55k per send. Judge verdict: HOLDS_WEAKER.
  - +3.819% reproduces exactly, but it is out of sample only for the model's weights. On the picks the live gate can actually trade, it is +3.492%.
  - The judge's honest estimates are the ones above.
  - Top-day concentration: per-block top-day shares are 63–78%. On P1, the latest pre-October out-of-sample block, ex-best-day is −0.29 SOL (live leg, tradable picks) and −0.034 SOL at the deciding cell; oracle is −0.985% (judge lines 59, 92, 213).

**Owner decisions pending (Console for_you `fy-20261008-1029-audit`): O1–O9.** Numbering: Console O1–O5 = SYNTHESIS O1–O5; Console O6 (wind-down) = SYNTHESIS/judge O7; SYNTHESIS O6 (close EXP-009) is not in the Console list; Console O7–O9 are new. No CAP-PICK pre-registration merges, and the EXP-021 screen does not run, before the owner answers O1–O3.
