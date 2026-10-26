# P36 venue census: "go where the bots aren't" (2026-10-26)

Lane P36, manager17. Public sources only: web, public RPC (`api.mainnet-beta.solana.com`), GeckoTerminal and DefiLlama public APIs. 0 Helius credits, no keys, no MAL tape. **1,145 public RPC calls** (ledger `data/p36_calls.jsonl`, retries included), under the ~1,500 cap. **Counts only. No edge claim. Nothing here says a venue or a book makes money. No book has cleared the promotion gate.**

Written 2026-10-26 ~17:45Z (`date -u`). The jobs were MiScusi #1649 (pump.fun plus PumpSwap sample), #1650 (LaunchLab public-logs capture, cancelled after 19.4 min once it had enough creates) and #1655 (LaunchLab sample). The meme.fun sample, the activity checks and the GeckoTerminal sweep ran inline.

## 0. Bottom line

* **No undiscovered venue has breadth.** Every launch venue outside pump.fun, Meteora DBC and Raydium LaunchLab is either dead in October 2026 (Heaven, Boop, Moonit, Printr V2, Clanker-Solana, Wavebreak, Token Mill, daos.fun, Zora: 0 to ~$5k/24h) or not a launch venue (Hadron is an oracle-priced prop AMM; MetaDAO runs futarchy raises in USDC).
* **One brand-new venue, meme.fun** (program `MEME4gfGe6itEpxgM1xaBVmiFF5XuCTc4oVXUE5w56F`). Its first non-test pools appear on 2026-10-26 from 14:18Z (GeckoTerminal `pool_created_at`); test pools named `E2E…` came earlier. In 9 of 9 sampled launches there was **0 non-launcher buyer in slots 0-1 and 0 failed txs** in the first 30 s. Breadth is tiny: 10 non-test pools and $173,232 of h24 volume on the listed pools. It is a day-0 venue. Some pools look seeded by one operator (three pools show an identical $1,977 h24 volume).
* **Where bots are, measured.** Share of the first-30 s buy volume taken in slots 0-1, excluding the launch tx's own signer (pooled, quote units):
  * PumpSwap at migration, 2026-09-20: **56.5%**.
  * LaunchLab LAUNCH-quoted curves: **66.6%**.
  * LaunchLab STONK-quoted curves: 100% (n=2).
  * pump.fun random launches today: **6.0%**. These are mostly dead coins, yet the windows carry 8,386 failed txs against 1,760 successful ones.
  * LaunchLab SOL-quoted curves: **1.28%**.
  * meme.fun: **0.0%**.
* **Ranking** (breadth × weak competition × cost; §4):
  1. Meteora DBC, front-end split. Already recorded by #1453. The upgrade is front-end attribution, and only after its seal ends.
  2. meme.fun. A new, cheap watch recorder.
  3. LaunchLab SOL-quoted curves: weak early competition, but MAL already FAILED 12 rules there.
  4. pump.fun.
  5. LaunchLab non-SOL quotes.
  6. PumpSwap.

  The rest are dead.

## 1. What MAL already knew (docs/library/ideas.jsonl, grep venue / LaunchLab / Moonit / Heaven / Boop / census / DBC)

* **LaunchLab: FAILED. Not redone here.** Block A (2026-10-07..10-16) ran 12 cells, and every one failed: `ll-early-momentum`, `ll-milestone`, `ll-bigbuy`, `ll-cascade-fade`, `ll-grad-floor`. The revive trigger in each row is "quote mint on the tape (separates SOL pools) or a cheaper fee tier; a different rule class". Only ~7% of graduations are SOL-quoted (`ll-grad-floor`).
* **Meteora DBC.**
  * `dbc-early-momentum` m10 is explored-survivor-unconfirmed. Its forward read is ≥ 2026-11-01T00:10Z.
  * D2–D8 all FAIL.
  * Round trip measured at 110–400 bps (`dbc-swap3-rescreen`) and 154–195 bps at young pools (`dbc-bigbuy`).
  * `p25-redeploy-holiday` (E1 [10-14, 10-17)): new pools/day 3,642 / 2,842 / 3,088. The median first-5-s signer count is an integer 1 or 2.
