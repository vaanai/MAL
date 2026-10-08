# MAL profitability audit — final synthesis (2026-10-08)

Final merge of the three audit plans (fastest path, highest-confidence evidence, contrarian), resting on 25 audit reports in `/data/mal/audit-1008/reports/` and their skeptic verdicts. Repo state: `main` 2bd45f1 (#454). Live jobs at writing (09:30Z):
- forward walk #382, running on #454;
- EXP-021 precount #375 passed (`would_refuse = []`); freeze #376 running;
- DEC-022 Phase A stream #371, about 12 h left.

**Evidence labels used throughout**
- **[V]** a finding a skeptic re-ran: "confirmed", or "partially confirmed" with the correction applied. Refuted readings are never used as support.
- **[U]** a single-investigator gap-fill measurement (the `g_*` reports, `xcheck`, `v_per_trade`). Each was pre-declared and cross-reproduced against other reports where noted, but not separately verified.
- **[S]** computed for this synthesis. These are power, kill-rate and drawdown simulations on exploration rows already read many times. They are descriptive arithmetic, not evidence of edge. Scripts and outputs: `/data/mal/audit-1008/work/synthesis_power/`.
- **[I]** inferred.

Every exploration number below comes from dates that 12 families have mined. None of it is gate evidence.

---

## 1. The answer in one paragraph

MAL is not profitable because the book it built and traded has no edge at the latency and cost MAL can actually reach: EXP-012-selected PumpSwap graduations bought about 1.3–1.6 s after the pool opens, held up to 30 minutes on tp50/sl30, at 500k lamports priority per side on a 0.05 SOL stake, with min_out set from a fresh quote. The lab's measurement choices (an optimistic gate operating point, a universe that was 60–75% junk, a fill-mixed label) hid this for two weeks. The owner's thesis ("enter fast, sell to slower retail") is refuted at MAL's latency: identified app retail (FOMO) puts only 0.79–0.81% of its buy SOL in by 10 s (median first buy at 127–160 s); the money slow buyers lose goes to pre-migration inventory holders and to launch bundles and snipers in slots 0–2; every identified cohort entering where MAL lands (k3–5) lost (−8.09% Aug, −30.33% Sep); and the probe's fixed builds made +0.496% of stake on price against 4.486% in fees. One mechanically different variant survives on exploration data. **CAP-PICK** keeps the frozen EXP-012 picks and adds an on-chain min_out at the seed price × 1.15, a 300 s wall-clock exit that leaves before pump.fun's own scheduled 17.585 SOL BOOST bid ends, and 55k lamports per send. It makes +3.819% of stake per attempt on the three blocks the model never trained on (date-CI90 lower bound +1.84, 20/28 days), but it was found after many looks, is −0.414% on the latest block with a falling daily mean, depends on a pump.fun parameter, and all of its evidence predates the 2026-10-02 program upgrade. **The strategy as framed is wrong; CAP-PICK is unknown.** It should be decided by one pre-registered forward read on post-upgrade walk-2 data, with looks at days 7, 14 and 21 under the DEC-021 bar (simulated power about 45% if the true edge is half the exploration figure, about 96% at the full figure, false pass about 2% [S]). The read is guarded by a zero-credit BOOST/structure monitor and preceded by a scorer reproduction, not by spending a sealed backward block (a 6-day read discards only about half of dead books [S]); EXP-021 and paid speed work should pause. If CAP-PICK fails, no measured book supports live trading at MAL's latency and cost, and running cost should be cut. If it passes, live starts at 0.05 SOL with a paper twin and pre-agreed stops, and scales only with bankroll, because at 0.25 SOL the daily P&L standard deviation is about 1.2 SOL [S].

---

## 2. Why MAL is not profitable — ranked root causes

The shares are judgment, not measurement. All three plans gave nearly the same split.

### RC1 (~35%): MAL buys on the wrong side of the flow at its reachable latency

The owner's thesis fails here, and it caps every variant.

| Evidence | Number | Source | Status |
|---|---|---|---|
| Where MAL lands, against the on-chain pool-create slot | k p50 5 (faa3192, n 34), p50 6 (fixed builds, n 56); 0 of 62 at k≤2; p50 29 successful pool txs ahead; in-slot index p50 0.448 | g_early G1 | [U] measured on chain for all 62 buys |
| Who owns the first slots | Instant launches are 47.8% (Aug) to 65.7–76.4% (Sep) of STD graduations, and the launcher's bundle owns them. Organic first buyers react inside the slot. Priority orders buyers only within a slot (Spearman −0.14 to −0.49) | g_early G2 | [U] |
| When identified retail arrives | FOMO: 0.79% / 0.81% of buy SOL by 10 s, 3.91% / 3.33% by 30 s, peak bin 300–600 s; median first buy 127 / 160 s | g_app G-F1 | [U], pre-declared, one-shot validation read |
| Who wins, identified cohorts | k0 +24.88% / +12.08%; k1–2 +24.44% / −4.93%; **k3–5 −8.09% / −30.33%** (Aug / Sep, pool-level, before fees) | g_app G-F2 | [U] |
| MAL-size real wallets (0.05–2 SOL) in normal pools | first buy at dk 0–5: **+0.49% / +0.94% / +1.77%** before the ~1.2% buy fee and priority, so negative net. Late wallets (dk 151–750) −15.47% to −23.26%. Outsiders as a group lose on 27/27 days, median −128 to −145 SOL per graduation over 24 h | g_whales g-F3, §3 | [U] |
| No net retail inflow to sell into | After 2 slots, sell/buy SOL is 0.93–1.05 in every window; k2 → +30 s gross is +0.92% | d14-F3 | [V] partial: relative claim holds, "no inflow" mechanism wording corrected |
| Unselected migration book | 0 of 1,008 cells are robust in all 4 blocks at probe fees; only 2 at lean fees, both START k=1; none at k≥3 | d05-F1 | [V] partial: reproduced exactly |
| No tested unselected universe has a positive point estimate | — | d14-F2 | [V] partial: magnitude biased negative; k2 not reachable |
| Bonding curve | 0 of 3,168 tradeable cells (≥1 slot after the event) are positive on discovery, validation or the 266 ms block | d04-F1 | [V] partial |

### RC2 (~20%): the frozen exit rode through the end of pump.fun's BOOST bid

This is fixable, and it is the main reason a candidate still exists.

| Evidence | Number | Source | Status |
|---|---|---|---|
| BOOST is a scheduled TWAP buyer | Exactly 17.585 SOL in ~29 slices over ~345 s after every non-mayhem graduation; detected on 94–99% of V>0 graduations | d12-F1 | [V] partial: facts reproduce, profit framing overstated |
| BOOST ends ~343–348 s after our entry | p10 329.5–333.0 s, in every slot-time era | g_reachable §4 | [U] |
| Holding past BOOST's end is the loss | Deadline exits held to 420 s instead of 300 s: −9.337% of stake each (n 1,349). After the last slice, the mean price is −6.96% to −9.31% within 30 s | g_reachable §4; d12 §3.4 | [U]; [V] partial |
| A 300 s cap beats the 30-min hold in all 5 blocks | +3.139 / +2.920 / +1.123 / +1.259 / +0.895 pp per fill, date-CI lower bound > 0 in each | g_reachable §4 | [U]; consistent with d07-F1 |
| The only robust exit among 89 alternatives | 300 s cap: +2.79 / +2.31 / +1.04 pts by block group, day-cluster CI lower bound > 0 | d07-F1 | [V] partial: gain real in the tape sim |
| It is why EXP-012 "had no ranking" | Pick − non-pick among guarded fills: +3.718 pp [+1.52, +5.93] with the cap, +2.41 [−0.16, +4.81] under the 30-min hold. Pick-only book +3.379% per attempt (cap) vs +0.784% (30-min) | g_reachable §5 | [U] |
| Live | 4 of 61 trips held over 300 s: 54.5% of the net loss, 14.1% of the gross; 2 of the 4 were gap-through stops | d07-F5 | [V] partial (corrected from "3 of 4") |

### RC3 (~15%): no seed-anchored price guard, on a universe dominated by manufactured graduations

| Evidence | Number | Source | Status |
|---|---|---|---|
| Migration-slot whale buys are real and run by one operator per mint | 50/50 RPC checks are real WSOL. WHALE50 + V0 pools are 56.8–70.4% of graduations. The 79% holder sells 0.0–0.4% into a 22–132× pump | g_whales g-F1, g-F2 | [U]; refutes d14-F10 "implausible" |
| Junk rows dominate every lab table | 59.5–70.2% of the EXP-015 universe; 74.89% of the 10-01 read block; EXP-012 never selects them | d16-F1 | [V] partial |
| The seed×1.15 guard enforced on chain | Pooled −0.388% → +0.659% per attempt (+1.047 pp). A reject costs one 55k send (0.011% of 0.5 SOL). Not lookahead | g_reachable G-F4 | [U] |
| The live executor anchors min_out to its own fresh quote and only logs drift | 5 of 34 faa3192 buys were sent more than 15% above the seed. Code: `tools/probe_executor.py:988-1007`, `:1210` | d07-F3, d11-F6 | [V] partial (d07-F3); d11-F6 inferred |
| Trap pools | 18–51% of canonical migrations are above 10× the seed by s0+1 | d07-F3 | [V] partial |

### RC4 (~10–15%): measurement choices created false confidence and cost weeks

| Evidence | Number | Source | Status |
|---|---|---|---|
| The 10-01 PASS was an operating-point artefact | +6.973% at k1 START, no V → +0.145% flat at k6 END, V, lag 2, haircut on the same 451 rows. A 6.83 pp bias, about 3.5 block SEs | d09-F3 | **[V] confirmed** |
| The gate config cannot tell a selector from none | At k1 START with exit at the trigger slot, all 5,547 attempts (buy-all) also pass | d02-F2 | [V] partial |
| The selector is a fill classifier under the 30-min exit | Win-given-fill AUC 0.514 [0.500, 0.529]. 69% of split gain sits on two curve-type constants. 36 new features: AUC 0.49–0.56 | d08-F1, d08-F3 | [V] partial |
| Training pool B came from a gappy tape | 26.6% of EXP-012 training rows (not 65%); 7.49%+ of slots missing; 562 blackouts | d13-F3, d01-F2 | [V] partial |
| Reserve-convention errors in some tools | PumpSwap rows are pre-trade (99.04% of 6.1M pairs). Wrong in `probe_sim_calibration.py` and in d06/d08 scripts. Corrected live − sim: +0.84%, not +1.38% | xcheck §1, §4; g_sim_live F1 | [U] (xcheck is itself a verification) |
| Slot time drift | 415 → 366 → 316 → 267 ms; `SLOT_MS = 400` hard-coded | d04-F5, d11-F3, d13-F5 | [V] partial |

### RC5 (~10% of the live loss): self-inflicted fixed costs at trial size

| Evidence | Number | Source | Status |
|---|---|---|---|
| The probe lost on fees, not price | 55 fixed-build trips: price +0.496% per trip; fees 4.486% (priority 2.000, pool buy 1.206, pool sell 1.241, base 0.020, failed 0.018); realized −3.990% | g_sim_live F3 | [U] (ledger arithmetic) |
| Priority about 10× what early landers pay | 500k per side on a 250k CU limit, ~112k CU used. Only 7.37% of k0–2 no-tip first buys paid that CU price; their median total priority was 50,778 lamports | d03-F2, g_early G8 | [V] partial |
| Priority share of the probe loss | ~29% (61M / 210M lamports) | d16-F3 | [V] partial: fixed-lamport arithmetic, not a market effect |
| What right-sizing is worth | At 0.5 SOL, 505k → 55k is only +0.128 pp. At 0.05 SOL it is ~+1.8 pp | g_reachable §3, d11-F7 | [U]; d11-F7 measured |

### RC6 (~5% of P&L; the largest calendar cost): an evidence process that could not see realistic edges

| Evidence | Number | Source | Status |
|---|---|---|---|
| EXP-021's confirmation bar B6 has no power | Pipeline power 0.000–0.005 at +1% to +5% | d09-F1 | [V] partial |
| What 2–3 weeks can certify | 80% gate power needs about +4.5% at 14 days and +3.5% at 28 days (k6 @ 0.05 template) | d09-F2 | [V] partial: if anything understated |
| Trade-level CI narrower than a date-cluster CI | about 1.6× (design effect 2.7) | d14-F1 | [V] partial |
| Over-mined dates | m = 12 → p < 0.00208 (only 9 of the 12 actually read outcomes) | d10-F2 | [V] partial |
| Process share | Governance 28% of Claude-era PRs (bucket caveat); failures with no outcome cost 8–10 days; precounts 6 h at 72 GB vs a 78 s / 2 GB columnar screen | d10-F1, F3, F6 | [V] partial |

### RC7 (forward risk): a protocol-controlled, non-stationary market changed under the lab

| Evidence | Source | Status |
|---|---|---|
| pump, PumpSwap and fee programs redeployed at 2026-10-02T15:47Z, unnoticed for 6 days. All MAL evidence predates it | g_october F1 | [U] |
| BOOST is still on (boost_enabled = 1; 7/7–8/8 sampled non-mayhem graduations, last slice median 337 s after the migrate tx), but its cadence is set off-chain and one `toggle_boost` tx can switch it off | g_october F3, invB F3 | [U], n = 10 |
| Synthetic migration is deployed but unused (0/10, 0/37). If adopted, pools open above the seed and the whale buy moves out of the decoded tape | g_october F8; g_early G6 | [U] |
| v2 fee-keeping makes V move per trade: 1.2% of trades (2.9% of WSOL volume) on 10-04..10-08; ≤1.22 bp drift in a fresh pool's first 30 min today | v_per_trade | [U] |
| 200 ms slots at epoch 1053, about 2026-10-09T14:34Z (not 10-08) | g_october F5; invB F4 | [U] |
| Block swings: the same rule ran −4.3% to +5.5%. The capped full book is negative on fast-pool-0918 in all 12 configs × 3 legs. The pick book's daily mean is falling (Spearman −0.362, p 0.033) | d05-F6; g_reachable G-F1, §5 | [V] partial; [U] |

---

## 3. The thesis, tested: delayed retail flow and fast entry, by phase

**Thesis.** MAL enters about 200 ms after an event, slow app retail arrives tens of seconds later, and MAL sells into it.

**Measured reality:**
- MAL's event-to-landing time is about 1.3–1.6 s. The owner's "~200 ms" is only the decision→send step (p50 221 ms, 55% of it file IPC) [d11-F5 inferred; U g_early G1].
- Retail does arrive late, by minutes, and it is exit liquidity.
- The value goes to whoever is inside the event's own slot (bonding curve) or in slots 0–2 and pre-migration inventory (PumpSwap).
- A ~1.3 s buyer is itself exit liquidity.

### 3.1 Bonding curve (d04) — refuted at every reachable latency

| Measurement | Value |
|---|---|
| Entry right behind a ≥1 SOL buy, same slot (L0), H5 s | +17.76% net, CI90 lower bound +17.24%, 15/15 days. Gone by the 3rd–5th same-slot follower (+23.80% → +0.15% → −2.62%) |
| Start of the next slot (L1s) | −4.60% net / −1.15% gross |
| Tradeable cells (latency ≥ 1 slot) with a positive mean | 0 of 3,168 on discovery, validation and the 266 ms block. Best gross +0.666% / +0.762% / +1.599%, against a 2.47% fee round trip |
| Thesis cell E10, L5, H30 | −14.25% (disc), −9.64% (val); 0/15 and 0/13 days positive |
| "Human" net-buy peak in [0,1) s | Not human reaction: 89–94% of it is same-slot bundle flow (verifier). No 10–60 s wave |
| Who wins on non-graduated curves | Creator +32.62%; transfer-recipients ("pure sellers") +88,907 SOL; every class entering ≥2 slots after create loses |

Status: [V] partial (F1–F3, F5).

### 3.2 Migration / PumpSwap (d05, d14, d11, d16, g_whales, g_app, g_reachable) — refuted for the thesis; one non-retail mechanism survives

**Latency decay, unselected organic canonical migrations.** Gross return with pool fees inside, n 12,675, 36 dates; d14-F3 [V partial]:

| Entry | k1 | k2 | k6 | ~10 s | ~30 s | ~60 s |
|---|---:|---:|---:|---:|---:|---:|
| Gross mean | +0.570% | +0.272% | −1.152% | −3.112% | −5.361% | −6.481% |

The relative claim holds: later is worse. The absolute claim fails: even k2 is below fixed costs.

**Who profits, by first-buy timing:**
- Slot 0–2 cohorts: +5.4% to +33.1% (SOL-weighted).
- Slots 3–5: −2.9% to −19.1%.
- 15–60 s: −1.3% to +0.1%.

Source: d05-F3 [V partial]. The verifier notes this metric is SOL-weighted and does not transfer to MAL-size trades. The MAL-size version is g_whales g-F3 and g_app G-F2 (RC1), with the same conclusion.

**Speed is not a robust lever.**
- EXP-020's paired k2 − k6 is +1.023% on all 27 dates. By block it is +1.921 (Aug, ex-08-21), −0.466 (Sep P3) and −0.935 (Sep P4); the September CIs span 0. Source: d11-F2 [V partial].
- For the capped book, entering at 0.8 s instead of 1.3 s is worth +0.244 pp pooled, all of it from August (+0.620); September blocks are −0.10 to −0.24 [U g_reachable].
- Being 6 slots earlier is worth about 1 pp at 0.5 SOL and is negative at 0.05 SOL [U g_early G4].

**What survives is not retail.**
- The capped book's profit is pump.fun's own BOOST TWAP bid, which the book holds into and leaves before it stops [U g_reachable §4].
- That is mechanical, not behavioural. It is also fragile (RC7).

### 3.3 Wallet influence / KOL / app flow (d06, d12, g_app) — refuted at reachable latency

| Measurement | Value | Status |
|---|---|---|
| Price after a public-KOL entry | Median +5.15% over ~15 s from the KOL's post-trade price. About 1/3 gone by slot+2, ~57% by slot+5. Copy bots land 58.6% in the same slot | [V] partial (d06-F1, F2) |
| KOL copier entering 5 slots later | 36 of 36 cells negative across 3 periods, every CI90 lower bound < 0 | [V] partial (d12-F4, "more robust than claimed") |
| d06's only positive cells (KOL, k=1 first-in-slot) | **Artefact** of the reserve convention: corrected −0.001166 / −0.005240 per 1 SOL. The pre-registered KOL_sel one-shot failed: n 817, mean −0.001311, 0/4 days | [U] xcheck §3.7; [V] partial d06-F3 |
| Early app flow (5–10 s) → +60 s / +300 s returns | Validation ρ 0.041 / 0.114; AUC CI includes 0.5. Fails the pre-declared bar | [U] g_app G-F3 |
| The Axiom-sniper veto | ρ −0.33 in Aug → −0.006 in Sep (regime shift) | [U] g_app G-F4 |

### 3.4 Thesis verdict

The premise is real: retail is slow and loses. The mechanism MAL would need is not:
- MAL cannot be early enough to be the counterparty that collects;
- and it cannot be late enough to sell to retail before the inventory holders do.

Refuted on all three phases.

---

## 4. The full path audit, stage by stage

The bridge from the traded book to CAP-PICK uses g_reachable's paired cells: pooled per attempt, live leg, full guarded book unless marked [U].

| Stage | What MAL does today | Where opportunity is lost (measured) | Size | Fix in plan |
|---|---|---|---|---|
| **Discovery** | The confirmed getBlock tip follower polls getSlot every 0.4 s. The runner decides on the tip's first-print receive | Sees the pool about 3 slots after create (create→read p50 3, read→land p50 2) [U g_early G1]. The processed stream leads by p50 1,012 ms but misses 7.7%, consistent with a v1-tx blind spot [U g_early G3]. The pre-create listener has been dead since 10-02 (no health alarm) [d11 §7]. getSlot polling is ~39% of tip credits [d01-F7, d03-F8, inferred] | Speed 1.3 s → 0.8 s: +0.244 pp pooled, negative in Sep. Not the binding loss | Keep the tip trigger (features match training). Stop paid speed. v1 fix, and slotSubscribe after 10-16 (credits) |
| **Data** | getBlock tape (sound: 99.72–99.76% slot coverage, 0 duplicates, tx order exact on 99.97%) [d13-F8] | Pool B gaps feed 26.6% of training [V d13-F3]. Silent NUL holes: 3 in exp011-0909; sealed blocks unscanned [V d13-F4]. Reserve convention wrong in some tools [U xcheck]. No fee payer, quote_mint or signature in the parquet [V d13-F1]. Per-trade V since 09-30 [U v_per_trade] | False signals: d08 s17 +0.007441 → −0.002016; d06 KOL artefacts; calibration +1.38% → +0.84% | Walker resume guard and readers that refuse on bad lines before walk 2. Event-V decoder for walk 2 |
| **Filtering / universe** | A "migration" is any first PumpSwap print | 59.5–75% junk rows [V d16-F1]; WHALE50 + V0 = 56.8–70.4% [U g_whales]; 42–60% PUMPED in the migration tx [V d05-F2] | The "98.9% vs 28% fill lift" was mostly a junk filter. Baselines diluted | Define the universe outcome-blind: canonical V≈17.58, non-mayhem, seed-anchored guard |
| **Decision (selection)** | Frozen EXP-012 at threshold 0.8030766588 | Under the 30-min exit: no ranking among fills, AUC 0.514 [V d08-F1]. Under the cap: pick − non-pick +3.718 pp, but it reverses in exp011 (−2.8) and oracle (−1.8) [U g_reachable §5]. Regime gates useless: 2 of 66 OOS cells [V d14-F4] | Selection is worth ~+2.6 pp per attempt on the pick book only with the cap (+0.784% → +3.379%) | Keep the frozen picks. Report a beat-the-baseline line. Stop selector retrains on the 27 dates |
| **Entry** | Lands k p50 5–6, mid-slot. 500k priority. min_out = fresh quote − 15% | Pays for pumped pools: no guard is −1.047 pp. Priority: −0.128 pp at 0.5 SOL, about −1.8 pp at 0.05 SOL. Position in slot unmeasured (START vs END is +0.547 pp). 4-slot blind window before positions are watched [d11-F9] | Guard +1.047 pp; priority +0.128 pp (0.5 SOL) | Seed×1.15 min_out on chain; 55k per send; CU limit ~130k; landing canaries |
| **Exit** | tp50/sl30, 30-min max hold, 400 ms polling | Holding through BOOST's end: cap +1.037 pp per attempt (full book), +2.6 pp (pick book). Stops gap through: a −30% stop realizes −44% to −53%; 92% is a single-slot jump, lag adds 0.8–1.5 pp [V d11-F1; d03-F5]. Live vs sim: tp +4.92 pp, sl −2.06 pp; time exits calibrated on n = 1 [U g_sim_live F2] | Largest fixable item | 300 s wall-clock cap, pre-signed and timer-fired. Exit lag in ms. Track live − paper twin by exit type |
| **P&L / costs** | 0.05 SOL; 4.44% round-trip cost | Venue fee 2.47% round trip, not cuttable below 420 SOL mcap [V d03-F1]. Fixed costs 2.02% at 0.05 SOL with 500k. Size: impact persists to 0.5 SOL (~0.1 pt); sim flatters 1–2 SOL [V d03-F3] | Probe −0.210755 SOL: price +0.496%, fees 4.486% per trip | At 55k per send, fixed cost is 0.22% at 0.05 SOL and 0.022% at 0.5 SOL, so a 0.05–0.1 SOL validation rung is no longer fee-killed |

**Value bridge on the capped full book, pooled per attempt, live leg** [U g_reachable §3]:
- primary P: +0.659%;
- with the 30-min hold instead: −0.378%;
- without the guard: −0.388%;
- 505k instead of 55k: −0.128 pp;
- START instead of END: +0.547 pp;
- 0.8 s instead of 1.3 s: +0.244 pp;
- exit lag 5 instead of 2: −0.241 pp.

**Pick-only book** (EXP-012 picks) [U]:
- 30-min hold: +0.784% (date lo −1.26);
- 300 s cap: +3.379% (date lo +1.70);
- P2–P4 only: +3.819% (date lo +1.84, 20/28 days, ex-best-day +34.19 SOL), flat leg +3.298%, pressure leg +2.753%.

---

## 5. Strategy verdict

**1. The owner's thesis — FUNDAMENTALLY WRONG at MAL's reachable latency.**
- Refuted independently on the bonding curve, at migration, and for wallet/app influence (§3).
- No priority fee or tip buys the slots where the money is.
- No measured exit sells to slow retail before pre-migration inventory does.

**2. The traded implementation — DEAD at realistic cost.** That is EXP-012 picks, 30-min hold, 500k priority, fresh-quote min_out, 0.05 SOL.
- −0.000439 SOL/trade on 27 dates at deciding costs (the lab's C0 figure, reproduced exactly by d08 §0); d16-F2 [V partial] gives −0.428 mSOL/trade for the frozen selection at k6 / 505k.
- Probe −3.99% per trip, which the simulator predicted.
- The 10-16 FINAL is formally compromised (DEC-016 Am.2) and cannot by itself support live.

**3. CAP-PICK — UNKNOWN.** One deciding experiment remains: a pre-registered forward paper read on post-upgrade walk-2 data, under the DEC-021 bar, with looks at days 7, 14 and 21 (§6, action A1).
- It works by holding into pump.fun's scheduled BOOST bid and leaving before it ends, not by front-running retail.
- Cheap kill checks come first: the structure monitor (BOOST alive), scorer reproduction with realistic execution legs, and a report-only October check where the calendar allows.
- An honest expectation: the true October edge is well below the +3.8% exploration figure. With a subjective prior over the true edge, P(pass) is about 25–30% [S/I]. This uses simulated power of 2% / 15% / 45% / 79% / 96% at a true flat edge of 0 / 1 / 1.9 / 2.8 / 3.8%.

**4. The strongest alternative is CAP-PICK itself.** No other family has a positive measured book at a reachable operating point. The remaining ideas are low-prior (<15% each), exploration-only, and some are already partly refuted:
- organic-age selection (g_early G7): post-hoc best of ~2,100 cells, negative on a sub-bin;
- receiver-wallet preparation (g_whales §6): ρ 0.392 with X SOL, but −0.146 (n.s.) with returns;
- operator-graph veto (g_whales E4): untested;
- inventory-into-BOOST (contrarian): d15's top-of-curve book with a migration+750-slot exit, which lands inside BOOST's window in August, lost −3.9% to −6.0% of stake with 1/15 days positive (d15 §3.3).

---

## 6. The plan

### 6.1 How the three plans were merged, and how each disagreement was resolved

All three plans agree on the diagnosis, the dead book, the CAP-PICK candidate, pausing EXP-021, stopping paid speed, and a zero-credit structure monitor. They disagree on how to gather evidence and how to trade it. The disagreements were resolved with measured evidence, and with new simulations where none existed. The power figures come from a d09-style model on the CAP-PICK pick book itself:
- per-attempt residual SD 34.5% of stake;
- between-day SD 3.09%;
- 88.5 attempts/day.

These are lower than the k6 @ 0.05 template the plans borrowed (41.5% / 3.5%), so the plans' power claims were somewhat pessimistic.

| # | Issue | Fastest | Evidence | Contrarian | Resolution and why |
|---|---|---|---|---|---|
| D1 | Primary exit | 300 s cap | 300 s cap | BOOST-90% + 300 s backstop | **300 s wall-clock cap, timer-fired, as primary.** It is g_reachable's pre-declared primary with the complete table, and needs no live detection. BOOST-90% (D9, 600 s backstop) is better by only 0.06–0.36 pp; with a 300 s backstop it would mostly be the cap anyway. Make BOOST-90% (by `boost_vault` PDA) report-only. Add an InitBoost pre-entry check and a structure-halt rule for a shorter BOOST |
| D2 | Spend a sealed backward block (fresh-0828, or pooled 0828+0808) first | optional | yes, as a kill filter | yes, 12-day Look 1 | **No.** [S] A 6-day read under a "flat mean ≤ 0" kill discards 49% of dead books and wrongly kills 17% of a true +1.9% edge. The evidence plan's harder rule (either-leg mean ≤ 0 or ex-best-day ≤ 0) gives 74% / 38%. Pooled 12 days: 50% / 8% (soft) and 73% / 19% (hard). At zero edge, P(mean ≤ 0) is ~50% at any sample size, so a cheap kill filter cannot be strong. Both blocks are August and pre-upgrade (fresh-0808 is adjacent to explore-0814), the regime furthest from October. Using them needs two DEC-014 exceptions (EXP-012 lineage is barred from blocks) and burns irreplaceable reserves |
| D3 | Route to gate evidence | walk-2 challenger | fresh-0828 then walk 2 | sealed Look 1 + forward-1002 Look 2 as gate evidence | **Owner amends DEC-021 (§2, §5, §4; see O2) for CAP-PICK**: register on walk 2 as the primary-promotion arm without a prior backward PASS, counted as an EXP-012-lineage second test. §2 cannot be met: DEC-014 bars EXP-012 retunes from blocks, and every block is pre-upgrade. **Reject using forward-1002 `[10-03, 10-10)` as gate evidence**: those hours already elapsed before any CAP-PICK pre-registration (the ledger requires the window to be fixed before the first counted hour begins), they are owned by EXP-012's FINAL, and they include the 10-05..10-07 seal-exposure days |
| D4 | Start counting before 10-16 | — | sealed count from ~10-10 (ledger exception) | — | **Owner option, recommended if the Part 1 merges before 10-10T00Z.** Count `[10-10T00, …)` sealed until the FINAL is written. The window is wholly after the 200 ms step and after the breach days. It gains 6 days on every look. Otherwise count from 10-16T01 |
| D5 | Look schedule and bar | day-7 strict interim + futility, day-14 gate | day-7 futility; 14/21/28 OBF | two looks, OBF | **Looks at days 7/14/21: CLAUDE.md gate on both legs plus one-sided p ≤ 0.005 / 0.008 / 0.012 (Bonferroni sum 0.025 = DEC-021 family α, k = 1). Day-7 futility (flat and pressure means ≤ 0) is non-binding for the read and binding for spending.** [S] (binding-futility version): false pass 2.2%; power 15% / 45% / 79% / 96% at +1.0 / 1.9 / 2.8 / 3.8%. A single 14-day read gives 2.3% / 13% / 38% / 69% / 92%; single 21-day, 1.6% / 16% / 49% / 85% / 98%. The fastest plan's design (CLAUDE gate only) has 4.1% false pass and does not meet DEC-021's p ≤ 0.025 |
| D6 | Paper-optimism haircut | "sim may be 1–2 pp optimistic" | haircut if bias ≤ −1.5 pp | "1–2 pp optimistic" | **Not supported, so no pre-registered haircut.** [S] CAP-PICK picks exit 44.7% tp / 37.6% sl / 17.7% deadline (P2–P4), a tp share among tp+sl of 54%, against the probe calibration's break-even 19.9%. The measured residuals (+4.92 pp tp, −2.06 pp sl) would favour live; the g_reachable sim already fills exits at lag 2 END, so the residual is smaller either way. The deadline leg (n = 1) is uncalibrated. Measure it with the scorer's timer leg and the live paper twin by exit type |
| D7 | Live-trial size and stops | 0.25 SOL, −0.5 SOL money kill, ~1 SOL | 0.25 SOL, −0.75 SOL cap, ~1.5 SOL | 0.25 SOL, −1.5 cumulative / 3 SOL total, 1.5 SOL capital | **Size to bankroll; start at 0.05 SOL.** [S] At 0.25 SOL the daily P&L SD is 1.22 SOL. P(−0.5 SOL stop within 14 d) is 48% if the true edge is +1.9% and 21% at +3.8%; for −0.75 SOL, 41% / 16%. The plans' stops would kill a real edge about half the time, and a 1 SOL bankroll is hit 36% of the time at +1.9%. At 0.05 SOL with a −0.5 SOL stop: 15% false stop at +1.9%, 53% true stop at 0. At 0.1 SOL with a −1.0 SOL stop: 15% / 52% |
| D8 | EXP-021 | suspend or amend | pause | do not spend fresh-0802 as written | **Pause now (owner).** Keep the freeze #376 artefacts. If the owner keeps the rug priority, re-express it after CAP-PICK's read as a paired challenger on the CAP-PICK cell, with B6 replaced by ex-best-date. As registered it scores a dead 30-min / 0.05 SOL / 505k book with ~0 confirmation power [V d09-F1] |
| D9 | Speed work | Phase A as a timing stream | end after Phase A | stop Phase B; delay grid | **Stop Phase B and all paid speed. Let #371 finish without extension; merge the 1-line v1 fix.** CAP-PICK keeps the tip trigger, so its features match training. That avoids the 14–18% pick swap of a processed trigger [V d01-F3]. The delay grid (1.3–30 s) is a cheap nice-to-have for architecture |
| D10 | October check before walk 2 | report-only FINAL leg (DEC-016 amendment) | release forward-1002 after FINAL | gate evidence | **If counting starts 10-16 (D4 = no):** CAP-PICK report-only on `[10-06, 10-16)` via a DEC-016 amendment merged before the FINAL. Kill-for-spending rule: flat and pressure means ≤ 0 and ex-best-day ≤ 0. [S] It catches 49% of dead books and wrongly kills 10% of a true +1.9% edge over 10 days; the fastest plan's rule catches only 14%. **If D4 = yes:** skip it; the day-7 look on 10-17 replaces it |
| D11 | Monitor thresholds | span 300–400 s, synthetic >20% | BOOST <50% 2 days, end <300 s, synthetic >25% | share <50%, budget/duration ±20%, synthetic >30% | **One pre-declared rule set** (A3). Halt on: boost off; BOOST on <80% of sampled non-mayhem WSOL graduations for 2 days (or <50% in one day); median last slice <315 s after the migrate tx (margin over the cap from entry under ~15 s); budget or slice count changed >20%; synthetic share >35% for 2 days |
| D12 | Event-V decoder | yes | before 10-16 | defer | **Deploy for walk 2's first hour**, a new walk, so walk #382 is not changed mid-window. Pre-10-16 hours (D4 = yes) use the V map, with the sub-bp error disclosed |
| D13 | Deciding stake | 0.25 | 0.5 (0.25 reported) | 0.25 | **0.1 SOL**, with 0.05 / 0.25 / 0.5 report-only. Results are nearly size-invariant at 55k (0.25 vs 0.5: +0.018 pp; fixed cost 0.11% at 0.1). 0.1 SOL is the largest size a 1–1.5 SOL bankroll carries with a stop a real edge survives |
| D14 | Pick vs control | report-only | required | required | **Report-only.** CAP-PICK is a retune (DEC-014). The live question is the book's own P&L under the gate. Pick − guarded-baseline is pre-declared and reported |
| D15 | Tier-0 harness | separate DuckDB harness | port `G/sim.py` | port `G/sim.py` | **One artefact**: the ported scorer with a Parquet/DuckDB loader serves as both CAP-PICK's scorer and the tier-0 screen |
| D16 | Fallback timing | at the first failed decision point | if a kill fires | pause spend by 10-16 if Look 1 fails | **Tie the wind-down to CAP-PICK's kill points** (A12), not to a calendar date. Owner decision within 24 h of a kill |

### 6.2 Actions that materially change profit (ranked)

Owner decisions are collected in §6.4. "Parallel" means the action can run while the others run, under the owner's constraint of one or two builders at a time (75% of weekly usage on 10-07).

**A1. Make CAP-PICK the single critical-path candidate and pre-register its walk-2 read** (decision + process)
- **Spec**, frozen in ms and SOL:
  - Universe: canonical PumpSwap pool with V in [17.5, 17.7] SOL (non-mayhem, WSOL), with InitBoost present in the migrate tx.
  - Selection: frozen EXP-012 model at 0.8030766588450794, features as the live runner computes them on the tip trigger.
  - Entry: 1.3 s after pool create, END bound. Pessimistic leg at 1.9 s (fixed-build p90), which must show mean > 0 and ex-top-3 > 0, as in DEC-016 Am.3 at k(p90).
  - Guard: on-chain min_out at seed × 1.15.
  - Exit: tp50/sl30 plus a 300 s wall-clock cap, timer-fired; exit lag 550 ms (pessimistic 1.35 s).
  - Costs: 55k lamports per send.
  - Stake: 0.1 SOL deciding.
  - Fail legs: flat 15% and pressure (gate); live 1/62 report-only.
  - Report-only lines: BOOST-90% exit, pick − guarded-baseline, date-cluster CI90, 0.05 / 0.25 / 0.5 SOL.
- **Read design (D5):** looks at days 7, 14 and 21; the gate on both legs plus one-sided p ≤ 0.005 / 0.008 / 0.012; non-binding futility at day 7.
- **Structure halt (A3):** if it fires, hours after the trigger are excluded and the read can end NOT_DECIDABLE. No retune and no re-read.
- **DEC-021 decidability:** name the §4 calibration set now, or condition (c) of a primary-promotion read is NOT_DECIDABLE. Use faa3192's closed fills (≥ 20 from one build), re-run with the patched tool (E1). If r̄ ≥ 0, no adjustment applies; the corrected transfer estimate is +0.84%. State the §7 pre-live checks at CAP-PICK's own operating point: runner-vs-scorer md5 equivalence, the 1.3 s / 1.9 s latency legs, and V-priced fills.
- **Why:** it is the only book positive out of sample for the model at a reachable point (+3.819% per attempt on P2–P4). Freezing it before any new read stops spec drift.
- **Expected impact:** this is the only route to live. If the true edge is half the exploration figure (flat ~+1.9%): ~+0.16 SOL/day at 0.1 SOL and ~+0.41 at 0.25 SOL [S]. Running cost is ~$400/month, about 0.11 SOL/day at $120/SOL.
- **Confidence:** high that it is the right focus; low-to-medium that it survives.
- **Cost:** about 0.5 manager-day plus quant-proof; 0 credits.
- **Time to validate:** Part 1 merged before 10-10T00Z (with the early count) or before 10-15 (count from 10-16T01).
- **Success:** merged before the first counted hour; every parameter in ms or SOL; quant-proof OK.
- **Kill:** superseded only by an A2 or A3 kill. Withdrawn before any read if killed.
- **Depends on:** O2 (and O3 for the early count). **Parallel:** yes. **Owner:** O2, O3.

**A2. Port the scorer, reproduce g_reachable, add realistic execution legs (DP1 kill check)** (build + experiment)
- **What:**
  - `tools/cap_pick_score.py` from `work/g_reachable_cap_book_rescore/sim.py`, with fixture tests, a Parquet/DuckDB loader for exploration, and the clean-view loader for walk tape.
  - Reproduce the P table and the pick-book table to ≤ 0.01 pp.
  - Add legs:
    - (a) the executor's exit loop: 400 ms processed snapshots, trigger on the snapshot, landing +1/+2 slots;
    - (b) the timer-fired deadline sell landing 1–2 slots after 300 s;
    - (c) entry at 1.6 and 1.9 s;
    - (d) the InitBoost filter by `boost_vault` PDA.
  - The manager runs the patched calibration E1 on the 55 trips (10 min; predictions pre-registered in g_sim_live §7).
- **Why:** no read on an unreproduced scorer. Measure the deadline-exit residual, which is the only uncalibrated leg (D6).
- **Expected impact:** this scorer is the read's instrument. It may also bound CAP-PICK's execution correction (expected small and favourable, D6).
- **Confidence:** high.
- **Cost:** about 1.5–2 builder-days; 0 credits; ≤ 3 GB on research-0.
- **Time to validate:** 10-10/11.
- **Success:** reproduction ≤ 0.01 pp. Under the calibrated legs, the P2–P4 pick book's flat mean is ≥ +1.0% per attempt, with date-cluster CI90 lower bound > 0 on the flat leg and mean > 0 at 1.9 s.
- **Kill:** any of those fails → withdraw the Part 1 and go to A12. A reproduction failure means fix first and no read.
- **Depends on:** none. **Parallel:** yes. **Owner:** no.

**A3. Daily structure monitor with pre-declared halt rules** (data)
- **What:** a MiScusi job on public RPC, about 400 calls/day, 0 Helius credits. It reads:
  - GlobalConfig and FeeConfig sha256 against pinned values (`boost_enabled`, `toggle_boost`);
  - programdata deploy slots;
  - ms/slot;
  - the 20 newest graduations: InitBoost in the migrate tx, `boost_vault` PDA slice count and budget, last-slice time after the migrate tx, mayhem flag, quote mint, PostCompleteBuyEvent share;
  - v2 trade share;
  - pump-public-docs commits.
  It posts a notebook entry and a Console event daily. Halt rules as in D11.
- **Why:** CAP-PICK's mechanism is an off-chain-cranked pump.fun parameter. The 10-02 redeploy went unnoticed for 6 days.
- **Expected impact:** prevents trading into a dead mechanism. Holding past BOOST's end costs −9.3% of stake per deadline exit.
- **Confidence:** high. **Cost:** about 0.5 builder-day; 0 credits.
- **Time to validate:** first report by 10-09. Confirm the slot step after 10-09T15Z.
- **Success:** 5 daily rows; a forced test alert fires; BOOST on ≥ 90% of non-mayhem WSOL graduations.
- **Kill:** a halt rule fires → suspend CAP-PICK counting and any live trading the same day.
- **Depends on:** none. **Parallel:** yes. **Owner:** no.

**A4. Pause the EXP-021 screen** (stop/decision)
- **What:** let freeze #376 finish (sunk) and keep its md5s. Do not score the screen. fresh-0802 stays unread (D8).
- **Why:** B6 gives ~0 confirmation power [V d09-F1]. The deciding cell is a dead book. A pass would raise m to 13 and could not go live.
- **Expected impact:** frees about 3–5 manager/builder days and ~6 h × 72 GB research-0 runs. Keeps the reserve block.
- **Confidence:** high. **Cost:** 0. **Time:** owner answer by 10-09.
- **Success:** no screen scored; ledger unchanged.
- **Depends on:** none. **Parallel:** yes. **Owner:** **O1**.

**A5. Build the CAP-PICK live path, keyless until approved** (build)
- **Executor changes:**
  - min_out from seed × 1.15 (reject and log synthetic-migration pools that open above the seed);
  - 55k priority per send, CU limit ~130k buy / ~110k sell;
  - pre-signed sell fired by a 300 s timer;
  - InitBoost check;
  - `track_volume = 0`;
  - pool and both vaults in one `getMultipleAccounts`, accepting V ≤ 0;
  - log the pool-create slot, our in-slot index and decision ms on every buy;
  - ATA-rent and extension tripwire;
  - maximum entry age in ms (2.0 s).
- **Paper twin:** after 10-16, re-point the fast-0 paper runner to CAP-PICK decisions.
- Carry the md5 decision-equivalence replay; run a simulateTransaction shadow on live decisions.
- **Why:** live must trade the book the paper scores. Today it pays for pumped pools, overpays priority about 10×, and has no wall-clock cap.
- **Expected impact:** guard +1.047 pp per attempt (full book); cap +2.6 pp (pick book); priority +1.8 pp at 0.05 SOL. Enables a same-day live start after a pass.
- **Confidence:** high. **Cost:** about 2.5–3 builder-days plus reviewer and Helm re-pin; 0 credits.
- **Time to validate:** shadow results by 10-16 (count from 10-10) or 10-20.
- **Success** over ≥ 200 shadow decisions:
  - guard-reject share within ±10 pp of the scorer;
  - timer sell built and simulated ≤ 2 slots after the deadline at p90;
  - 0 build errors;
  - CU p99 under the limit;
  - md5 EQUIVALENT.
- **Kill:** reject share off by > 20 pp, or the timer misses by > 5 slots at p90 → fix before live (does not block the paper read).
- **Depends on:** A2 pass. **Parallel:** yes. **Owner:** no (deploy needs O5 and Helm).

**A6. Landing canaries and live calibration at 55k** (experiment; DEC-021 §7 requires a live calibration before live at a new priority)
- **What:** about 300 buys with an impossible min_out on real new canonical pools after the 200 ms step, at 55k (one arm at 150k). Measure:
  - landing ms from pool create;
  - in-slot index;
  - landed / failed / dropped rates.
- **Cost:** about 0.02 SOL in fees; a Helm-custody key funded with ~0.05 SOL; about 1 builder-day on the A5 send path.
- **Why:** START vs END is worth +0.547 pp. The 1/62 fail rate was measured at 500k. The 200 ms step changes the slot mapping.
- **Expected impact:** pins ±0.5 pp of the expected edge and the realistic latency leg.
- **Confidence:** medium-high. **Time:** 2–3 days after the key exists (10-13 to 10-15).
- **Success:** landing p50 ≤ 1.6 s and p90 ≤ 2.0 s from pool create; failure ≤ 5%; in-slot index reported.
- **Kill:** p50 > 2.0 s or failure > 15% → re-score A2 at the measured latency before any live request. If the mean is ≤ 0 there, no live.
- **Depends on:** A5 send path, O4. **Parallel:** yes. **Owner:** **O4**.

**A7. Pre-register the live-trial protocol, sized to bankroll** (decision)
- **L1:** 0.05 SOL (DEC-018 rung 1); 1 SOL bankroll; −0.5 SOL money stop; max 3 open; paper twin on identical decisions; ≥ 300 fills (~4 days).
- **Execution kill:** live − twin per-trade CI90 upper bound < −1 pp. Also halt on guard-reject share off by > 20 pp, or on any structure halt.
- **L2:** 0.1 SOL (a new rung; owner yes); bankroll ≥ 1.5 SOL; −1.0 SOL stop.
- **0.25 SOL** only with bankroll ≥ 4 SOL and a −3 SOL stop, after 14 live days with a date-cluster CI90 lower bound > 0.
- **Why:** [S] the plans' 0.25 SOL / −0.5 to −0.75 SOL stops would kill a true +1.9% edge 41–48% of the time within 14 days. These stops do so ~15% of the time, and still stop a zero-edge book 52–53% of the time.
- **Expected impact:** bounded downside (≤ 0.5 SOL at L1), without discarding a real edge.
- **Confidence:** high on the arithmetic. **Cost:** a manager note and an owner conversation.
- **Time:** agreed before the first possible pass (10-17 with the early count, 10-23 otherwise).
- **Success:** owner signs; Helm key path ready. **Kill:** n/a.
- **Depends on:** A1, A5. **Parallel:** yes. **Owner:** **O5**.

**A8. Walk-2 integrity and pricing** (build)
- **What:**
  - Event-V decoder: signed `virtual_quote_reserves` from Buy/SellEvent, `ix_name`, `creator_fee_unclaimed`, PostCompleteBuyEvent, BoostBuyAndBurnEvent, SweepPoolFeeEvent. Fixture tests; md5 replay. Deploy as the walk-2 job's first hour.
  - `JsonlSink` must refuse to resume when the file is shorter than the checkpoint (the NUL-hole source).
  - Gate readers count bad lines and refuse when there are any.
  - Per-hour slot_ms in the scorer.
  - Budget walk-2 credits at +26–34% after the step (~410–430k/day).
- **Why:** a crash during walk 2 would otherwise silently drop counted hours [V d13-F4]. Per-trade V grows with v2 adoption.
- **Expected impact:** prevents a NOT_DECIDABLE or wrongly priced read.
- **Confidence:** high. **Cost:** about 1.5 builder-days; 0 extra credits.
- **Time:** merged before 10-16T01.
- **Success:** fixture tests pass; event V matches account reads on sampled rows; a forced short-file resume refuses.
- **Depends on:** none. **Parallel:** yes. **Owner:** no.

**A9. Restate every slot-denominated parameter in ms before ~10-09T14:34Z** (process)
- **What:** CAP-PICK spec, executor `max_entry_k_slots: 8`, exit lag, caps, and `SLOT_MS = 400` in the simulators.
- **Why:** a 750-slot cap is 200 s at 267 ms and ~150 s at 200 ms.
- **Expected impact:** prevents silent parameter drift: 300 s vs 420 s is worth +0.28 to +1.6 pp per fill.
- **Confidence:** high. **Cost:** about 0.5 day.
- **Success:** tests pass at 200, 267 and 416 ms per slot.
- **Depends on:** none. **Parallel:** yes. **Owner:** no.

**A10. Stop paid speed and DEC-022 Phase B** (stop)
- **What:** let Phase A #371 end (~10-08T20:30Z) with no extension. Merge the one-line `maxSupportedTransactionVersion: 1` fix in `tools/fast_grad_stream.py:236`, `tools/migration_stream_probe.py:82` and `tools/fee_landing_study.py:415`. No co-location, ShredStream, LaserStream, Sender/Jito tiers or migrate+buy race.
- **Why:** k≤1 is owned by bundles and same-slot reactors. 0.8 s vs 1.3 s is +0.244 pp, all from August. A slot earlier is not where money is lost.
- **Expected impact:** saves 2–4 builder-days, avoids $400–1,000/month of paid feeds, and ends ~130k credits/day of stream.
- **Confidence:** high. **Cost:** 0.
- **Success:** no Phase B work after today. **Kill:** n/a; reopen only if A6 shows in-slot position responds to priority (a priority A/B, not infrastructure).
- **Owner:** inform (DEC-022 is a proposal).

**A11. October report-only check — only if counting starts 10-16T01 (D10)** (experiment)
- **What:** a DEC-016 amendment merged before the FINAL adds a pre-declared, outcome-blind, report-only CAP-PICK leg on `[10-06, 10-16)`, read after the FINAL is written.
- **Why:** the first post-upgrade look, 7 days before the day-7 walk-2 look.
- **Expected impact:** [S] stops spending on 49% of dead books by 10-16/17, at a ~10% false-kill cost if the true edge is +1.9%.
- **Confidence:** medium (one compromised window, report-only).
- **Cost:** about 0.5 builder-day using the A2 scorer.
- **Kill rule (binding for spending, not for the walk-2 read):** flat and pressure means ≤ 0 and ex-best-day ≤ 0.
- **Depends on:** A2. **Owner:** no.

**A12. Pre-declare the fallback** (decision)
- **Trigger:** any CAP-PICK kill — structure halt, A2 fail, or failure at the day-21 final look. A day-7 futility or A11 signal pauses build spending but does not end the read.
- **What the owner is told plainly:** no measured pump.fun book is positive-EV at MAL's latency and cost.
- **Recommendation:**
  - cut running cost to the minimum: the A3 monitor, the tip-tape archive, the tier-0 scorer, walk 2 only if another arm is registered;
  - keep the sealed blocks unread;
  - run only the low-prior challenger lane (N3) on post-10-16 forward data.
- **Expected impact:** caps further spend at about $100–200/month instead of ~$400.
- **Confidence:** high that this is the right response if CAP-PICK fails. **Cost:** 0.
- **Success:** owner decision recorded within 24 h of a kill.
- **Owner:** **O7**.

### 6.3 Nice to have (do only with spare capacity)

| # | Action | Why | Cost | Success / kill |
|---|---|---|---|---|
| N1 | Governance time-box: one Part 1 plus ≤ 1 amendment per candidate; ≤ 1 handoff/console PR a day; quant-proof only on decisions and edge claims; no new FINAL tooling | 28% of Claude-era PRs were governance and 25% console/handoff (bucket caveat). LLM usage, not credits, is the binding resource | 0 | Governance + handoff < 20% of merged PRs on 10-09..10-16 |
| N2 | After 10-16, with a coverage proof: `slotSubscribe` instead of getSlot polling; no duplicate stream feeds | ~216k credits/day (~6.5M/month, ~$32); Phase A stream ~130k/day | ~0.5 builder-day | Credits/day down ≥ 35%, coverage stays 100.000% (revert below 99.9%) |
| N3 | Low-prior challenger lane, tier-0, exploration plus forward-1002 after its release, report-only, one frozen cell each: organic-age selection under the CAP-PICK exit; receiver-wallet preparation (outcome-blind precount first, ~0.1–0.17M credits); operator-graph veto; delay grid 1.3/3/6/12/30 s (could retire the sub-second stack if 6 s keeps ≥ 70% of value); inventory-into-BOOST (lowest prior, d15 S3) | Seeds a fallback. Each <15% prior | ~1 builder-day each | ≥ 1 pp over control on discovery and validation, same sign, date lo > 0; otherwise close with no retune |
| N4 | EXP-021 successor: rug features as a paired challenger on the CAP-PICK cell (B6 replaced by ex-best-date), after CAP-PICK's read | Honours the owner's rug-filter priority on a book that can trade | ~1 day plus its own Part 1 | Paired gain ≥ 1.5 pp on exploration before any block claim |
| N5 | App tags in the decoder (FOMO fee payer `AgmLJ…`, Axiom/GMGN/… fee wallets, router ids) from `accountKeys`, as a regime monitor, not a trigger | Free; tracks the Axiom-sniper and FOMO regimes | ~0.5 day | Daily shares logged |
| N6 | Close EXP-009 (its direction is refuted: serial creators are monotonically worse, −0.60 → −1.71 mSOL/trade). Keep `[09-15T12, 09-19T01)` sealed as a reserve | Removes a stale owner; the most recent unread historical data stays held | 0 | Owner yes (O6) |
| N7 | Universe and number hygiene: exclude WHALE50/V0/mayhem from base rates; keep `quote_mint` and `signature` in tables; handle null `tx_index` (oracle-insample); correct LAB_STATE's stale lines | Stops misreads | ~0.5 day | — |
| N8 | Counts-only NUL/gap scan of fresh-0802/0808/0828 before any future sealed read | Three NUL holes were found in exp011-0909 | hours, 0 credits | 0 holes, or holes excluded by a pre-declared rule |
| N9 | Reclaim the stranded 1,346,200 lamports (`close_user_volume_accumulator`; Helm) | Trivial money; tidy | minutes | — |

### 6.4 Owner decisions needed (one consolidated ask, today)

| ID | Decision | Recommendation | Deadline |
|---|---|---|---|
| O1 | Pause the EXP-021 screen (freeze artefacts kept; fresh-0802 unspent) | Yes | 10-09 |
| O2 | Amend DEC-021 so CAP-PICK (EXP-022) can register on walk 2 as the primary-promotion arm. Three parts: (§2) no backward-block PASS required; (§5) the 7/14/21 looks with a p ≤ 0.005 / 0.008 / 0.012 alpha split (family α 0.025, k = 1) replace the single read; (§4) the named calibration set is faa3192 via E1 | Yes. Otherwise there is no gate-valid route that does not bend DEC-014 | before the Part 1 merges |
| O3 | Ledger exception: CAP-PICK counts `[10-10T00, 10-31T00)`, sealed until the 10-16 FINAL is written (second owner of forward-1002 hours `[10-10, 10-16)`) | Yes, if the Part 1 can merge before 10-10T00Z; otherwise count from 10-16T01 | 10-09 |
| O4 | DEC-019 amendment: landing canaries and live calibration at 55k with a Helm-custody key (~0.05 SOL) | Yes (required by DEC-021 §7 before live at a new priority) | 10-10 |
| O5 | Live-trial terms and bankroll: L1 0.05 SOL / 1 SOL / −0.5 SOL stop; L2 0.1 SOL (new rung) / ≥ 1.5 SOL / −1.0 SOL; 0.25 SOL only with ≥ 4 SOL bankroll | Yes | before the first possible pass (10-17 or 10-23) |
| O6 | Close EXP-009, keep its block sealed | Yes (optional) | any time |
| O7 | Pre-agree the fallback (cost wind-down) if CAP-PICK dies | Yes | decided at the trigger |

### 6.5 Parallelism and staffing

- **D0–D2:**
  - builder 1: A2 (scorer);
  - builder 2: A3 (monitor), A9 (ms) and the A10 one-line fix;
  - manager: A1 Part 1, owner asks, E1 calibration run, quant-proof.
- **D3–D7:**
  - builder 1: A5 (executor), then A6;
  - builder 2: A8 (walk-2 decoder and integrity);
  - manager: reviews, md5 replays, the A11 amendment (if count from 10-16), A7.
- **Hosts:**
  - research-0: one heavy job at a time; the freeze #376 holds 72 GB until it ends; the scorer needs ≤ 3 GB.
  - fast-0: jobs ≤ 1.9 GB, as the handoff requires.

---

## 7. Stop doing (with savings)

| Stop | Evidence | Saves |
|---|---|---|
| Scoring EXP-021 as registered: 30-min hold, 0.05 SOL, 505k, B6 | Pipeline power 0.000–0.005 [V d09-F1]; the deciding cell is a dead book | 3–5 days; ~6 h × 72 GB runs; fresh-0802; m stays 12 |
| DEC-022 Phase B and every paid speed product | k≤1 belongs to bundles and in-slot reactors; 0.8 s vs 1.3 s is +0.244 pp, all August [U g_reachable, g_early] | 2–4 builder-days; $400–1,000/month avoided; ~130k credits/day of stream |
| New tooling, amendments, snapshots or dry-runs for the 10-16 FINAL beyond what is merged; accept (B) NOT_DECIDABLE if §5 fails | Compromised; cannot support live [V d10-F4; U v_per_trade §5] | 1–2 manager days |
| Spending sealed backward blocks as kill filters for EXP-012-lineage books | [S] a 6-day read catches ~49% of dead books and wrongly kills 17–38% of a true +1.9% edge | Three irreplaceable reserves; DEC-014 exceptions |
| The 30-minute max hold, slot-count caps and `SLOT_MS = 400` anywhere | Cap +0.9 to +3.1 pp per fill; 750 slots = 200 s at 267 ms | — |
| 500k priority, 250k CU limit, `track_volume = 1`, fresh-quote min_out, 120 s stale-signal age | RC3, RC5; g_sim_live F7 | ~0.9M lamports per round trip; 1,346,200 lamports per new wallet |
| Selector retrains, regime gates and screens on the 27 non-P1 dates | m = 12, p < 0.00208; 2 of 66 regime cells, about chance [V d14-F4]; 36 features add nothing [V d08-F3] | Tries and power |
| "Front-run slow retail" designs: retail-wave triggers or exits, app-flow or Axiom-momentum triggers, KOL/wallet-follow, curve entries ≥ 1 slot after an event | §3 | Builder hours |
| Treating the unselected or buy-all migration book as a candidate | Negative on fast-pool-0918 in all 12 configs × 3 legs [U g_reachable G-F1] | — |
| WHALE50/V0/mayhem rows in base rates, cohort tables or fill metrics; heuristic BOOST detection | g_whales A1; g_october F4 (PDA identity 99.58%) | Misreads |
| Live trials at 0.25 SOL on a ~1 SOL bankroll with ≤ 0.75 SOL stops | [S] 41–48% false stop at a true +1.9% edge | Avoids killing a real edge or the bankroll |
| 72 GB / 6 h precounts for first looks | 78 s / 2 GB columnar screen [V d10-F3] | Days per family |
| Event-by-event handoff, console and LAB_STATE PRs | 48 handoff PRs; LLM usage binding | LLM budget |
| getSlot polling (after 10-16, with a coverage proof) | ~39% of tip credits | ~6.5M credits/month (~$32) |
| Peeking at walk-2 outcomes outside the pre-registered looks | DEC-016 Am.2 precedent | The read's validity |
| **Quoting refuted or superseded numbers:** the 10-01 PASS as edge; d02's +1.38% live−sim, −2.43% exit lag, +17.6 bps and its size-table levels; d06's positive KOL k=1 cells; d08 s17's +0.007441 and its "micro-pool simulator noise"; "65% Oracle training rows"; d01-F1's −12% medians as MAL-relevant; d14-F10 "drained pools implausible"; "200 ms at 10-08T06:29Z"; d13-F2 "START−END is mostly exits"; d07's "300 s" label (it was 750 slots); "3 of 4 gap-through stops"; "k2−k6 reverses in September" (CIs span 0); "1.03 pts size cost at 0.5 SOL"; d12-F2 "pre-migration holders win" without a class split; arXiv 2607.02795 (withdrawn); fomotrading.app latency (affiliate claim); "98.9% vs 28% fills" as edge; "training data unaffected"; #151 "exits are dead"; the 09-27 "latency curve is flat" | xcheck, verifier verdicts, g reports | Avoids decisions on wrong numbers |

---

## 8. Data to collect (cheapest way)

| Data | Why | Cheapest way | Cost |
|---|---|---|---|
| Daily October structure (BOOST profile, config hashes, synthetic share, v2 share, ms/slot) | CAP-PICK's mechanism | A3 monitor, public RPC | 0 credits |
| 200 ms activation | All slot mappings | `getRecentPerformanceSamples` after 10-09T15Z | 2 calls |
| Landing ms, in-slot index and fail rate at 55k | START/END (+0.547 pp); the DEC-021 §7 precondition | A6 canaries | ~0.02 SOL |
| Exact corrected calibration on the 55 trips | Replaces the transfer estimate (+0.84%) | Manager runs the patched tool (g_sim_live §7) on the tip tape | 10 min |
| Deadline (timer) exit fill quality | The only uncalibrated exit leg (17.7% of CAP-PICK exits) | A2 timer leg, then the L1 paper twin by exit type | 0 |
| Event V(t), PostCompleteBuy and BOOST events on walk 2 | Pricing as v2 adoption grows; synthetic migration | A8 decoder | 0 credits |
| k against pool create for every live buy | Was unverified for a week | A5 logging (create slot and in-slot index) | 0 |
| App tags (FOMO payer, Axiom/GMGN fee wallets) | Regime monitor | N5, from `accountKeys` already fetched | 0 |
| Real monthly bill split and a SOL/USD series | Economics | Owner pastes invoices once | owner minutes |
| LLM usage by task | The binding resource | MiScusi usage export | minutes |
| NUL/gap status of the sealed blocks | Integrity before any future read | N8 counts-only scan | hours |
| (Optional) FOMO push latency | Thesis closure only | Owner logs 30 pushes | ~1 owner-hour |

---

## 9. Timeline and decision points (next 3 weeks)

**Option X** = owner approves O3 (count from 10-10T00Z). **Option Y** = count from 10-16T01.

| Date (UTC) | What happens | Decision point |
|---|---|---|
| **Thu 10-08 (D0)** | Owner ask (O1–O7). Start A2, A3, A9 and the A10 one-line fix. Manager drafts the A1 Part 1 and runs the E1 calibration. #371 Phase A ends ~20:30Z, not extended. Freeze #376 finishes; no EXP-021 screen | — |
| **Fri 10-09 (D1)** | First monitor report. 200 ms step ~14:34Z, confirmed after 15Z; A9 merged before it. Part 1 through quant-proof. **X:** Part 1 merged before 10-10T00Z | **DP0:** BOOST alive and unchanged? If a halt rule fires, CAP-PICK is dead; go to A12 |
| **Sat 10-10 – Sun 10-11** | A2 reproduction and realistic legs. **X:** counting runs (sealed) | **DP1:** A2 pass or kill. Kill → withdraw Part 1, go to A12 |
| **10-12 – 10-15** | A5 build, review, md5 replay, simulateTransaction shadow. A6 canaries (after O4). A8 decoder and integrity merged. A7 protocol signed. **Y:** Part 1 merged by 10-15; A11 amendment merged | Canary kill → re-score at the measured latency |
| **Fri 10-16** | FINAL runs as written (~02Z). Walk 2 starts 10-16T01 on the A8 decoder. Paper twin re-pointed to CAP-PICK (md5). N2 credit cuts begin. **Y:** A11 October check after the FINAL | **DP2 (Y):** October kill-for-spending rule |
| **Sat 10-17** | **X: day-7 look.** Early efficacy at p ≤ 0.005 plus the gate → owner live request; L1 at 0.05 SOL from ~10-18. Futility → pause A5/A6 spending and tell the owner failure is likely; the read continues | **DP3 (X)** |
| **Fri 10-23** | **Y: day-7 look** (as above). **X:** L1 in progress if passed (≥ 300 fills by ~10-22) | **DP3 (Y)** / **DP6 (X):** L1 execution check → L2 at 0.1 SOL (owner yes) |
| **Sat 10-24** | **X: day-14 look** (p ≤ 0.008 plus gate) → live request if not already live | **DP4 (X)** |
| **Thu 10-29** | End of the 3-week window. **X:** CAP-PICK is either live at L1/L2, or awaiting the day-21 look (10-31). **Y:** awaiting the day-14 look (10-30) | — |
| Sat 10-31 / Fri 11-06 | Day-21 final (X / Y): p ≤ 0.012 plus gate → live request; otherwise retire CAP-PICK (no retune, no re-read) → A12 | **DP5** |
| +14 live days | Live date-cluster CI90 lower bound > 0 and bankroll ≥ 4 SOL → owner may step to 0.25 SOL | **DP7** |

**Honest expectation:**
- Fastest real-money start: ~10-18 (X) or ~10-24 (Y). This needs a strong early pass: about 50% likely at day 7 if the true edge is the full exploration figure, under 10% if it is half [S] (H3 "decided by day 7" is 55% / 29%, which includes futility signals).
- Fastest "no": 10-09 (DP0), 10-10/11 (DP1), or a futility signal at 10-17 or 10-23.
- P(CAP-PICK clears the walk-2 read) is about 25–30% under a subjective prior [S/I].
- The owner's 2–3 week cost-recovery target is reachable only if the true October edge is ≥ ~+2% per attempt and the bankroll allows 0.25 SOL. That would be ~+0.41 SOL/day at +1.9%, but with a daily SD of ~1.2 SOL [S].

---

## 10. Risks, and what would change the plan

| Risk | Signal | Plan change |
|---|---|---|
| pump.fun changes or stops BOOST (`toggle_boost`, cadence, budget), or redeploys | A3 halt rules | Suspend counting and live the same day. Re-specify as a new EXP (no retune of the counted read), or go to A12 |
| Winner's curse and decay: the October edge is ≤ 0 (the oracle pick book is −0.414%; the daily mean is falling) | Day-7 futility or the A11 kill | Pause spending; let the read finish (non-binding); A12 at the final |
| Synthetic migration adopted | Synthetic share > 20% (flag) or > 35% for 2 days (halt) | The guard rejects more and whales vanish from the decoded tape. Re-specify the guard against the pool-open price, as a new EXP |
| Live does not trade the paper book (deadline exits, guard rejects at 55k, in-slot position, sell failures) | A6 canaries; L1 live − twin by exit type | Execution kill at L1; fix before L2. A paper pass is not live evidence |
| Bankroll variance (daily SD ~1.2 SOL at 0.25 SOL) | Drawdowns at L2 | Stay at 0.05–0.1 SOL until the bankroll is ≥ 4 SOL. Stops pre-agreed |
| The owner declines O2 | — | No gate-valid route without a DEC-014 exception. Track CAP-PICK as report-only paper; no live; A12 |
| The owner declines O3 | — | Count from 10-16T01; run A11; looks move 6 days later |
| The 200 ms step changes latency more than expected | A6 shows p50 > 2.0 s | Re-score at the measured latency; the 1.9 s leg must stay positive |
| Crowding: others learn to sell before BOOST ends | BOOST-window flow and pre-BOOST-end returns in the monitor | Expect the read to fail; no retune |
| Integrity: walk-2 NUL holes, coverage gaps, seal breaches | A8 reader refusals; coverage checks | NOT_DECIDABLE for affected hours, disclosed. Allowlisted timing-only reads before looks |
| Owner pressure to read early, extend after peeking, or size up | — | Pre-registered looks and stops only. Any change needs a dated amendment written before data |
| Capacity: OOM, LLM usage | MemoryCurrent; usage export | One heavy job at a time; ≤ 2 builders; N1 time-box |

**What would make the plan more aggressive:**
- A2 shows execution legs favourable and A6 lands ≤ 1.3 s → more confidence in sizing. The read stays as registered.
- An early efficacy pass at day 7 → live at L1 within a day.

**What would make it more conservative:**
- Any halt, or the pick book negative on the October check → stop build spending immediately.

---

## 11. Appendix

### 11.1 Every finding with its verification verdict

Status is the investigator's own label. "Verdict" is the skeptic's verdict where one exists. g, xcheck and v reports had no separate skeptic pass [U]. **Refuted readings are marked and never used as support.**

**d01 — discovery and latency**

| ID | Finding (short) | Status | Verdict / correction |
|---|---|---|---|
| d01-F1 | k5 entry is inside the distribution phase; −12.26% median to +60 s | measured | partially confirmed — **composition artefact**: mayhem pools are 40.3% (Aug) / 53.6% (Sep) of the "normal" set. Not MAL-relevant as stated; use g_whales / g_app |
| d01-F2 | EXP-012 trained 65% on incomplete Oracle tapes; boundary fragile | measured | partially confirmed — **26.6%**, pool B only; pool B timestamp skew is the bigger issue; fragility holds |
| d01-F3 | DEC-022 fast trigger changes 14–18% of picks | measured | partially confirmed — core number holds; fix and "~3 slots of warning" overstated |
| d01-F4 | 25–46% of graduations are migration-slot bundles; fill lift = avoiding them | measured | partially confirmed — counts hold; buys real |
| d01-F5 | Slot time 415 → 267 ms | measured | not separately verified; consistent with verified d04-F5, d11-F3 |
| d01-F6 | End-to-end k unverified | inferred | superseded — g_early G1 verified k on chain |
| d01-F7 | getSlot polling ~39% of tip credits | inferred | not verified; consistent with d03-F8 |
| d01-F8 | Brief wrong: PumpSwap reserves pre-trade | measured | confirmed by xcheck |
| d01-F9 | No attention or app data | measured | partially addressed by g_app |
| d01-F10 | Restart blind spot small (0.89%) | measured | not verified |

**d02 — simulator honesty**

| ID | Finding | Status | Verdict / correction |
|---|---|---|---|
| d02-F1 | 10-01 PASS false positive from the gate operating point | measured | partially confirmed — passes only at the gate config; fails at k2/3/4/6 START lag 2 under all fail legs |
| d02-F2 | Gate passes the unfiltered book at its config | measured | partially confirmed — numbers right; five corrections to framing |
| d02-F3 | "Predicted fills" = skipping mayhem dust and giant first-slot buys | measured | **confirmed** |
| d02-F4 | Execution honest; live − sim +1.38% | measured | partially confirmed — rests on 3 tp trips; **superseded by g_sim_live F1: +0.84%** (wrong-convention tool) |
| d02-F5 | Fail models calibrated to the wrong target | measured | not separately verified; consistent with d09-F4 [V], d16-F7 |
| d02-F6 | END bound also delays the exit | inferred | see d13-F2 verdict (split roughly half entry, half exit) |
| d02-F7 | Exit lag 1–2 slots costs 2.4% | measured | superseded — g_sim_live F4: −2.23%, ~80% crossing-slot remainder |
| d02-F8 | Per-trade sim P&L peaks near 0.25 SOL | measured | shape survives; levels shift (g_sim_live §3.3) |
| d02-F9 | Sim trigger is first print, 0–2 slots after migrate | measured | consistent with g_whales F5 (class mix) |

**d03 — cost economics**

| ID | Finding | Status | Verdict / correction |
|---|---|---|---|
| F1 | Venue fee binding; no robust gross edge clears it | measured | partially confirmed — fees right; buy-all base rates misread (mixed population) |
| F2 | Priority overspend ~10×; CU limit 2.2× use | measured | partially confirmed — 112k CU per buy measured; 87k sell not measured |
| F3 | Size-cost numbers disagree ~10× at 0.5 SOL | measured | partially confirmed — **5×** for DEC-020 / EXP-021; 10× only for probe-final |
| F4 | 2–3 weeks certifies only ≥ 2% | inferred | partially confirmed — numbers reproduce; wording too strong |
| F5 | 30% stop behaves like −44% / −53% | measured | not verified; consistent with d11-F1 [V], d07-F7 |
| F6 | 0.05 SOL unprofitable because of 500k | inferred | not verified; consistent with g_reachable (55k) |
| F7 | Opportunity counts (1,070–1,166 grads/day), concurrency | measured | not verified |
| F8 | Half the tip-follower credits are getSlot | inferred | not verified |
| F9 | Tape semantics (pre-trade PumpSwap) | measured | consistent with xcheck |
| F10 | Cheaper-fee universes untraded | speculative | — |
| F11 | 15% min-out sandwichable at 2 SOL | inferred | — |
| F12 | Early-curve entries ~3× impact | measured | — |

**d04 — bonding curve thesis**

| ID | Finding | Status | Verdict / correction |
|---|---|---|---|
| F1 | 0 of 3,168 cells ≥ 1 slot positive | measured | partially confirmed — holds; depends on the graduation-exit approximation (xcheck: a PumpSwap-exit variant makes some E07/E09 cells positive; none survive the top-3 drop) |
| F2 | Profit taken inside the event slot by ~13k sniper wallets | measured | partially confirmed — numbers hold; intra-slot ladder caveat |
| F3 | No delayed wave; bots and creators distribute | measured | partially confirmed — **89–94% of the [0,1) s "human" peak is same-slot bundle flow** |
| F4 | Zero-sum: creators and transfer-recipients win | measured | not verified |
| F5 | Slot-time steps; 08-21 is the transition day | measured | partially confirmed — steps right (epoch boundaries); **08-21 link refuted** |
| F6 | TP/SL, human-flow, influencer loopholes all fail | measured | not verified |
| F7 | Costs not binding on the curve | measured | not verified |
| F8 | Curve population shift; mayhem curves unmodelled | measured | not verified |
| F9 | DuckDB IO stall (process) | measured | — |

**d05 — migration thesis**

| ID | Finding | Status | Verdict / correction |
|---|---|---|---|
| d05-F1 | No robust edge at k ≥ 3; only m0+1 START survives | measured | partially confirmed — reproduced exactly; 56 duplicate cells |
| d05-F2 | 42–60% PUMPED in the migration tx; sim counts them as free MISSes | measured | partially confirmed — reproduced |
| d05-F3 | 1–5 s buyers pay curve holders and slot 0–2 snipers | measured | partially confirmed — computed right; **SOL-weighted metric does not transfer to MAL-size trades** (use g_whales / g_app) |
| d05-F4 | Speed not the lever: EXP-012 book flat k2 → k40 | measured | partially confirmed — numbers right; interpretation overstated; k3–5 comparison holds |
| d05-F5 | No delayed one-sided wave | measured | not verified; consistent with d14-F3 [V] |
| d05-F6 | Block regime dominates (−4.3% to +5.5%) | measured | not verified; consistent with g_reachable |
| d05-F7 | 120 s exit beats tp50/sl30 | inferred | consistent with d07 / g_reachable |
| d05-F8 | Copying snipers does not transfer | measured | not verified |
| d05-F9 | oracle-insample has no `tx_index` | measured | consistent (d06, d12, g_reachable) |
| d05-F10 | V0 micro-graduations untradeable | measured | consistent (g_whales) |

**d06 — wallet influence**

| ID | Finding | Status | Verdict / correction |
|---|---|---|---|
| F1 | KOL follower flow real but front-loaded below fees | measured | partially confirmed — numbers match |
| F2 | Copy bots take the leader's slot | measured | partially confirmed — copier-window definition inflates same-slot share |
| F3 | Only KOL k=1o positive; KOL_sel failed its one-shot | measured | partially confirmed — conclusion stands; **sim misprices PumpSwap; positive k=1 cells are artefacts (xcheck)** |
| F4 | Tape lacks fee payer; FOMO unlabeled | measured | partially confirmed |
| F5 | Influence persistent; t-stat attractors are bots | measured | not verified |
| F6 | Prior wallet work closed by process | measured | not verified |
| F7 | `tx_index` nulls; mint-keyed PumpSwap includes non-SOL pools | measured | consistent |
| F8 | KOL sell as an exit signal | speculative | — |

**d07 — exits**

| ID | Finding | Status | Verdict / correction |
|---|---|---|---|
| d07-F1 | 30-min hold is the leak; a 5-min cap robustly beats it | measured | partially confirmed — gain real in the tape sim (+2.79 / +2.31 / +1.04 pts) |
| d07-F2 | #151 measured pre-V, k=1, worst block | measured | not verified |
| d07-F3 | Trap pools 18–51%; no live guard | measured | partially confirmed — shares and code right; "k2 cannot see" argument wrong; the fix omits a cost |
| d07-F4 | Even capped, the plain book is regime-dependent | measured | partially confirmed — direction holds; **late-block figures, EXP-012 attribution and the "300 s" label wrong (750-slot cap)** |
| d07-F5 | 4 holds > 300 s = 54.5% of the probe loss | inferred | partially confirmed — 54.5% of net, 14.1% of gross; **2 of 4** gap-through stops |
| d07-F6 | Entry decides most of the outcome; no state exit beats the cap | measured | not verified |
| d07-F7 | Sim reproduces live stop slippage | measured | not verified; consistent with g_sim_live |
| d07-F8 | Cap-effect magnitude ~3× the lab's | measured | not verified |

**d08 — model selection**

| ID | Finding | Status | Verdict / correction |
|---|---|---|---|
| d08-F1 | EXP-012 = fill classifier; no ranking among fills | measured | partially confirmed — core holds; **"selected fills worse" reverses inside normal pools** (mayhem mix); overfit stronger |
| d08-F2 | Label rewarded fills under wrong pricing | measured | partially confirmed — numbers reproduce; reading partly overstated |
| d08-F3 | 36 new features add nothing | measured | partially confirmed — label mixes fill into win |
| d08-F4 | up20 overlay failed fresh P1 dates | measured | partially confirmed — does not replicate; P1 test confounded |
| d08-F5 | Mayhem positive in sim, decayed | measured | partially affected — xcheck voids the "simulator noise" reason; decay stands |
| d08-F6 | Real wallets: fast entry and sub-minute exit lose | measured | not verified |
| d08-F7 | n_eff; ~10k trades to see +1% | measured | not verified |
| d08-F8 | Feature builder blind for instant graduations | measured | not verified |
| d08-F9 | k6 fill rate falling | measured | not verified |
| d08-F10 | Path labels predictable, P&L not | measured | xcheck: AUCs survive the fix |

**d09 — evaluation power**

| ID | Finding | Status | Verdict / correction |
|---|---|---|---|
| d09-F1 | B6 → ~0 power | measured | partially confirmed — 0.000–0.005 |
| d09-F2 | MDE ~4–5% in 2–3 weeks | measured | partially confirmed — understated (80% at +4.5% / 14 d) |
| d09-F3 | Assumption bias, not multiplicity, made the false PASS | measured | **confirmed** |
| d09-F4 | Pressure leg contradicted by live | measured | partially confirmed |
| d09-F5 | Day effects; trade CI anti-conservative | measured | consistent with d14-F1 [V] |
| d09-F6 | k2−k6 lever fails 0.00208 and B6 | measured | not verified |
| d09-F7 | Forward tournament idea | inferred | — |
| d09-F8 | Gate cannot see lottery edges | measured | not verified |
| d09-F9 | V=0 pool price artefacts | measured | consistent |
| d09-F10 | Throughput not the constraint | measured | consistent with d10 |
| d09-F11 | No return ranking among fills on P1 | measured | consistent with d08-F1 |

**d10 — process and spend**

| ID | Finding | Status | Verdict / correction |
|---|---|---|---|
| d10-F1 | 14% of PRs new hypotheses | measured | partially confirmed — reproduces only under the investigator's mapping; bucketing drives the 14% |
| d10-F2 | Pool over-mined; 4.13M credits of sealed blocks unread | measured | partially confirmed — only 9 of 12 families actually read outcomes on the 27 dates |
| d10-F3 | Tier-0 harness 78 s vs 6 h | measured | partially confirmed — speed reproduces; ratios framed too strongly |
| d10-F4 | 10-16 FINAL is a sunk cost | measured | partially confirmed — compromised; confirmation must come from a second EXP-012-lineage test on walk 2 |
| d10-F5 | ~1M credits/day on duplicate feeds | inferred | not verified |
| d10-F6 | Outcome-less failures ~8–10 days | measured | not verified |
| d10-F7 | Binding resources: LLM and calendar | inferred | not verified |
| d10-F8 | Reviewer catches real | measured | not verified |
| d10-F9 | Search narrow; no net retail wave | measured | consistent with d14-F3 |
| d10-F10 | ~30% dust-pool graduations | measured | consistent (V0) |
| d10-F11 | Dead code, handoff churn | measured | — |

**d11 — live execution**

| ID | Finding | Status | Verdict / correction |
|---|---|---|---|
| F1 | Probe loss is stop gap-through | measured | partially confirmed — reproduces; builds pooled; wording overreaches |
| F2 | k2−k6 reverses in September | measured | partially confirmed — **"reverses" overstated: P3 −0.466, P4 −0.935, CIs span 0** |
| F3 | Slot time 4 steps; 200 ms at 10-08 | measured | partially confirmed — regimes right; **200 ms timing superseded (epoch 1053, ~10-09T14:34Z)** |
| F4 | Instant bundle launches pumped ~20× at k0 | measured | partially confirmed — numbers reproduce; causal story does not; after-cost figures ~1.2 pp too pessimistic |
| F5 | Latency budget | inferred | g_early G1 measured create→read p50 3, read→land p50 2 |
| F6 | Live vs sim fill rules differ | inferred | not verified; consistent with g_reachable G-F4 |
| F7 | Priority ~10× | measured | consistent with d03-F2 [V] |
| F8 | Earlier trigger computes features on another state | inferred | consistent with d01-F3 [V] |
| F9 | 4-slot blind window; 400 ms polling | measured | not verified |
| F10 | Live coupled to a drifting runner; no health alarms | inferred | not verified |
| F11 | Data notes (pre-trade; oracle tx_index) | measured | consistent |
| F12 | Sandwich exposure small | measured | not verified |
| F13 | Canary design | inferred | — |

**d12 — market landscape**

| ID | Finding | Status | Verdict / correction |
|---|---|---|---|
| F1 | BOOST is a scheduled 17.585 SOL buyer | measured | partially confirmed — facts reproduce; profit impact overstated |
| F2 | Pre-migration holders win every day | measured | partially confirmed — numbers right; **reading does not hold without a class split** (g_whales: internal transfers in whale pools) |
| F3 | Audit tape pre-trade; null `tx_index` | measured | partially confirmed |
| F4 | KOL-follow at 2 s fails, 36/36 | measured | partially confirmed — more robust than claimed |
| F5 | Slot time not comparable across blocks | measured | consistent |
| F6 | Universe manufactured | measured | consistent with g_whales |
| F7 | Competition is retail automation; Fomo latency is an affiliate claim | inferred | — |
| F8 | Inventory-into-BOOST untested | inferred | partly tested by d15 S3 (750-slot exit failed) |
| F9 | Late flow too small after k5 | measured | not verified |
| F10 | Withdrawn paper still cited | measured | — |
| F11 | Mayhem/BOOST flag missing from the selector | measured | — |

**d13 — data integrity**

| ID | Finding | Status | Verdict / correction |
|---|---|---|---|
| F1 | Brief wrong 3 ways | measured | partially confirmed — pre-trade confirmed broadly; non-WSOL rows 37–67%/hour |
| F2 | START placement mostly on exits | inferred | partially confirmed — **split wrong: at k6 the entry part is +0.50–0.72 pp of the +1.06 pp total** (verify `v4_stdout.txt`) |
| F3 | 26.6% of training rows from a gappy tape | measured | partially confirmed — gaps larger |
| F4 | NUL holes from the walker resume | measured | partially confirmed — **three holes**, not two |
| F5 | Slot duration; `SLOT_MS = 400` | measured | consistent |
| F6 | V map correct (good news) | measured | not verified |
| F7 | PumpSwap buy `sol_lamports` mostly fee-exclusive | measured | not verified |
| F8 | Ordering and duplicates clean | measured | not verified |
| F9 | Same-slot bundles drive part of the fill lift | measured | consistent with d16-F1 |
| F10 | Survivorship at block edges | measured | — |

**d14 — regime and universe**

| ID | Finding | Status | Verdict / correction |
|---|---|---|---|
| F1 | Gate trade CI ~1.7× too narrow | measured | partially confirmed — **~1.6×**, design effect 2.70 / 2.72 |
| F2 | No universe positive; k2 least bad | measured | partially confirmed — ranking holds; magnitude biased negative; k2 unreachable |
| F3 | Late entrants worse; no net retail inflow | measured | partially confirmed — relative claim holds; slot-gradient label and mechanism wording wrong |
| F4 | Day regime real, unpredictable | measured | partially confirmed — robust |
| F5 | 08-21 risk-on day; a second regime week | measured | not verified |
| F6 | Target moving | measured | not verified |
| F7 | Two-thirds of `complete` events not tradable | measured | consistent |
| F8 | Pre-trade reserves | measured | consistent |
| F9 | Overfit and operating-point gap | measured | consistent with d09-F3 [V] |
| F10 | Drained pools implausible / decode issue | speculative | **REFUTED** by g_whales g-F1 and g_early: real WSOL, one operator |
| F11 | Weekend / early-UTC weakly better | inferred | — |

**d15 — alternative strategies**

| ID | Finding | Status | Verdict / correction |
|---|---|---|---|
| F1 | Brief's reserve convention backwards | measured | **confirmed** |
| F2 | Retail-lag thesis not visible | measured | partially confirmed — price-path half holds |
| F3 | Four families fail | measured | partially confirmed — S2, S3, S4 decisively dead; S1 narrower |
| F4 | Crash continuation usable as an overlay | measured | partially confirmed — overstated as an exit overlay |
| F5–F10 | Top-of-curve binary; LP negative; mayhem; high-mcap breakouts; arbitrage dead; DuckDB temp corruption | measured / inferred | not verified |

**d16 — research ledger and code**

| ID | Finding | Status | Verdict / correction |
|---|---|---|---|
| F1 | 60–75% junk universe; fill lift is a junk filter | measured | partially confirmed — 59.5–70.2% |
| F2 | Real graduations lose at every k; selection −0.428 mSOL | measured | partially confirmed — k=4/6/8 only; k6 −1.050 [−1.362, −0.759], 8/27 days |
| F3 | Priority lever closed without a test | measured | partially confirmed — fixed-lamport arithmetic; story overstated |
| F4 | Re-injection optimistic in tiny pools | measured | partially confirmed — negligible at ≤ 1% of vault+V |
| F5 | First-buyer window ~42% | measured | not verified; g_early: the first slots are bundle-owned or contested by a migrate+buy bot |
| F6 | EXP-009 direction refuted | measured | not verified |
| F7–F12 | Fail models; stale statements; 32-min truncation; checks; dry-run tools without V; aged graduations | measured | not verified |

**g, xcheck and v reports [U]** — no separate skeptic pass.

| Report | Findings | Notes |
|---|---|---|
| g_reachable_cap_book_rescore | G-F1 capped book not positive in all blocks (fast-pool negative in every variant); G-F2 EXP-012 lift +3.7 pp only under the cap, sign flips in 2/5; G-F3 cap = BOOST-end avoidance; G-F4 seed guard +1.05 pp, not lookahead; G-F5 START worth more than speed; G-F6 weakness in different strata per block; G-F7 no-create mints (post-hoc, decays) | Pre-declared cells (`prereg.json` 07:12:38Z). Reproduces d07's cell to ≤ 0.05 pp per block. P1 OOF picks are not out-of-sample |
| g_early_landing_reachability | G1 k verified on chain; G2 first slots bundle- or reactor-owned; G3 DEC-022 stream blind to v1 txs; G4 6 slots earlier ≈ 1 pp at 0.5 SOL; G5 migrate permissionless, already contested; G6 synthetic migration; G7 organic-age hypothesis (best of ~2,100 cells); G8 MAL overpays priority; G9 superseded by g_october F5 | Public RPC only; seal respected |
| g_october_structure_check (+ invB) | F1 upgrade 10-02T15:47Z; F2 fee-keeping V bound; F3 BOOST on; F4 BOOST PDA identity; F5 200 ms at epoch 1053; F6 fees unchanged; F7 mix non-stationary; F8 synthetic migration hides whales; F9 weekly upgrades, no monitor | October sample n = 10 (invB), aggregated by A without re-verification |
| g_migration_slot_whales_and_farming | g-F1 real WSOL; g-F2 one operator per mint; g-F3 MAL-size early wallets ≈ break-even; g-F4 "holders win" internal in whale pools; g-F5 class mix explains first-print contradictions; g-F6 receiver-wallet pilot (n 57); g-F7 X wave after minute 1; g-F8 fee legs to the creator vault; g-F9 transfers not on tape | Refutes d14-F10 |
| g_sim_live_calibration_rerun | F1 corrected live − sim +0.84%; F2 tp +4.9 / sl −2.1 pp; F3 probe lost on fees; F4 exit lag mostly crossing-slot remainder; F5 patched tool verified 60/60; F6 phantom profits in the unpatched tool; F7 1,346,200 = volume accumulator; F8 no hidden cash line | F1/F2 are transfer estimates until the manager's exact re-run (E1) |
| g_app_attributed_retail_timing | G-F1 FOMO minutes late; G-F2 k3–5 cohorts lose; G-F3 early app flow does not predict; G-F4 Axiom veto regime-shifted; G-F5 FOMO buys the dump; G-F6 top-level scans miss app flow; G-F7 app tags free; G-F8 push latency unmeasured | Pre-declared; one-shot validation read; 21 h sample |
| xcheck_reserves | Pre-trade truth; d08 s9/s17 voided; d02 calibration tool wrong; d06 KOL k=1 artefacts; minor re-runs immaterial | Itself a verification pass |
| v_per_trade | PARTLY CONFIRMED: V moves per v2 trade since 09-30; V0 law holds; small today; main risk is (B) §5 NOT_DECIDABLE | Structure-only RPC |

### 11.2 Experiment cell counts (multiplicity)

Everything below is exploration on dates already read many times, or descriptive structure. **No cell in this audit is gate evidence.** Lab context: DEC-014 m = 12 (p < 0.00208); the 27 non-P1 dates have had 100+ tries; `data/tries.jsonl` has 121 lines since 10-02.

| Source | Cells / looks (as reported) |
|---|---|
| d01 | Forward horizons 5 × 11 k × 2 sets × 6 stats; truncation N 5 values; coverage c 4 values + inverse (descriptive) |
| d02 | A1 3 groups × ~20 stats; A2 5 books × 2 legs × 3 datasets + 6 lift CIs; A3 560 cells; A5 12; T2 6 × 4 × 2 caps × 6 flags |
| d03 | Replay 36; impact decay 6 × 2 × 2; sandwich 3 × 6 × 4; 4 priority policies; owner grid 3 × 5 × 4; caps 4; per-date 28 |
| d04 | v1 grid 3,432; v2 discovery 3,432 (3,168 tradeable); v2 validation 3,432; recent 3,432; TP/SL 144; human-flow 240; intra-slot 63; influencer 312 (+312 fresh-only sub-read) |
| d05 | Base grid 4,032 × 2 fee scenarios; screen 1,008 × 4 blocks; capped 432; events 672; EXP-012 comparisons 414; copy-sniper 72; day rules 3 |
| d06 | sim2 1,680 (+1,680 superseded, invalid pricing); KOL_sel 1 rule + 30 validation + 36 size cells; one-shot 1 + 6 + 2 |
| d07 | Discovery 90 × 2 k × 3 L × 2 sizes × 3 filters; END 90 × 2 × 2 × 2 × 2; surface 193 × 2 sizes × 3 blocks; validation and late re-runs of all 90; 1 pre-declared confirmatory read |
| d08 | 9 models + 1 reference; 57 single-feature AUCs; 12 path-label cells; 6 decile splits; 4 + 4 + 1 overlay books; 3 P1 checks; segment splits (3 history groups, mayhem × 6, 6 vault buckets) |
| d09 | 2 templates × 7 μ × 12 designs (+ IID 2 × 12), 400–600 sims each; pipelines 5 gains × 2 ρ × with/without B6; ~20 lottery variants |
| d10 | Harness 12 cells; PR classification |
| d11 | EXP-020 re-cut (3 regimes + 3 blocks + P1 + 08-21 split + 4 ttm buckets) × 2 legs; unconditional 9 × 5 × 4; cohorts 10 × 3; stop gap 6 lags |
| d12 | Drift 14 × 2; whale splits 4 × 4 × 2; BOOST front-run 24 (+16 superseded); cliff 5 × 3; KOL 36; cohorts 11 × 3 |
| d13 | k 0–10 × 2 blocks; k 5 values × 2 groups × 2 blocks; 4 cells split at 08-21T07 |
| d14 | 77 regime cells (66 OOS); ≥ 20 cells per universe-slice family; phase sims |
| d15 | 24 primary cells × 2 runs (pre/post fix) + 72 sensitivity rows; best-cell validation reads pre- and post-fix; high-tier slice × 2; S4 robustness 3 × 3; MIG × mayhem 10 |
| d16 | p06 11 × 3; p10b 337 rows; p15 16; p08/p08b 2 × 5 × 5 × 5 (descriptive) |
| g_reachable | 12 pre-declared cells × 4 legs × 5 blocks (432 rows); 4 bridge cells; 3 strata families; EXP-012 lift on 6 more cells; pick book 7 × 3 legs; 1 post-hoc stratum |
| g_early | ~2,100 cells (g09 576, g10 ≤ 72, g11 1,260, g13 216) |
| g_whales | Cohort tables (class × group × 2 windows); 30 MAL-size cells; 18 activity cells; insider-sell 15; X cohort 18; entity net 6; pilot 6 tests |
| g_app | Primary 4 reads; exploratory 224 (112 clean); addendum 3 frozen + books; §D 24 rows; post-hoc X3 8; k cells 22 + 12; cohorts 72; cascade 28 |
| g_sim_live | H1 7,034 trip-runs; A2 18; A3 9; A5 7 caps; A6 2 |
| g_october / v_per_trade | Structure only; no outcome cells (one pending-fee bound, 2 runs) |
| xcheck | Re-runs of d08 s9 / s17 (3 modes) / s13 / s16, d09 s05, d10 harness, d02-F1 mini-sim, d04 v04, d06 p3kol |
| **This synthesis [S]** | Power and kill simulations on the CAP-PICK pick book (P2–P4, 28 day-units, 2,340 attempts):<br>• 5 true-edge values × 2 one-shot windows × 3 rules;<br>• 6 forward designs under the CLAUDE gate;<br>• design G (7/14/21 with day-7 futility);<br>• 3 designs under the DEC-021 bar;<br>• October rules 2 windows × 2 rules;<br>• trial drawdowns 3 stakes × 4 edges × 6 caps;<br>• exit-mix tabulation.<br>One superseded first pass (a two-stage bootstrap that double-counted within-day noise) is not cited. Scripts and outputs: `/data/mal/audit-1008/work/synthesis_power/` |

### 11.3 Key synthesis computations [S]

All on the P2–P4 EXP-012 pick book of g_reachable's primary cell:
- equal-day flat mean 3.468%, pressure 2.888%, live 4.016% per attempt;
- residual SD 34.47%; SD of day means 5.24%; between-day SD 3.09%.

The true edge μ is the flat-leg mean per attempt (% of stake); the pressure leg is ~0.58 pp lower.

**Exit mix** (picks, P2–P4): tp 44.7% (mean +45.55% of stake), sl 37.6% (−45.52%), deadline 17.7% (+7.50%).

**One-shot sealed read, P(kill):**
- soft rule (flat mean ≤ 0): 6 days → 49% / 31% / 17% / 7% / 3% at μ = 0 / 1 / 1.9 / 2.8 / 3.8;
- hard rule (either leg ≤ 0 or ex-best-day ≤ 0): 74% / 55% / 38% / 20% / 10%;
- 12 days: soft 50% / 24% / 8% / 2% / 0.3%; hard 73% / 43% / 19% / 6% / 1.6%.

**Forward reads, P(pass):**

| Design | μ = 0 | 1.0 | 1.9 | 2.8 | 3.8 |
|---|---:|---:|---:|---:|---:|
| CLAUDE gate, fixed 7 d | 5.1% | 16% | 34% | 57% | 78% |
| CLAUDE gate, fixed 14 d | 3.5% | 19% | 47% | 77% | 94% |
| Fastest plan (7 strict + futility + 14) | 4.1% | 20% | 47% | 77% | 94% |
| G (futility 7 + early efficacy + 14 + 21) | 5.3% | 26% | 61% | 88% | 98% |
| DEC-021 bar, single 14 d | 2.3% | 13% | 38% | 69% | 92% |
| DEC-021 bar, single 21 d | 1.6% | 16% | 49% | 85% | 98% |
| **Recommended H3 (7 / 14 / 21 at p 0.005 / 0.008 / 0.012 + gate)** | **2.2%** | **15%** | **45%** | **79%** | **96%** |

**October 10-day report-only check, P(kill):**
- evidence plan's rule: 49% / 25% / 10% / 3% / 0.5%;
- fastest plan's rule: 14% / 5% / 2% / 0.5% / 0%.

**Live trial over 14 days, P(drawdown ≥ cap)**, by stake (daily P&L SD):

| Stake (daily SD) | Cap | μ = 0 | μ = +1.9% | μ = +3.8% |
|---|---|---:|---:|---:|
| 0.05 SOL (0.24 SOL) | −0.5 SOL | 53% | 15% | 3% |
| 0.1 SOL (0.49 SOL) | −1.0 SOL | 52% | 15% | 3% |
| 0.25 SOL (1.22 SOL) | −0.5 SOL | 79% | 48% | 21% |
| 0.25 SOL | −0.75 SOL | 75% | 41% | 16% |
| 0.25 SOL | −1.0 SOL | 71% | 36% | 12% |
| 0.25 SOL | −3.0 SOL | 42% | 10% | 1% |

Expected SOL/day at μ = +1.9%: 0.078 / 0.159 / 0.411 at 0.05 / 0.1 / 0.25 SOL.

**Caveats:**
- Exploration-derived. Day effects are shrunk to the method-of-moments between-day SD, and residuals are pooled, so within-day heteroskedasticity is lost.
- The trade CI uses the normal approximation (validated against the bootstrap in d09 s02).
- October dispersion is unknown.
