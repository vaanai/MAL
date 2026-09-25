# EXP-002c addendum — Honest return distributions (median / p10 / p90 / SOL)

> **Status: scored addendum** — Oracle `mal-core-vnic` 2026-09-25. Paper-only. **Does not retune** rules v1/v2 or L3 constants. Parent stamps unchanged except as restated below.

| Field | Value |
| --- | --- |
| **ID** | `EXP-002c-addendum-honest-return-distributions` |
| **Parent** | [EXP-002c](EXP-002c-rules-v2-adverse-selection.md) (also reads EXP-002b, EXP-005, EXP-005b) |
| **Status** | Scored. **Do not promote.** Cross-day keep/kill at **350 bps** vs spine: **all listed filters KILL**. |
| **Paper-only** | Yes |
| **Host artifacts** | `/var/lib/mal/paper/_honest-rescore-2026-09-{20,21}_*` (gitignored) |
| **Fee stacks** | **125 bps** = EXP-006 `pump_assumed_bps_v0`. **350 bps** = planning round-trip (curve 1.25% + PumpPortal Local 0.5% per side) before impact / priority / rent. **Keep/kill uses 350 bps.** |

Parent EXP-002c published **arithmetic means** of `return_pct` (reject ≈3203%, runner ≈0.39%). Those means are outlier-dominated. This addendum publishes the **distribution** on the same sealed courier days and the same evaluate labels.

---

## 0. Method (no rule changes)

- Sealed observe `observe-2026-09-{20,21}.jsonl` + existing `marks-*.jsonl` on `mal-core-vnic`.
- Labels: EXP-002b **rules v1**, EXP-002c **rules v2**, EXP-005 **L3_PACKET_V0**, EXP-005b **L3_minus_v2**. Same constants.
- Join: last tick with `T < t_mark ≤ T+H` (EXP-002 / EXP-003), including later `ingest_hot` ticks on the same mint (migrate / duplicate creates) plus RPC `outcome_mark` rows.
- Entry **0.1 SOL**. Net SOL ≈ `0.1 × (return_pct − fee_pct_points) / 100`.
- **spine** = full bonding-create detect book (DEC-007). **random** = seed-1 same-n as v2 runners (EXP-002c allocation).
- Primary horizon **60s**. Other EXP windows (1s/5s/15s/30s) on the host tables.

---

## 1. Survivorship (do not drop silently)

| Day | bonding creates | RPC-attempted mints (marks file) | unique mints with a 60s tick | priced_n @60s (creates) |
| --- | ---: | ---: | ---: | ---: |
| 2026-09-20 | 14795 | 98 | 133 | 338 |
| 2026-09-21 | 18101 | 116 | 159 | 646 |

- **Unpriced on the full book** (≈98% of creates) is mostly **never queried by RPC**, not a proven rug. EXP-003 subsample-first: marks files have 98 / 116 unique parents.
- **Unpriced inside the RPC-attempted mint set** (no tick in `(T, T+H]`) is the honest dead/rug bucket. Host JSON field `attempted_unpriced_as_dead` fills those at **−100%**.
- Priced_n on the full book is **larger** than EXP-004’s graph `priced_60s_n` (102 / 122) because (1) this spine **includes bonk** (EXP-004 parks it; 1766 / 2614 rows) and (2) later observe ticks on the same mint (482 mints on 09-20 have >1 `ingest_hot` row, max 225) count as marks. Combined v2 runner+reject priced_n **44+294=338** and **52+594=646** — v2 runner priced **44+52=96** matches the parent 002c combined figure.
- Priced-only stats are **survivorship-biased** toward tokens that still traded. A +10000% name can move the **mean** and **mean SOL** by orders of magnitude; **median / p10 / p90** are the honest read.

---

## 2. Primary table — 60s, 0.1 SOL entry

Gross `return_pct` is the mark/entry ratio. **win** = `return_pct > 0`. **win>125bps** / **win>350bps** = return above that fee haircut. Mean SOL columns are per-trade; tot SOL @350bps is the book if every **priced** row were taken at 0.1 SOL.

### 2026-09-20 @ 60s

