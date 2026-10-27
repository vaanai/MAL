# P69: slot-era audit of slot-denominated latency charges (desk, 2026-10-27)

Desk audit. Repo docs, the library and code only. No tape opened, nothing priced, no sealed path touched, 0 Helius credits. No book has cleared the promotion gate and nothing here says one makes money.

## Verdict

**0 lines are flagged** under the task rule (charge too harsh by 1.5x or more AND the line's own k-sweep showed edge at the corrected k).
**5 library rows are marked "possibly mis-charged"** (the feed delay D = 7 slots applied on August and early-September tape). None of them has an own sweep that shows edge at the corrected D, so none can flip. One slot-denominated line (P56) is mis-charged in the lenient direction.

## 1. Slot time by period (what the repo documents)

| Period | Slot time | Source | Quality |
| --- | --- | --- | --- |
| 2026-08-14 .. 08-28 (explore-0814) | 0.368 s ("August median") | library row `hunt23-speed-1`, why_stopped, from its DECLARE unit note (the DECLARE is under /data/mal, not read) | measured, second-hand |
| 2026-08-28 .. 09-18 | not in repo docs | p25 IDEAS.md names the 350 ms step at epoch 1020 (Solana Compass link) but no date. Steps 300 and 250 ms: only the 250 ms start, 09-18, is dated (C3). | gap. Nominal 350 -> 300 ms, so about 0.31 to 0.37 s measured (inferred: measured/nominal is 1.05 in the two periods below) |
| 2026-09-18 .. 10-09 | 0.267 s | `tape-coverage-chain-verdict-2026-10-02.md` (3.7 slots/s); `fast-entry-trigger-2026-10-07.md` (268 ms); p23 DESIGN (267.26 ms at the 10-09 07:11Z run) | measured |
| 2026-10-09 ~14:35Z on | 0.2174 s (A3 monitor 217.39; first full epoch 218.3 ms); 0.21683 s (feed-delay tape 10-18) | p23 DESIGN; p25 C3; `arena-records-2026-10-18.md` | measured |

The 400 ms figure appears only as a fallback constant (launchlab-p11 DECLARE) and in a synthetic HV4 test. No repo summary shows a 0.4 s tape.

**Open measurement (not done, desk only):** a counts-only per-hour slots/s from the tape's slot and block_time fields for 08-28 to 09-18 would close the gap. It needs a precount-style job and a seal check; proposed, not run.

## 2. How each family converts seconds to slots

Real entry latency is in seconds: leg 1.3 to 1.9 s (`LATS` in `tools/arena_paper.py` = `F.LATENCIES_S`, 1.3 and 1.9), plus a feed delay of p90 1.4815 s (median 1.0445 s), measured once on 2026-10-18 (`arena-records-2026-10-18.md`, 0.21683 s per slot).

| Family | Charge | Era-correct? |
| --- | --- | --- |
| Arena kit legs (`arena_paper._k`, k = ceil(lat / sps) with the hour's sps); EXP-022 (1,300 / 550 ms per hour); EXP-024/025/028 legs; DEC-024 (ceil(3.5 s / sps)); launchlab-p11 (round(1.9 s / slot_s) per hour) | seconds, converted with the hour's own clock | **yes**, no mismatch |
| Arena kit feed delay `feed_delay_slots` (`StackConfig`, one required integer, D = 7 from the 10-18 tape) | **fixed slots**, measured in seconds on an Oct tape | **no** on any tape with sps above 0.217 s |
| EXP-012 family (EXP-015/016/017/018/020/021: k = 6, exit lag 2) | fixed slots | yes for the pools they ran on (see 3.1) |
| EXP-014 (entry offset d = 4 / 8, exit lag 2) | fixed slots | lenient, below the real latency (4 slots = 1.1 to 1.5 s on Aug/Sep tape) |
| P56 (entry = signal slot + 8) | fixed slots | lenient (see 3.4) |

## 3. Lines that cite a slot count or k (task item 1)

D = 7 slots against the measured feed delay of 1.4815 s:

| Tape sps (s) | D = 7 in seconds | x vs 1.4815 s | corrected D = ceil(1.4815 / sps) |
| ---: | ---: | ---: | ---: |
| 0.368 (Aug) | 2.576 | 1.74 | 5 |
| 0.31 (early Sep, inferred low) | 2.170 | 1.46 | 5 |
| 0.267 | 1.869 | 1.26 | 6 |
| 0.2174 | 1.522 | 1.03 | 7 |

Feed delay is a receive-time lag against block_time, so it is a seconds property. That it does not scale with the slot length is an assumption; this audit did not test it (that is p60's question).
Total modelled entry latency (D + leg) against the real one (1.4815 + leg): at 0.368 s and a 1.9 s leg, 4.476 s vs 3.382 s, 1.32x; at a 1.3 s leg, 3.876 vs 2.782, 1.39x. **Below 1.5x for every period.** The D component alone reaches 1.5x only on August tape (and early September if the slot time is 0.32 s or above).

### 3.1 EXP-012 family and the k-sweeps (P51-style, 2026-10-02 / 10-04 curves)

- **Data period:** the 9-day pool, fast-pool 09-18T23..09-22, oracle-insample 09-22..25, oracle-live 09-25..27: the 0.267 s era. The holdout block 09-03..09-09 is 0.31 to 0.37 s.
- **Charge:** k = 6, i.e. 1.60 s at 0.267 s. Real leg 1.3 to 1.9 s, so in range (0.84x to 1.23x). k = 8 is 2.14 s, k = 12 is 3.20 s.
- **Sweep result under V:** pressure CI90 lower bound above 0 through k = 8 (0.00117 at k = 8) and below 0 from k = 12 (-0.00413); `exp012-latency-virtual-2026-10-04.md`.
- **Verdict: not mis-charged.** The "edge gone by k = 12" statement is 3.2 s, about 1.7x our upper leg. It was not the cause of the EXP-012 outcome and the sweep already shows edge at 1.3 to 1.9 s. On the 09-03 holdout, k = 6 at 0.31 to 0.37 s is 1.9 to 2.2 s: at most 1.15x the upper leg. For Oct-9-and-later forward hours a fixed k = 6 is 1.30 s, **lenient** by up to 1.46x against 1.9 s. DEC-016's live-support check uses the measured k, so it is not affected.

### 3.2 The 7-slot feed delay (arena kit and screens)

| Line (library id) | Tape | D | Verdict |
| --- | --- | --- | --- |
| `hunt23-speed-1` | explore-0814, 0.368 s | sweep D in {2, 4, 7} x leg {0.5, 0.9, 1.3, 1.9} | Own sweep covers the corrected D = 4 or 5 (1.47 s, 1.84 s). Levels stay negative at the fastest point (flat -0.81%, -2.18%, -2.11%; E1 mean about -20%); no grid point has mean, ex-top-3 and ex-best-day above 0. **Charge was 1.74x harsh at D = 7 but the sweep shows no edge at the corrected k. Not flagged.** |
| `p6-latency-reprice` | Aug/Sep blocks, D = 7 held, leg swept | 7 | D not varied. The leg sweep moves means by 0.0000 to +0.0008 SOL/trade; correcting D from 7 to 5 or 6 is a smaller or equal step than the 1.9 to 1.0 s leg change (about 0.6 to 1 s). Negative lines (REVIVE r2h/r6h/r12h at -0.0028 to -0.0037) are 3 to 10 times larger than that gain. **Possibly mis-charged, cannot flip. Not flagged.** FLOOR v2 is positive at every leg already. |
| `hunt23-b-flow-B` | four blocks pooled (explore-0814 336 h, oracle-insample 79 h, fast-pool 73 h, oracle-live 65 h) | 7 | The 1.15 price guard refused 18% to 31% of attempts because the price had moved more than 15% by landing ("about 4.5 s"). At 0.368 s that is 2.58 + 1.9 = 4.48 s against a real 3.38 s. Gross medians -5.12%, -10.80%, -18.90% are far below what a 25% shorter delay would recover. No own sweep. **Possibly mis-charged, no edge shown. Not flagged.** |
| `hunt23-c-1` | explore-0814 | 7 | The 1.15 guard rejects 38% of SM1-h30 attempts. All 16 sub-tests fail with means -4.10% to -13.11%. No own sweep. **Possibly mis-charged, no edge shown. Not flagged.** |
| `hunt23-w2-death-1`, `-2` | four blocks whole (553 h), D = 7, 1.9 s | 7 | Every cell fails mean, ex-top-3, ex-best-day and CI. No own sweep. **Possibly mis-charged (August share), no edge shown. Not flagged.** |
| Oct-era arena lines (Q50, H5, DBC, HV4 deciding legs) | 0.2174 s tape | 7 | ceil(1.4815 / 0.2168) = 7. **Correct.** On 09-18 to 10-09 hours D = 7 is 1.26x harsh, below 1.5x. |

### 3.3 Other slot-count citations

- `lib-c-Q2.1` ("MAL lands a median 5-6 slots late; break-even needs the first seat on 36.6-68.9% (Aug) or 87.9-93.9% (Sep) of tries"): the 5 to 6 slots are an October measure (1.3 to 1.6 s at 0.267 s; SYNTHESIS line 20 says 1.3 to 1.6 s). If the August seat share used 5 to 6 slots, it charged 1.84 to 2.21 s at 0.368 s, 1.4x to 1.7x. The row has no sweep and the audit's tradeable-cell grid (3,168 cells, best gross +0.666% / +0.762% / +1.599% against a 2.47% fee round trip, SYNTHESIS line 127) is fee-bound, not latency-bound. **Cannot flip. Not flagged.** Source file with the seat shares not in repo; unverified.
- KOL copier "5 slots later, 36 of 36 cells negative across 3 periods" (SYNTHESIS line 165): periods differ in slot time; fee-bound as above. Not flagged.
- `drain-escape-1/3` ("sell 1 or 5 slots later"), `p20-dbc-*` (expiry + 2 slots), `lib-c-Q2.4` (mig+15 slots), `pump-buyback-1`: slot counts are exit or feature windows and not entry-latency charges. Exit lag 2 is below the live exit leak either way. No entry-latency flip possible.
- `p60-bam-preconf-race`, `p25-alpenglow-send-cost` ("land within 6 slots"): 6 slots = 1.3 s at 0.2168 s, 1.6 s at 0.267 s. Consistent with the 1.3 to 1.9 s leg; no change.
- DBC DEC/EXPs and launchlab-p11 use seconds per hour: correct.

### 3.4 P56 (lenient direction, not a flag)

Entry = signal slot + 8 on `fresh-0903` (09-03..09-09): 8 slots = 2.4 to 3.0 s at 0.30 to 0.37 s. The real total is feed 1.48 s + leg 1.3 to 1.9 s = 2.78 to 3.38 s. So P56's entry charge is at or below real, and the KILL (flat-15% mean -0.004107835180322226, 0/7 days) stands. Its "150 slots (about 60 s)" signal window assumed 0.4 s; at 0.30 to 0.37 s it was 45 to 55 s. This is a feature-window definition, not a latency charge, and does not change the verdict.

## 4. Flags and follow-ups (task items 2 and 3)

No line meets both conditions, so no re-price is requested. For the record, the **only** candidate family is the D = 7 group (3.2). A pre-registered follow-up would be worth running only if the owner wants the D-in-seconds question closed:

- **Proposal P69-R1 (not scheduled, needs manager and quant-proof OK first):** re-price the `hunt23-speed-1` four cells on explore-0814 once, with the feed delay set per hour as D_h = ceil(1.4815 s / sps_h) (4 or 5 on August hours), leg 1.9 s, 0.25 SOL, strand rule total, rent 0, both fail models, md5-equivalence replay at D = 7 first.
- **Kill lines (declare before pricing):** KILL if no cell has mean, ex-top-3 and ex-best-day all above 0 under both fail models, same as hunt23-speed-1's own rule. No promote path: it is in-sample exploration.
- **Why it probably needs no run:** the existing sweep at D = 4 already covers D_h = 4 to 5 and shows no cell near zero.
- **Cost:** `StackConfig.feed_delay_slots` is one required integer, so per-hour D needs an engine change and an md5 decision-equivalence proof; a hand re-run at D = 4 and D = 5 on a single block needs none.
- **Blocks (docs/HOLDOUT_LEDGER.md):** explore-0814 `[2026-08-14T12, 2026-08-28T12)` is exploration pool, free for screens. fresh-0828 is reserved for EXP-028 (owner cell stays reserved until a claim record); fresh-0808 is next for a later cohort; fresh-0802 is the held-back unspent reserve. **Use none of those three for this question.**

## 5. What was measured and what was not

- Measured in repo docs: slot time Sept 18 onward (0.267, 0.2174, 0.21683 s) and August (0.368 s, second-hand through a library row).
- Not measured: slot time 08-28 to 09-18 (gap), whether the feed delay scales with slot time, any P&L. The hunt23 DECLARE, the audit's seat-share source and all tape were not opened.
- Nothing here changes a gate or a verdict. Rows marked "possibly mis-charged" keep their statuses.
