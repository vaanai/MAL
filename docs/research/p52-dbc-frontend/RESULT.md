# P52: Meteora DBC front-end attribution (P36 R1), exploration days only (2026-10-26)

Lane P52, manager17. Written 2026-10-26 ~22:15Z (`date -u`). **Counts only. No pricing, no P&L, no edge claim. Nothing here says a front end or a book makes money. No book has cleared the promotion gate.**

* **Data.** DBC tape rows only through the `tools.dbc_price` guard, exploration hours [2026-10-14T00, 2026-10-17T00) (72 of 72 hours, 0 incomplete). The tool never turns on the spent, holdout or forward switches, and it refuses to run if the guard is not in default mode. It read nothing from [10-17, 10-24), the forward window [10-25T00Z, 11-01T00:10Z) or fwdv1/. Job #1453 was not touched.
* **RPC.** Public `api.mainnet-beta` only, 0 Helius, no websockets. **1,256 calls** (ledger `data/p52_calls.jsonl`, every HTTP attempt counted): 1,233 `getTransaction` (1,000 create txs plus 233 rate-limit retries) and 23 `getMultipleAccounts` (403 config accounts missing from the P17 cache, plus PartnerMetadata PDAs for 1,756 partners). Each fetched tx was checked to have a block time inside the exploration window.
* **Config source.** P17's cached fetch `/data/mal/hunt-1008/push-1016/hunt28/p17/cfg_raw_all.json` (job #1504) gave 2,041 of the 2,444 configs; the other 403 were fetched today. Config and partner-metadata accounts are read in their current state, not their state at create time.
* **Job.** MiScusi #1692 (fetch then table, 37 min). Tool `tools/dbc_frontend_attrib.py`, tests `tools/test_dbc_frontend_attrib.py` (16, synthetic data only).

## 0. Bottom line

1. **No DBC front end reaches the R1 breadth bar (≥ 200 creates/day).** The largest named front end is Bags: 113.3/day over all quotes, 80.0/day SOL-quoted. The largest single partner (`CtNKS1…`) has 133.7/day. Under R1 as declared, **no front end is a candidate**.
2. **Most DBC "breadth" is not a launchpad.** 9,572 creates in 3 days. Grouped by partner behaviour (classes defined **after** I saw the first table; see §3):
   * 4,021 (42.0%) come from **"all-graduate fast"** partners: one Token-2022 config each, 25 bps flat fee, a median of 82 trades in the first 30 s, 100% graduate within 24 h, median completion 44–324 s.
   * 2,339 (24.4%) come from **"one-trade completer"** partners: ≥ 80% of their pools complete ≤ 5 s after the create, on one trade, and there are no outside buys.
   * 3,212 (33.6%) are **"launchpad-like"** (everything else).

   This matches Bitquery's "35–42% of DBC graduations happen on a single trade" (P36 §2).
3. **The real front ends we could name are thin and nearly bot-free in slots 0-1, but they also barely trade.**
   * Bags: 0 of 340 pools graduated within 24 h; only 61 of 240 SOL pools had any outside buy in 30 s.
   * Jupiter Studio (`static-create.jup.ag`): 29.3/day, 0 graduations.
   * Moonshot (`cdn.moonshot.com`, USDC): 26.7/day, 0 graduations.
4. **The bots are in the graduation farms.** 6 of the 20 largest unattributed partners are in the R1 drop bin. Five of them have slot-0-1 shares of 0.5772 to 0.8217 and median other buyers up to 2. The sixth, DwP6Zr, is there on a share of 1.0000 from a single pool.
5. **Filter implication (not a proposal).** A partner-list filter would be causal, since `config → fee_claimer` is known at create time. It would **narrow** m10's universe and needs QP. It cannot change the 11-01 read. I did not check which of these classes m10 actually trades (open item 1).

## 1. Method