| filter | cohort_n | priced_n | unpriced_n | mean % | median % | p10 % | p90 % | win | win>125bps | win>350bps | mean SOL @125bps | mean SOL @350bps | tot SOL @350bps |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| spine | 14795 | 338 | 14457 | 2887.85 | ~0 | −99.28 | 16941.38 | 47.9% | 33.1% | 31.4% | 2.887 | 2.884 | 974.91 |
| random | 5815 | 143 | 5672 | 3758.81 | ~0 | −99.39 | 19741.71 | 48.3% | 35.0% | 34.3% | 3.758 | 3.755 | 537.01 |
| v1_runner | 2729 | 23 | 2706 | 5.30 | 3.36 | −8.58 | 41.91 | 60.9% | 56.5% | 47.8% | 0.00405 | 0.00180 | 0.041 |
| v2_runner | 5815 | 44 | 5771 | 1.23 | 0.24 | −29.09 | 28.07 | 50.0% | 47.7% | 43.2% | −0.00002 | −0.00227 | −0.100 |
| v2_reject | 8980 | 294 | 8686 | 3319.86 | ~0 | −99.39 | 19541.28 | 47.6% | 31.0% | 29.6% | 3.319 | 3.316 | 975.01 |
| L3 | 2325 | 22 | 2303 | 1949.17 | 0.93 | −58.13 | 152.29 | 50.0% | 50.0% | 45.5% | 1.948 | 1.946 | 42.80 |
| L3_minus_v2 | 1175 | 14 | 1161 | 3067.91 | 6.91 | −78.04 | 15089.05 | 64.3% | 64.3% | 64.3% | 3.067 | 3.064 | 42.90 |

### 2026-09-21 @ 60s

| filter | cohort_n | priced_n | unpriced_n | mean % | median % | p10 % | p90 % | win | win>125bps | win>350bps | mean SOL @125bps | mean SOL @350bps | tot SOL @350bps |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| spine | 18101 | 646 | 17455 | 2891.64 | ~0 | −99.37 | 17655.64 | 49.5% | 31.4% | 30.3% | 2.890 | 2.888 | 1865.7 |
| random | 6297 | 211 | 6086 | 3160.36 | ~0 | −99.30 | 18910.08 | 51.7% | 34.1% | 32.7% | 3.159 | 3.157 | 666.10 |
| v1_runner | 3409 | 27 | 3382 | 1.03 | 1.16 | −20.14 | 28.16 | 66.7% | 48.1% | 44.4% | −0.00022 | −0.00247 | −0.067 |
| v2_runner | 6297 | 52 | 6245 | −0.33 | ~0 | −21.64 | 22.77 | 50.0% | 36.5% | 34.6% | −0.00158 | −0.00383 | −0.199 |
| v2_reject | 11804 | 594 | 11210 | 3144.80 | ~0 | −99.40 | 18966.25 | 49.5% | 31.0% | 30.0% | 3.144 | 3.141 | 1865.9 |
| L3 | 2578 | 18 | 2560 | 1189.40 | 1.91 | −40.11 | 40.79 | 55.6% | 50.0% | 50.0% | 1.188 | 1.186 | 21.35 |
| L3_minus_v2 | 1380 | 11 | 1369 | 1948.57 | 6.94 | −51.25 | 47.17 | 72.7% | 63.6% | 63.6% | 1.947 | 1.945 | 21.40 |

1s / 5s / 15s / 30s tables: host `_honest-rescore-2026-09-{20,21}_table.md`. Short horizons are even thinner; v1/v2 medians sit near 0 until 30–60s.

---

## 3. Verdict (350 bps vs spine priced @60s; both days required)

Compare **median** and **mean SOL after 350 bps** to the **spine priced** frame (the actual marked population). Same-n random is shown as a row; it is itself outlier-dominated. **Do not retune.**

| filter | 2026-09-20 | 2026-09-21 | Cross-day |
| --- | --- | --- | --- |
| v1_runner | **KILL** (median 3.36% beats ~0; mean SOL 0.0018 ≪ spine 2.88) | **KILL** (median 1.16% beats ~0; SOL −0.0025 ≪ 2.89) | **KILL** |
| v2_runner | **KILL** (median 0.24%; SOL −0.0023) | **KILL** (median ~0; SOL −0.0038) | **KILL** |
| v2_reject | **KILL** (median ~0, not above spine) | **KILL** (median ~0) | **KILL** |
| L3 | **KILL** (median 0.93% beats; SOL 1.95 < 2.88) | **KILL** (median 1.91%; SOL 1.19 < 2.89) | **KILL** |
| L3_minus_v2 | KEEP on priced-only letter (median 6.91%; SOL 3.06 > 2.88) — **one name**, p90 15089% | **KILL** (median 6.94% beats; SOL 1.95 < 2.89) | **KILL** |