* **Heaven / Boop / Moonit.**
  * `census-heaven-boop-moonit` is parked (P13: 0 notices in 10 min; ids unverified).
  * `web-cross-venue-6`: Moonit is under maintenance.
  * `web-cross-venue-5`: Heaven is desk-read.
* **Desk rows on other venues.**
  * `web-cross-venue-4`: Believe and Bags on DBC.
  * `launchpad-landscape-3`: DBC front ends.
  * `launchpad-landscape-4`: sniper latency per venue, untested. This census is the first measurement of it.
* **pump mechanics already in the library.**
  * custom pairs (`p25-quote-drift-completion`, `hunt23-w3-qsink-1`)
  * the 10-17 app platform fee (`p25-app-fee-retail-tag`)
  * Pump Bridge (`p25-pump-bridge-lead`)
  * PreStock quote legs (`p25-prestock-quote-band`)
  * StonkFun buyback flow (`web-cross-venue-1`, `mech-buyer-scout-2/3`)
* **P13 census (`hunt29/p13/CENSUS.md`).** It ranked LaunchLab→CPMM and DBC→DAMM v2 graduation pools first and said "do not record Heaven, Boop, Moonit". This census confirms that last point with fresh rates (§2).

## 2. Venue list (October 2026)

Sources for volume:
* **DefiLlama** `api.llama.fi/overview/dexs/solana`, fetched 2026-10-26 ~16:59Z (`data/defillama_solana_subset.json`). I quote `total24h` and `total30d`; the 30-day window is ~09-26..10-26.
* **GeckoTerminal ("GT")**: the sum of h24 volume over the listed pools (top ≤200 by volume), fetched 17:19Z (`data/gt_aggregate.json`). GT and DefiLlama disagree. GT PumpSwap $960,385,054 against DefiLlama $310,941,996. Treat both as order-of-magnitude figures.