* **Per create** (the tape `create` row: pool, config, creator, base mint, slot s0, block time t0):
  * Window: trades of that pool with `slot ≥ s0` and `block_time − t0 ≤ 30 s`.
  * Buy volume: `amount_in` of buy rows (quote atoms, fee included), pooled only within one quote mint.
  * **Ex-launcher**: drops every buy inside the create tx, every buy whose signer is the pool creator, and every buy by the create-tx payer when that tx was sampled. In all 1,000 sampled txs except Moonshot and a few small hosts, the payer equalled the creator.
  * `share01`: pooled ex-launcher buy volume in slots {s0, s0+1} divided by the pooled ex-launcher 30-s buy volume.
  * `others01`: distinct ex-launcher buy signers in slots 0-1.
  * Graduation: `curve_complete` within 24 h, over creates with t0 ≤ 10-16T00Z (all follow-up stays inside exploration).
  * Creates in the last 30 s of the window are left out of the window metrics (n_window column in the data).
* **Front-end label per partner (`fee_claimer`)**, in this precedence:
  1. the on-chain `PartnerMetadata` name (15 partners have one);
  2. the majority host of the metadata `uri` in the sampled create-ix params, when it is not a generic IPFS or Arweave gateway;
  3. the majority vanity mint suffix (BAGS, jups/jupx, pump, …), as a hint only;
  4. otherwise "unattributed partner <prefix>".
* **Sample.** Seeded (seed 1): 5 create txs from every partner with ≥ 20 creates (67 partners), then a uniform random fill up to 1,000 txs.
* **R1 screen**, quoted from P36 CENSUS §5 R1: a front end goes to the "bots are here" bin if its pooled slot-0-1 ex-launcher share is ≥ 30% or its median other-signers in slots 0-1 is ≥ 2. Only front ends with ≥ 200 creates/day and share < 10% go forward as a sub-universe proposal. I applied it per (front end, quote), with creates/day taken per quote.

## 2. Table: named and labelled front ends (≥ 10 creates)

Share and median are ex-launcher, first 30 s. "Buy in create tx" is the share of creates whose create tx also holds a buy (the platform or creator opening buy).