**RPC-attempted, unpriced-as-dead (−100%) @60s, 350 bps** — the trade you would have placed on the marked subsample:

| filter | 09-20 dead-set n / median / mean SOL@350 | 09-21 dead-set n / median / mean SOL@350 |
| --- | --- | --- |
| L3 | 20 / ~0 / **−0.0081** | 17 / ~0 / **−0.0082** |
| L3_minus_v2 | 12 / 6.81% / **−0.0054** | 10 / 6.86% / **−0.0091** |
| v2_runner | 44 / 0.24% / **−0.0023** | 52 / ~0 / **−0.0038** |

On the attempted subsample, **L3 and L3_minus_v2 lose SOL after 350 bps** once missing marks are counted as bags to zero. The day-20 priced-only KEEP is survivorship plus one moon.

**Read:** every scored filter either selects the **low-return body** (v1/v2 runners: median a few percent, p90 < 42%, mean SOL ≈ 0 after portal+curve) or **fails to beat spine median**. The huge spine/reject **means** are the right tail of tokens the filters **do not pick**. That is the same adverse-selection story as EXP-002c, with honest magnitude: you do not harvest 3200% by taking the reject book; p10 is **≈ −99%**.

Parent EXP-005 / EXP-005b **DIRECTIONAL_WATCH** is **killed** on this meter (median+SOL@350bps vs spine, both days, plus dead-as-loss). EXP-002b **FAIL** and EXP-002c lift **FAIL** stand. No evaluate retune.

---

## 4. EXP-003 — 2026-09-24 seeded backfill (public RPC)

New sealed day `observe-2026-09-24.jsonl` (**32993** bonding creates). Producer: existing `tools.exp003_rpc_backfill` (seed **1**, sample **300**, window 60s) + `tools.exp003_marks`. Outputs under `/var/lib/mal/paper/` (not sealed JSONL).

| Probe | Result |
| --- | --- |
| 0.40 s throttle | **Sustained 429.** ~6 min on create 1; **154** HTTP 429s; **0 creates/min**. |
| 2.5 s throttle, skip already-marked parents, stop after a rate sample | **0 429s.** Walked **14 / 300** in **913 s** ≈ **0.92 creates/min**. Marks: **361** ticks, **5** unique parents. Hot tokens dominate `getTransaction` time. |

`tools.exp003_marks` coverage (RPC `outcome_mark` only): 60s **ok=5** / na=32988 → **INCOMPLETE** (`<20` READY floor). Host `_exp003-oracle-2026-09-24_coverage.json`.

Full-book ingest-join (same law as §2) still prices **352** creates @60s from later `ingest_hot` ticks — **not** RPC power. Filter priced_n on the RPC subsample is **1–2** (v1/v2/L3) → **INCOMPLETE**. Do not read the 09-24 v2_reject “KEEP” on ingest-join as a third courier day.

**Gate 1 meter:** public `api.mainnet-beta.solana.com` from Phoenix Always Free is **not** a densify path at 0.40 s (~**25 429/min**). At 2.5 s the 429s stop; the bottleneck is work per hot mint. Do not open a paid RPC to rescue these filters.

---

## 5. Cross-links

- Parent: [EXP-002c](EXP-002c-rules-v2-adverse-selection.md), [EXP-002b](EXP-002b-evaluate-rules-v1.md), [EXP-005](EXP-005-smart-wallet-follow-discovery-v0.md), [EXP-005b](EXP-005b-l3-residual-vs-exp002c-v2-falsifier-v0.md), [EXP-003](EXP-003-post-create-marks.md)
- Fee planning: execution-stack note (350 bps round trip). EXP-006 remains 125 bps as a **lab constant**, not the live haircut.
- Does **not** amend [LAB_STATE.md](../LAB_STATE.md) in this PR.