Sources for launches/day:
* **pump.fun**: the create-only mint-authority PDA `TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM` (checked: its txs are `CreateV2`). Its newest 100 signatures span 141 s with 96 successful, which is ~58,800/day from a single 141-s window at ~17:01Z.
* **LaunchLab**: public `logsSubscribe` for 19.4 min (5,493 slots) gave 34 successful `PoolCreateEvent`s, so ≥ ~2,520/day. This is a lower bound, because public logs drop notices.
* **Bitquery** ([solana-launchpad-year](https://bitquery.io/investigations/solana-launchpad-year), data 2025-11-01..2026-10-07, verified 10-10):
  * all Solana new tokens ≈ 1.28M in Sep 2026;
  * pump.fun share 90% in Aug, falling back in Sep;
  * LaunchLab 153,813 tokens in Sep, mostly StonkFun, with only ~1 in 9 SOL-paired;
  * DBC ~5% of new tokens since Mar 2026, ~75–82% graduate, and 35% (Sep: 42%) graduate **on a single trade**.

Fees per side are taken from on-chain event fields where I saw them, and cited otherwise.

| Venue | Program id(s) | Launches/day (Oct) | Volume/day | Fee per side | Status |
|---|---|---|---|---|---|
| pump.fun bonding curve | `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` | ~58,800 (141-s sample); Sep 17: 38,526 (MemeFees via Wikipedia) | DefiLlama $101,107,991 (24h), $4,798,677,683 (30d) | 95 bps protocol + 30 bps creator (TradeEvent fields, 10-26); plus a 45 bps pump app platform fee for wallets without referral from 10-17 ([KuCoin 10-22](https://www.kucoin.com/news/flash/meme-coins-still-matter-7-assets-and-launchpads-to-watch-in-october-2026)) | dominant; custom pairs (quote = PUMP, SPCX, ANTHRP PreStocks, …) since ~10-09; 4 of 20 sampled launches were non-SOL quoted |
| PumpSwap | `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA` | graduations only (migrator `39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg` signed 20 txs in 607 s; 14 of the 20 sampled Sep migrator txs created a pool) | DefiLlama $310,941,996 (24h), $9,749,047,826 (30d) | market-cap tiered; one large pool showed 5 bps protocol, 0 LP, 0 creator (event fields); MAL round trip ≈ 2.4% at its entry sizes (P36 brief) | MAL's main venue |
| Meteora DBC (front ends: Bags, Believe, Jupiter Studio, Scribe, Star.fun, America.fun, …) | `dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN` | MAL E1: 3,642 / 2,842 / 3,088 new pools/day (10-14/15/16) | DefiLlama $7,493,591 (24h), $444,821,948 (30d); GT bags-fm $218,340 (160 pools) | per config; MAL round trip 110–400 bps (154–195 at young pools) | recorded by #1453 (ix-only); **forward window sealed** |
| Raydium LaunchLab (LetsBonk/bonk.fun, StonkFun, LAUNCH, xStock-quoted configs) | `LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj` | ≥ ~2,520 (logs, lower bound); Sep ≈ 5,100/day (Bitquery 153,813) | DefiLlama LaunchLab $8,610,345 (24h), $204,967,490 (30d); StonkFun (listed separately, may overlap) $9,105,178 (24h), $321,884,936 (30d) | 25 bps protocol + ~101 bps platform + creator per config (TradeEvent fields, one sell); StonkFun platform fee 1% ([DefiLlama/datawallet](https://www.datawallet.com/crypto/stonk-fun-explained)) | recorded by #1453 (full TradeEvent); **FAILED** for 12 SOL rules |
| meme.fun (NEW, first real pools 2026-10-26 14:18Z) | `MEME4gfGe6itEpxgM1xaBVmiFF5XuCTc4oVXUE5w56F` (owner of GT pool `GGHQ6Q…`) | 10 non-test pools on GT in the first ~2 h; the program had 1,000 txs in 2.55 h | GT $173,232 (20 pools) | not measured | no recorder; layout unknown |
| Jupiter Studio | DBC (front end) | not separable without config attribution | in DBC | anti-sniper fee decaying ~99% → ~1% over a random 15–60 s (single secondary source: [Smithii](https://smithii.io/?p=44934)); MadeOnSol says the same | sealed with DBC |
| Scribe (NEW 2026-09-15) | DBC (front end); Token-2022 mints with on-chain media | 344 tokens by 09-24 ([Forbes](https://www.forbes.com/sites/boazsobrado/2026/09/24/solana-made-room-for-bigger-files-memecoins-got-there-first/)) | in DBC | per config | creates the mint, the DBC pool **and the platform's own opening buy in one v1 tx** ([Bitrue](https://www.bitrue.com/blog/what-is-scribe-crypto)) |
| Bags / Believe | DBC (front ends) | not separable | GT bags-fm $218,340 | Bags: 1% to creator (third-party listings) | sealed with DBC |
| Heaven | `HEAVENoP2qxoeuF8Dj2oT1GHEnu49U5mJYkdeC8BAX2o` | 0 (1,000 txs span 33.7 days ≈ 30 txs/day, newest 2.3 h old, recent ones are `Sell`) | DefiLlama $0 (24h), $1,048.74 (30d); GT 0 pools | 1% under $100k mcap (P13 desk) | dead |
| Boop | `boop8hVGQGqehUK2iVEMEnMrL5RbjywRzHKBmBE7ry4` | 0 (1,000 txs span 112.7 days ≈ 9/day) | DefiLlama $0 (24h), $3,658.07 (30d); GT $1 | — | dead |
| Moonit | `MoonCVVNZFSYkqNXP6bxHLPL6QQJiMagDL3qcqUQTrG` | 0 creates seen (1,000 txs span 6.1 days ≈ 163/day; recent ones are Jupiter `RouteV2`+`Buy`) | GT $164 (20 pools); moonshot.money app $157,828 (24h) | 1% (P13 desk) | dead as a launch venue |
| Hadron | `HADRoNbLovyqhCsocfYQYB7QdfCAAinN9HTePvBCVDQ8` | not a launchpad | GT $2,855,530 (40 pools; top LAUNCH/SOL) | operator-set curve | oracle-priced prop AMM ([blockspot](https://blockspot.io/exchange/hadron/)); not a launch venue |
| Printr V2, Clanker (Solana), Zora, Wavebreak, Token Mill, daos.fun, Virtuals (Solana), EasyA Kickstart | not resolved (no activity) | 0 | GT $0–$10 each; Zora 0 Solana pools (its Solana product is Feb-2026 Attention Markets, [boerse-express](https://www.boerse-express.com/news/articles/solana-zora-springt-ab-867883)) | — | dead in Oct |
| Rise.rich, Bordrless, sendfun, others (DefiLlama "Launchpad") | not resolved | — | DefiLlama Rise.rich $9,087 (24h); others ≤ $41 | — | negligible |

**New mechanics, Sep–Oct 2026** (MAL already knows most of them; see §1):
* tx version 1 / larger txs (Scribe, 09-15; pump `CreateV2` txs are now version 1, and `getTransaction` needs `maxSupportedTransactionVersion: 1`);
* pump custom pairs (~10-09);
* pump v3 instructions and multi-hop (10-08);
* DBC Swap3/EvtSwap3 (10-16);
* pump app 45 bps fee (10-17);
* Pump Bridge (10-22);
* StonkFun routed through LaunchLab since 09-05 ([crowdfundinsider](https://www.crowdfundinsider.com/2026/09/307273-stonk-token-surges-after-stonkfun-routes-new-listings-through-raydium-and-jupiter/));
* LAUNCH-quoted LaunchLab curves (49 of the top-200 LaunchLab pools on GT are LAUNCH-quoted; quote mint `AhBidGAgv6QbKSWfrSCegGrzFLcBx4E5FNYTKHNQQj6C`);
* meme.fun (10-26).

Measured slot time: 0.2119 s on 10-26 (`getRecentPerformanceSamples`) against 0.2663 s on 09-20 (block times).

## 3. Bot competition (first 30 s after the launch tx)

**Method** (`p36_hist.py`):
* For each launch, list the follow address's signatures (mint for pump.fun, pool otherwise) between the launch tx (`until`) and the first block past the window (`before`). Keep successful txs with `blockTime − t0 ≤ 30 s`.
* `getTransaction` covers every slot-0-1 tx (cap 30, none hit) and a seeded sample of ≤12 later txs, which are weighted.
* Buys are decoded from logs:
  * pump.fun / PumpSwap: `observe.trade_decode` via `tools.arena_feed.rows_from_notice`; buyer = the event's trader.
  * LaunchLab: `tools.venue_tape.decode_ll_event`; buyer = fee payer.
  * meme.fun: no decoder. Buyer = fee payer whose base-mint balance rises; amount = lamport drop minus the tx fee. This amount includes ATA rent (~0.002 SOL), which is a disclosed bias.
* "Ex-launcher" drops the launch tx's signer and the event creator.
* Slot 0 is the launch slot.
* Shares are in quote units and are pooled only within one quote.

| Venue (sample) | n | Pooled share of 30-s buy volume in slots 0-1, ex-launcher | Launches with ≥1 other buyer in slots 0-1 | Other wallets in slots 0-1: median / mean | Median first other buy (slots) | Successful / failed txs in the 30-s windows |
|---|---|---|---|---|---|---|
| pump.fun, 20 random creates, 10-26 16:54:55–17:01:53Z (10–50 min old at fetch) | 20 | 0.060 | 5 / 20 | 0 / 1.4 | 7 (13 launches with any other buy) | 1,760 / 8,386 |
| PumpSwap migrations, 2026-09-20 09:36–11:58Z | 14 (6 migrator txs had no pool create) | 0.565 | 6 / 14 | 0 / 2.43 | 3 | 2,998 / 3,271 |
| LaunchLab SOL-quoted (config `6s1xP3…`), 10-26 17:12–17:29Z (all LaunchLab rows) | 7 | 0.0128 | 4 / 7 (tiny buys) | 1 / 0.86 | — (0,1,1,1,7,14) | 770 / 2,880 |
| LaunchLab LAUNCH-quoted (config `6h2iyA…`) | 5 | 0.6664 | 4 / 5 | 2 / 1.6 | — (0,0,1,1,9) | 18 / 615 |
| LaunchLab STONK-quoted (StonkFun, config `4Rb2jo…`) | 2 | 1.0 (one pool with buys) | 1 / 2 | 2.5 / 2.5 | 0 | 33 / 464 |
| LaunchLab other quotes (xStocks, …) | 4 | n/a (2 pools with any buy) | 1 / 4 | 0 / 0.25 | — | 5 / 0 |
| meme.fun, 10-26 14:18–15:59Z (GT list) | 9 | 0.0 | 0 / 9 | 0 / 0 | 16 | 85 / 0 |
| Meteora DBC (all front ends) | — | **not measured**: the only live sample falls in the sealed forward window [10-25T00Z, 11-01T00:10Z), and [10-17, 10-24) is spent for counts. A pre-10-14 sample needs a create list that public RPC cannot give cheaply (no create-only account found) | | | | MAL E1 (10-14..16): median first-5-s signers 1 or 2; Bitquery: 35–42% of DBC graduations happen on one trade |
| Heaven / Boop / Moonit | — | no launches to sample | | | | |

**Reading (counts only).**
* Competition sits at the **graduation and migration event** (PumpSwap 56.5%) and on **non-SOL-quoted LaunchLab curves**. Those are platform-style launches, where the launcher's own bundle wallets probably count as "other" wallets. On random pump.fun and SOL-quoted LaunchLab creates, competition shows mostly as **failed-tx spam**: 83% of the pump windows' txs failed, and 79% of the SOL LaunchLab windows' txs failed. Neither shows as slot-0-1 volume capture. That is consistent with snipers hitting a few coins hard and ignoring the rest.
* meme.fun is the only venue with neither slot-0-1 buys nor failed txs.
* Small n everywhere: 20 / 14 / 18 / 9 launches, one afternoon each. One pump launch hit the 4,000-signature page cap (`sigs_truncated` 1), so its earliest txs may be missing.

## 4. Ranking (breadth × weak competition × cost)

Judgement on the counts above. This is not a model.

| Rank | Venue | Breadth | Competition (this census) | Cost | Note |
|---|---|---|---|---|---|
| 1 | **Meteora DBC, by front end** | ~2.8–3.6k pools/day (MAL), $7.5M/24h | not measured fresh (seal). MAL E1: 1–2 signers in the first 5 s. Jupiter Studio's anti-sniper fee and Scribe's in-tx platform buy should change who competes by front end | 110–400 bps RT | already MAL's one breadth line (m10, 170–200 attempts/day). The unknown is **which front ends** hold the weak-competition flow |
| 2 | **meme.fun** | day 0: ~10 launches in 2 h | weakest seen: 0 / 9 slot-0-1 other buyers, 0 failed txs | unknown | cheap to watch; the value is being first if it grows |
| 3 | LaunchLab SOL-quoted | ~1/3 of LaunchLab creates in this sample (7 of 18) | weak: 1.28% pooled | ~125 bps + creator per side | FAILED (12 cells); stays closed |
| 4 | pump.fun bonding | ~38–59k/day | weak on volume (6.0%), heavy failed-tx spam | 125 bps (+45 bps app) | MAL: every event-reaction edge dies at ~4.4 s |
| 5 | LaunchLab LAUNCH / STONK | most LaunchLab volume ($9.1M StonkFun) | strong (66.6% / 100%) | ≥125 bps + platform | bots are already there |
| 6 | PumpSwap at migration | ~1–3k/day | strongest (56.5%) | tiered | MAL's existing books |
| — | Heaven, Boop, Moonit, Printr V2, Clanker-Sol, Zora, Wavebreak, Token Mill, daos.fun | 0 | — | — | dead; recheck monthly (P13 rule kept) |

## 5. Recorder proposals (top 2)

### R1. DBC front-end attribution: an upgrade of #1453, not a new socket. Start only at or after 2026-11-01T00:10Z.

* **Subscription.** Keep the current `meteora_dbc` logsSubscribe on `dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN`. The logs carry instruction names only, with no accounts, so attribution needs one `getTransaction` per **create**: `InitializeVirtualPool*`, ~3k/day, about 3k public calls/day. That is affordable at the existing public rate, but it is a new RPC load next to arena_feed. Alternatives are 0 Helius under the owner's rule, or a daily batch.
* **Fields from the create tx.**
  * `config`, with config → partner/front end resolved once per config by `getAccountInfo` (fee_claimer / partner key). Hand-map the known front ends: Bags mint suffix `BAGS`, Jupiter Studio, Believe, Scribe (Token-2022 with on-chain media, platform opening buy in the create tx).
  * quote mint.
  * anti-sniper fee schedule (MAL already decodes config fee schedules, `p20-dbc-fee-floor-clock`).
* **Fit with #1453.**
  * Same writer (`tools/venue_tape.py`) and the same hourly JSONL. Add `create_detail` rows keyed by signature, so ix rows stay byte-identical.
  * This needs a runner change with an md5 decision-equivalence proof, because m10 reads this tape. Doing it inside the forward window would touch the sealed read's inputs. Hence the start date.
* **First counts-only screen** (pre-declared, no prices), on the first 7 full days after 11-01T00:10Z:
  * per front end: creates/day, the share completing on one trade, and median other-signers in slots 0-1 and in the first 5 s;
  * then a 20-launch/day public-RPC sample of slot-0-1 share (this census's `p36_hist.py`, a DBC buy decoder via the payer-balance method).
  * Kill rule: a front end goes to the "bots are here" bin if its pooled slot-0-1 ex-launcher share is ≥ 30% or its median other-signers in slots 0-1 is ≥ 2. Only front ends with ≥ 200 creates/day and share < 10% go forward as a sub-universe proposal. That needs QP, because it narrows m10's universe.

### R2. meme.fun watch recorder: a new venue in `venue_tape`, now.

* **Subscription.** Public `logsSubscribe` mentions on `MEME4gfGe6itEpxgM1xaBVmiFF5XuCTc4oVXUE5w56F` at ~390 txs/h (1,000 txs in 2.55 h). It shares the #1453 process as a third entry in `VENUES` with `silence_s` ~1800, so no new socket host is needed.
* **Rows.** It writes `ix` rows (instruction names, failed flag, depth), like DBC, until a layout is known. If the logs carry `Program data`, keep the base64 raw for an offline decoder (`event_undecoded`, already supported).
* **Fit with #1453.** One more subscription on the same socket pool. #1453's LaunchLab and DBC rows are unchanged; that needs a test that the existing venues' rows are byte-identical with a replayed notice set.
* **First counts-only screen**, days 1–7:
  * creates/day (identified by instruction name once seen; GT `pool_created_at` as a cross-check);
  * a daily `p36_hist.py --venue generic` sample of 20 launches (≤ ~200 public calls/day): slot-0-1 ex-launcher share, other wallets in slots 0-1, failed txs, first other buy slot;
  * the fee read from one buy (lamports in vs. pool vault delta);
  * the share of pools that look operator-seeded (identical volumes).
  * Pre-declared kill: drop the recorder if creates/day < 50 on 5 of the 7 days, or if pooled slot-0-1 ex-launcher share is ≥ 30% by day 7. Graduate to a decoder lane only if creates/day ≥ 200 and share < 10%.

Not proposed:
* LaunchLab quote split. A config → quote-mint map on #1453's create rows, from the LaunchLab revive trigger, is a ~10-line change. This census shows the non-SOL cohorts are the competitive ones, and the SOL cohort has already failed. So it is a cheap bookkeeping fix, not a venue bet. File it as an open item if the manager wants the ll-* revive trigger tested.
* Heaven, Boop and Moonit: no.

## 6. Seals, limits and what was not measured

* **Seal questions for the manager.**
  * (a) The GeckoTerminal sweep returned per-pool h24 rows for `meteora-dbc` and `bags-fm` that include pools created inside the sealed DBC forward window, and DefiLlama's DBC 24h/30d totals cover it too. I used venue-level aggregates only. I did not inspect DBC pool prices, returns or per-pool rows, and the per-pool rows are not in git (`data/gt_aggregate.json` holds aggregates only). Please rule whether venue-level DBC volume aggregates from the window count as contact.
  * (b) pump.fun bonding launches were sampled on 2026-10-26, an HV4 counted day. They are counts of first-30-s buys only, with no pricing and no HV4 output.
  * (c) PumpSwap was sampled only on 2026-09-20 (Fast pre-cut block, exploration pool), never in October, because of the EXP-024 Look-2 seal.
  * (d) No DBC tx, log or signature was fetched by this lane. No MAL tape was opened.
* **Not measured.**
  * DBC competition (seal).
  * Fees on meme.fun, Bags, Believe and Jupiter Studio (only secondary sources).
  * PumpSwap launches/day (migrator txs ≠ migrations).
  * Volume for venues with no DefiLlama row beyond GT's listed-pool sums.
* **Limits.** Every competition row is one short afternoon window with n ≤ 20. Public RPC was used, but the sample method uses `getSignaturesForAddress`/`getTransaction`, not logs, so it does not drop notices. One pump launch hit the page cap. The LaunchLab capture (public logs) is a lower bound for creates.
* Raw files on research-0: `/data2/mal/p36-venue-census/` (capture gz, logs). Committed: `data/` (samples, aggregates, call ledger) and the scripts.

## Files
* `p36_hist.py`: first-30 s sampler (pump, pumpswap, launchlab, generic).
* `p36_rpc.py`: public RPC client with a call ledger and budget.
* `p36_capture.py`: public logsSubscribe capture.
* `p36_ll_creates.py`: LaunchLab create extraction.
* `p36_summarize.py`: summary table.
* `gt_sweep.py`: GeckoTerminal sweep.
* `data/*.json`: outputs. `data/p36_calls.jsonl`: every RPC call.

Sources: [Bitquery launchpad year](https://bitquery.io/investigations/solana-launchpad-year), [DefiLlama API](https://api.llama.fi/overview/dexs/solana), [GeckoTerminal API](https://api.geckoterminal.com/api/v2), [Forbes on Scribe](https://www.forbes.com/sites/boazsobrado/2026/09/24/solana-made-room-for-bigger-files-memecoins-got-there-first/), [Bitrue on Scribe](https://www.bitrue.com/blog/what-is-scribe-crypto), [KuCoin Oct 2026](https://www.kucoin.com/news/flash/meme-coins-still-matter-7-assets-and-launchpads-to-watch-in-october-2026), [Datawallet StonkFun](https://www.datawallet.com/crypto/stonk-fun-explained), [Crowdfund Insider StonkFun](https://www.crowdfundinsider.com/2026/09/307273-stonk-token-surges-after-stonkfun-routes-new-listings-through-raydium-and-jupiter/), [Smithii Jupiter Studio](https://smithii.io/?p=44934), [Blockspot Hadron](https://blockspot.io/exchange/hadron/), [Wikipedia Pump.fun](https://en.wikipedia.org/wiki/Pump.fun), [boerse-express Zora](https://www.boerse-express.com/news/articles/solana-zora-springt-ab-867883).