| Front end (label) | Quote | Creates (3 d) | /day (this quote) | /day (all quotes) | Pools with ext. buys | Share 30-s buy vol in slots 0-1 | Median other buyers slots 0-1 | Grad ≤24 h | Complete ≤5 s | Buy in create tx | Fee start→end bps (median) | R1 verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| mint suffix pump (4 partners; not a known front end) | SOL | 596 | 198.7 | 198.7 | 6 | 0.4550 | 0.0 | 0.9870 | 0.9849 | 0.0134 | 25→25 | drop (bots are here) |
| Bags (mint suffix BAGS; 340 one-launch partners) | SOL | 240 | 80.0 | 113.3 | 61 | 0.3038 | 0.0 | 0.0000 | 0.0000 | 0.9667 | 200→200 | drop (bots are here) |
| bingan (PartnerMetadata) | CWZ6Bs | 137 | 45.7 | 46.3 | 133 | 0.5498 | 1 | 0.0745 | 0.0000 | 0.9854 | 100→100 | drop (bots are here) |
| Bags | XsDoVf | 85 | 28.3 | 113.3 | 9 | 0.0000 | 0 | – | 0.0000 | 0.0000 | 200→200 | neither (keep watching) |
| uri:cdn.moonshot.com | USDC | 80 | 26.7 | 26.7 | 43 | 0.1291 | 0.0 | 0.0000 | 0.0000 | 1.0000 | 50→50 | neither (keep watching) |
| uri:hiie.arthurlabs.net | SOL | 50 | 16.7 | 16.7 | 35 | 0.0411 | 0.0 | 0.0476 | 0.0000 | 0.6200 | 313→313 | neither (keep watching) |
| uri:media.launchpad.capx.ai | 7AoBuY | 44 | 14.7 | 18.0 | 35 | 0.0000 | 0.0 | 0.2308 | 0.0000 | 1.0000 | 25→25 | neither (keep watching) |
| uri:whaletails.fun | SOL | 43 | 14.3 | 14.3 | 2 | 0.0000 | 0 | 0.0000 | 0.0000 | 0.0000 | 5000→100 | neither (keep watching) |
| uri:static-create.jup.ag (Jupiter Studio; 88 configs, one partner) | USDC | 36 | 12.0 | 29.3 | 11 | 0.0392 | 0.0 | 0.0000 | 0.0000 | 0.0000 | 200→200 | neither (keep watching) |
| uri:static-create.jup.ag | JUP | 33 | 11.0 | 29.3 | 17 | 0.0059 | 0 | 0.0000 | 0.0000 | 0.0000 | 200→200 | neither (keep watching) |
| uri:oneonly.lol | SOL | 30 | 10.0 | 10.0 | 9 | 0.5434 | 0.0 | 0.0000 | 0.0000 | 0.7667 | 125→125 | drop (bots are here) |
| uri:souvenir.finance | SOL | 28 | 9.3 | 9.3 | 13 | 0.0070 | 0.0 | 0.0000 | 0.0000 | 0.4643 | 300→300 | neither (keep watching) |
| uri:brown-left-cougar-435.mypinata.cloud | SOL | 26 | 8.7 | 8.7 | 14 | 0.3008 | 0.0 | 0.0500 | 0.0000 | 0.6154 | 200→200 | drop (bots are here) |
| uri:static-create.jup.ag | SOL | 19 | 6.3 | 29.3 | 9 | 0.0123 | 0 | 0.0000 | 0.0000 | 0.0000 | 200→200 | neither (keep watching) |
| uri:100kdaily.top | SOL | 16 | 5.3 | 5.3 | 16 | 0.0003 | 0.0 | 0.0000 | 0.0000 | 0.8750 | 25→25 | neither (keep watching) |
| uri:xstok.fun | SOL | 16 | 5.3 | 15.3 | 15 | 0.0425 | 1.0 | 0.0000 | 0.0000 | 0.6875 | 100→100 | neither (keep watching) |
| uri:spec-fun-production.up.railway.app | SOL | 16 | 5.3 | 5.3 | 0 | – | 0.0 | 0.0000 | 0.0000 | 0.0000 | 600→600 | no data |
| uri:rex.fun | CmQJNJ | 13 | 4.3 | 8.0 | 4 | 0.0000 | 0 | 0.0000 | 0.0000 | 0.6154 | 200→200 | neither (keep watching) |
| Bots Family (PartnerMetadata) | SOL | 12 | 4.0 | 4.0 | 6 | 0.3161 | 0.0 | 0.0000 | 0.0000 | 0.0000 | 100→100 | drop (bots are here) |
| uri:doubletap.fun | SOL | 11 | 3.7 | 3.7 | 5 | 0.1979 | 0 | 0.0000 | 0.0000 | 0.7273 | 5000→100 | neither (keep watching) |
| uri:violet-obedient-ferret-184.mypinata.cloud | SOL | 11 | 3.7 | 3.7 | 11 | 0.0037 | 2 | 0.0000 | 0.0000 | 1.0000 | 25→25 | drop (bots are here) |
| uri:ivory-adorable-octopus-471.mypinata.cloud | CTgiaZ | 10 | 3.3 | 3.3 | 5 | 0.0689 | 0.0 | 0.0000 | 0.0000 | 0.6000 | 313→313 | neither (keep watching) |
| uri:olive-cheerful-crayfish-253.mypinata.cloud | SOL | 10 | 3.3 | 3.3 | 10 | 0.0003 | 0.0 | 0.0000 | 0.0000 | 0.6000 | 25→25 | neither (keep watching) |
| uri:media.launchpad.capx.ai | SOL | 10 | 3.3 | 18.0 | 9 | 0.0012 | 0.5 | 0.0000 | 0.0000 | 0.6000 | 25→25 | neither (keep watching) |

**Front ends from the brief:**
* **Bags.** Found by the BAGS mint suffix: 340 one-launch partners, flat 200 bps, the creator's buy sits inside the create tx on 96.67% of SOL pools. SOL share 0.3038, which is in the drop bin by the share rule, from only 61 pools with any outside buy. Median other buyers 0.
* **Jupiter Studio.** Found by the `static-create.jup.ag` uri: one partner `E8QjCz3a…`, 88 configs, mints mostly suffixed jupx. Median fee 200→200 bps. 21.59% of its configs start at ≥ 5,000 bps, so the "anti-sniper fee" (single secondary source in P36) applies to a minority of its configs, not to all of them. 0 graduations.
* **Believe and Scribe.** **Not identified.** No PartnerMetadata, no uri host, no suffix in this sample. Scribe's signature in P36 (Token-2022 plus a platform buy in the create tx) does not match the large Token-2022 partners, whose buy-in-create share is 0.
* **Others with on-chain names** (Ripples, FarmFun, Prana, pims, Xstok, SkyTerminal, Anoncoin, …): all < 5 creates/day.
* **Fee-schedule anti-sniper configs.** Starts of 2,500–9,900 bps decaying to ~100–200 bps over median durations of 3–60 s appear only on small front ends (whaletails.fun, doubletap.fun, vaultbunker.tech, 1edge.app, Ripples, FarmFun, pims, Prana).

## 3. The large unattributed partners, and the post-hoc classes

The large unattributed partners (≥ 100 creates; each has 1 config except 8TPACX with 6 and 5cwmaY with 11). Payer = creator in every sampled tx, metadata on ipfs.io or pinata.

| Partner | Quote | Creates | /day | Creators | Pools with ext. buys | Share slots 0-1 | Median others 0-1 | Grad ≤24 h | Median completion s | Median trades in 30 s | Class | R1 verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CtNKS1 | SOL | 401 | 133.7 | 33 | 401 | 0.5935 | 1 | 1.0000 | 60.0 | 105 | all-graduate fast | drop (bots are here) |
| J7yBcg | SOL | 293 | 97.7 | 30 | 293 | 0.5772 | 2 | 1.0000 | 60.0 | 116 | all-graduate fast | drop (bots are here) |
| 8TPACX | SOL | 288 | 96.0 | 288 | 287 | 0.0001 | 0 | 1.0000 | 324.0 | 17 | all-graduate fast | neither (keep watching) |
| HyhosN | SOL | 244 | 81.3 | 226 | 210 | 0.0003 | 1.0 | 1.0000 | 44.5 | 125.0 | all-graduate fast | neither (keep watching) |
| 94TgdY | SOL | 238 | 79.3 | 4 | 238 | 0.0013 | 1.0 | 1.0000 | 94.0 | 95.0 | all-graduate fast | neither (keep watching) |
| AqUCiE | SOL | 233 | 77.7 | 18 | 233 | 0.0027 | 1 | 1.0000 | 88.0 | 36 | all-graduate fast | neither (keep watching) |
| DZWnZv | SOL | 230 | 76.7 | 230 | 0 | – | 0.0 | 1.0000 | 1.0 | 1.0 | one-trade completer | no data |
| 4LbbPN | SOL | 220 | 73.3 | 20 | 219 | 0.0000 | 0.0 | 1.0000 | 123.5 | 91.0 | all-graduate fast | neither (keep watching) |
| YYvred | SOL | 219 | 73.0 | 2 | 219 | 0.7691 | 2 | 1.0000 | 90.0 | 70 | all-graduate fast | drop (bots are here) |
| FSUTuA | SOL | 214 | 71.3 | 214 | 0 | – | 0.0 | 1.0000 | 1.0 | 1.0 | one-trade completer | no data |
| ENyc6m | SOL | 191 | 63.7 | 191 | 0 | – | 0 | 1.0000 | 0.0 | 1 | one-trade completer | no data |
| DwP6Zr | SOL | 189 | 63.0 | 189 | 1 | 1.0000 | 0 | 1.0000 | 1.0 | 1 | one-trade completer | drop (bots are here) |
| 5oTzDL | SOL | 188 | 62.7 | 1 | 188 | 0.7237 | 2.0 | 1.0000 | 75.5 | 85.0 | all-graduate fast | drop (bots are here) |
| 7bBBrP | SOL | 166 | 55.3 | 162 | 165 | 0.0003 | 1.0 | 1.0000 | 68.0 | 71.5 | all-graduate fast | neither (keep watching) |
| 6mJvFp | SOL | 154 | 51.3 | 102 | 0 | – | 0.0 | 1.0000 | 0.0 | 1.0 | one-trade completer | no data |
| DNrh3u | SOL | 142 | 47.3 | 10 | 141 | 0.1841 | 1.0 | 1.0000 | 157.5 | 30.0 | all-graduate fast | neither (keep watching) |
| bTUgLc | SOL | 130 | 43.3 | 37 | 130 | 0.8217 | 2.0 | 1.0000 | 89.0 | 42.5 | all-graduate fast | drop (bots are here) |
| CkU1cp | SOL | 118 | 39.3 | 77 | 98 | 0.0004 | 1.0 | 1.0000 | 50.0 | 92.0 | all-graduate fast | neither (keep watching) |
| AhXeFw | SOL | 112 | 37.3 | 103 | 0 | – | 0.0 | 1.0000 | 0.0 | 1.0 | one-trade completer | no data |
| 5cwmaY | SOL | 111 | 37.0 | 20 | 108 | 0.0040 | 0 | 0.0723 | 63.0 | 30 | launchpad-like | neither (keep watching) |

**Classes.** They are POST HOC: I defined them after the first table, and their thresholds are my choice.
* "One-trade completer": ≥ 80% of a partner's pools complete ≤ 5 s after the create.
* "All-graduate fast": ≥ 80% graduate within 24 h and the median completion is ≤ 600 s.
* "Launchpad-like": the rest.

The class depends on **future** outcomes (completion up to 24 h later). So it is a description, not a causal filter. It would become causal only as a partner list built on earlier days and applied to later ones.

| Class | Quote | Creates | /day | Creators | Pools with ext. buys | Share slots 0-1 | Median others 0-1 | Mean others 0-1 | Grad ≤24 h | Complete ≤5 s | Median trades 30 s | R1 thresholds applied to the class |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all-graduate fast | SOL | 4019 | 1339.7 | 1439 | 3920 | 0.4054 | 1.0 | 1.0722 | 1.0000 | 0.0216 | 82.0 | drop (bots are here) |
| one-trade completer | SOL | 2339 | 779.7 | 2029 | 17 | 0.9881 | 0 | 0.0056 | 1.0000 | 0.9996 | 1 | drop (bots are here) |
| launchpad-like | SOL | 2315 | 771.7 | 1244 | 1507 | 0.0708 | 0.0 | 0.5847 | 0.0296 | 0.0022 | 5.0 | nominally "candidate" (see caveat) |
| launchpad-like | USDC | 177 | 59.0 | 73 | 83 | 0.1065 | 0 | 0.2542 | 0.0517 | 0.0000 | 1 | neither |
| launchpad-like | CWZ6Bs | 137 | 45.7 | 82 | 133 | 0.5498 | 1 | 1.1022 | 0.0745 | 0.0000 | 5 | drop |
| launchpad-like | XsDoVf | 114 | 38.0 | 27 | 28 | 0.0733 | 0.0 | 0.0439 | 0.2500 | 0.0000 | 0.0 | neither |
| launchpad-like | 7AoBuY | 50 | 16.7 | 48 | 41 | 0.0020 | 0.0 | 0.1000 | 0.1667 | 0.0000 | 5.0 | neither |

**Caveat on the "launchpad-like SOL" row.** It is **not a front end**. It is a residual bucket of 1,244 creators across many partners, defined by its outcomes after the fact. R1 is declared per front end, so this row does **not** pass R1. I show it only because it is the one grouping above 200/day with a share under 10%. Its graduation rate is 0.0296, and its median trades in 30 s is 5.

## 4. R1 verdict per front end (P36 thresholds, unchanged)

* **Drop (bots are here):**
  * Bags SOL (share 0.3038)
  * bingan (0.5498)
  * oneonly.lol (0.5434)
  * brown-left-cougar pinata host (0.3008)
  * Bots Family (0.3161)
  * violet-obedient-ferret pinata host (median others 2)
  * the "mint suffix pump" one-trade group (0.4550, from 6 pools)
  * among unattributed partners: CtNKS1, J7yBcg, YYvred, 5oTzDL, bTUgLc, ECmYUu, 49FxoT, DwP6Zr (the last one from 1 pool)
* **Candidate:** **none.** No front end has ≥ 200 creates/day.
* **Neither (keep watching):**
  * Moonshot USDC (0.1291)
  * Jupiter Studio (USDC 0.0392 / JUP 0.0059 / SOL 0.0123)
  * Bags XsDoVf quote
  * arthurlabs, capx, whaletails, souvenir, xstok, 100kdaily, rex.fun, doubletap
  * the low-share unattributed graduation partners (8TPACX, HyhosN, 94TgdY, AqUCiE, 4LbbPN, 7bBBrP, DNrh3u, CkU1cp, 5cwmaY)
* **No data** (no outside buys in 30 s): spec-fun, and the one-trade completers DZWnZv, FSUTuA, ENyc6m, 6mJvFp, AhXeFw.

## 5. Limits and what was not measured

* **Attribution coverage.** 7,746 of 9,572 creates (80.9%) belong to 1,321 partners left "unattributed". For most of them the metadata uri sits on ipfs.io or pinata gateways. Off-chain metadata JSON (for example a `createdOn` field) was **not** fetched. That is a token-metadata fetch, governed by the meta-capture QP rulings, and outside this brief.
* **Slot-0-1 "other" buyers** include launcher bundle wallets that are not the creator or the payer; they cannot be told apart without a wallet graph. This is the same bias P36 disclosed.
* **Sampling.** Payer exclusion uses only the 1,000 sampled txs. In those, payer = creator except for Moonshot, whaletails and a few small hosts, so the effect is small. It is not zero for those hosts.
* **Time window.** Three exploration days only, 10-14..10-16, which includes the Swap3 switch at 10-16T04Z. There is no October-forward or later evidence. Front ends may have changed since.
* **Not measured:** prices, returns, impact, latency, and which of these pools m10 or P18 actually select.

## 6. Open items for the manager

1. **m10 overlap.** What share of m10's exploration attempts falls on each class or front end? That needs m10's exploration-day screen output, joined on pool. I did not open it. It is exploration data, but it is outside this brief.
2. **Partner-list filter as a causal rule.** If the manager wants it: build the list on 10-14..15 and apply it to 10-16, counts only first. It needs QP, and it narrows m10. It cannot change the 11-01 read.
3. **Believe and Scribe** are still unidentified. A desk lookup of their partner keys would close this.

## Files
* `tools/dbc_frontend_attrib.py`: `fetch` (public RPC) and `table` (offline).
* `tools/test_dbc_frontend_attrib.py`: 16 tests on synthetic data.
* `data/table_rows.json`: rows with ≥ 5 creates, per front end and per class, with fee schedules.
* `data/partners_ge10.json`: partners with ≥ 10 creates (label, evidence, class, hosts, suffixes).
* `data/p52_calls.jsonl`: every RPC call.
* Full cache and table on research-0: `/data2/mal/p52-dbc-frontend/` (`attrib_cache.json`, `table.json`).
