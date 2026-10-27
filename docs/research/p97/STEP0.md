# P97: Step 0 desk checks for `p95-callout-follow` and `p81-holder-carry`

Lane P97, 2026-10-27. Desk and public reads only: the pump public docs (local copy of `pump-fun/pump-public-docs` at
its latest commit `639b87fb45`, 2026-10-13, checked against GitHub today), public web pages, public GitHub code, and
public Solana RPC (`api.mainnet-beta.solana.com`). 0 Helius credits. No MAL tape row, no price read from any MAL
tape, no Q50/HV4 output, no EXP-024 file, no DBC data, no `/data/mal/meta-capture/` listing. No wallet, no key, no
transaction, no account, no login. MAL never posts callouts. This note makes no money claim; no book has cleared the
promotion gate.

## 1. `p95-callout-follow`, Step 0: is there a public, timestamped callout feed?

**Result: NOT_TESTABLE.** No callout feed meets the row's terms (per-call timestamp and mint, readable without an
account or key). Step 1 is not written, per the row.

### What was checked

| Source | What it is | Per-call time and mint? | Without account or key? | Visible when? |
| --- | --- | --- | --- | --- |
| pump public docs (`pump-fun/pump-public-docs`, all files at `639b87fb45`; GitHub code search "callout" in the repo: 0 hits) | the only official developer docs | none: no callout API, program, account or event is documented | n/a | n/a |
| pump, PumpSwap and fee program IDLs (same commit) | on-chain programs | none: no instruction, account or event names a callout (only `holder`, `cashback`, `distribute*` names exist) | n/a | n/a |
| `frontend-api-v3.pump.fun/callout/coin/{mint}` and `/callout/list/{callerId}` (undocumented app backend; named in public GitHub clients: `nirholas/three.ws` `api/_lib/pump-callouts.js`, `Firestorm0609/zane` `callouts.py`) | per-mint or per-caller history, fields `calloutId`, `createdAt` (ms), `thesis`, `username`, call market cap | yes, per call | the clients say "anonymous-capable", but **from MAL's host the endpoint answers HTTP 403 with a Cloudflare JS challenge** ("Just a moment...", measured 2026-10-27T20:33:42Z on `/callout/coin/{mint}`; `/coins/latest` and `pump.fun/docs/*` the same). The clients report about 60 requests/min per IP (`x-ratelimit-limit: 60`) and Cloudflare 1015 bans with Retry-After up to 600 s | only by polling one caller or one mint at a time: 100 callers at 50/min is one look per caller every 2 min |
| `frontend-api-v3.pump.fun/following-feed` and `/callout/leaderboard` | the only all-callers feeds | yes | **no**: the zane client needs a logged-in Bearer token (Privy/Firebase, about 1 h expiry) for both; `/callout/leaderboard` returns 401 without it | push-notification speed for followers |
| `advanced-api-v2.pump.fun/callouts` (captured 2026-06-17, `BankkRoll/pumpfun-apis`) | app endpoint | unknown | **no**: the capture shows `Authorization: Bearer` and an `_rsc` token | unknown |
| Orbital callouts feed (docs.orbitalhq.xyz) | third-party relay of gold-tick callers | caller, coin, contract address, message, call market cap, "age" (no explicit timestamp field) | **no**: "requires an active subscription or NFT holder access"; Telegram or Discord webhook, no API | "usually less than a minute after the caller posts" |
| Xanguard callout alerts (xanguard.tech) | third-party relay | `callout_new` event with `caller_wallet`, `mint`, `callout_mcap`, `thesis` | **no**: paid plans, API key (`/apikey`) | "typically within a few seconds" (vendor claim) |
| Apify actor `memecoin-caller-track-record` | third-party archive | callout id, call time, mint, call market cap | **no**: paid actor ($20 per 1,000 callers), historical only | after the fact |
| `pump.fun/docs/callout-reward-terms` | the program's terms | n/a | 403 to the fetcher and to curl from MAL's host; Internet Archive offline at the time of the check | n/a |

The Callout Rewards payout itself is off-chain (daily, in USDC or PUMP, per the census sources); no on-chain program
or memo carries a call with a time and a mint.

### Why it is NOT_TESTABLE

1. **No documented public feed.** The official docs and IDLs have no callout surface at all.
2. **The only per-call sources need an account, a key or a subscription,** or are an undocumented app backend that
   blocks MAL's host with a browser challenge. Passing that challenge (headless browser, proxy rotation, a
   logged-in token) is a scraping-around-access step this lane is not allowed to take, and would be an owner decision
   with a terms-of-service question.
3. **Latency, even if access were granted.** Followers get a push notification at the call instant `t`. A poller of
   per-caller endpoints at the reported per-IP limit sees a top-100 caller's call on average about 60 s late (one look
   per caller per 2 min); paid relays claim "a few seconds" to "under a minute". The row's own kill line needs MAL in
   before the followers (entry at `t + 2 s`, and KILL if the median first follower buy lands within 2 s). A source
   that shows the call seconds to a minute after the followers' push notification cannot give that lead, so even a
   granted feed would most likely hit the row's latency kill on day 1. That is a judgment from the vendors' claimed
   delays, not a measurement.

### What would reopen it

An official, documented, unauthenticated callout stream (websocket or REST with `since`), or an on-chain record of a
callout with its slot and mint, either with a measured visibility delay under 2 s. Not a scraper, not an account.

### Not measured

The callout count per day, caller sizes, follower volumes, follower timing, the true visibility delay of any source,
and whether the frontend endpoints answer a residential IP without a challenge. No callout was read.

## 2. `p81-holder-carry`, Step 0: are holder rewards paid as documented?

**Result: payouts are real, on chain, frequent and pro rata. The row's Step 0 kill clause ("no payout seen to
sampled holders") does not fire.** The other two kill clauses (drift minus cost against the carry bound; fewer than 5
flagged days at turnover >= 3) need tape turnover and drift, so they belong to Step 1 and were not evaluated here.

Reads: public RPC only, 2026-10-27 20:26Z to 21:00Z. **756 calls** counted as attempts, 429 retries included (668
`getTransaction`, 55 `getSignaturesForAddress`, 24 `getMultipleAccounts`, 5 `getAccountInfo`, 4
`getProgramAccounts`), under the 2,000 cap. 0 Helius. Scripts: `docs/research/p97/scripts/` (stdlib only; `rpc.py`
enforces the cap). Round summaries: `docs/research/p97/holder_rounds_sample.json`.

### How the routing works (docs, IDL and chain agree)

- The pump IDL (`idl_pump.json` at `639b87fb45`) has `distribute_fee_to_holders(amounts: Vec<u64>)`: it "pays fees
  collected on the `holder-rewards` PDA ... out to holders: `amounts[i]` goes to remaining accounts `[2i]` (owner)",
  signed by `Global.holder_reward_claim_authority`. The amounts are chosen off chain by that signer; the transfers
  and a `DistributeFeeToHoldersEvent` (mint, quote mint, recipient count, total) are on chain.
- `Global` (`4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf`, 1,088 bytes, decoded to the last byte):
  `holder_reward_claim_authority = HckQ93Xqjjo8mwt5pNPWvyCTZXQZ858rvzmm7ZRrZg9t`, `is_holder_reward_enabled = 1`,
  `max_configurable_creator_fee_bps = 300` (the 3% custom-pair maximum in the census).
- The coin's creator is the PDA `["holder-rewards", mint]` (pump program); our derivation matches the accounts seen
  in the transactions. One round, as seen on chain: PumpSwap `transfer_creator_fees_to_pump` (pool creator vault to
  the pump creator vault), then `collect_creator_fee` (vault to the PDA), then a run of `distribute_fee_to_holders`
  transactions with up to 17 recipients each. Recipients come in ascending owner-address order.
- Over the 10,000 most recent signatures that mention the authority (73 s of history), 250 succeeded; 231 of the
  ones it signed were distributions across 13 mints, quote mints SOL (196), and the pump-coin quotes `DJTu7v...`
  (21), `TTWofw...` (9) and `2BJDwU...` (5). The authority also signed 6 Jupiter swap transactions (not explained
  here) and 11 collect transactions.

### Who receives

Full holder snapshots (`getProgramAccounts` on the mint, all token accounts) against full rounds:

| Coin | Round (UTC) | Txs | Recipients | Holders with a balance | Paid | Recipients' share of supply | Implied minimum balance at the round |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `7ttfwuBS...pump` | 10-27 20:16:44 | 14 | 200 | 278 | 0.199118 SOL | 0.883 (latest round) | 135,702 tokens = 0.0138% of supply |
| `7ttfwuBS...pump` | 10-27 20:27:45 | 15 | 205 | 278 | 0.300159 SOL | 0.883 | same |
| `51SfaAyr...pump` | 10-27 20:42:49 | 127 | 1,781 | 7,074 | 1.037525 SOL | 0.861 | 20,258 tokens = 0.0021% of supply |
| `51SfaAyr...pump` | 10-27 20:48:54 | 124 | 1,747 | (same snapshot) | 0.248806 SOL | n/a | n/a |
| `H1fK1zrj...pump` (quote `TTWofw...`) | 10-27 20:22:26 | 7 | 38 | not read | 102 raw quote units | n/a | n/a |

- **Pro rata on balance.** Paid share over held share is one constant inside a round: 95% (7ttfw) and 97% (51Sfaa) of
  recipients sit within 1% of the round's per-token rate; the rest are wallets whose balance moved between the round
  and the snapshot. All top-11 wallets of 7ttfw, and 9 of the top 11 of 51Sfaa (the other 2 moved balance), are paid at the same ratio to 3 decimals.
- **Balance at the round instant, not time-weighted.** The 16 wallets paid at 20:48:54 that were not paid at
  20:42:49 got exactly 1.000x the per-token rate on their balance (snapshot 20:56Z). A holder is paid for the fees
  accrued since the previous round if it holds at the round.
- **Not paid: program-owned accounts.** All 1,781 recipients of the 51Sfaa round are system-owned wallets (1,773) or
  had no account when queried (8). The unpaid holders among the top 60 are: the canonical PumpSwap pool vault
  (5.70%; 7ttfw: 11.68%), the bonding-curve account (1.06%), two Meteora DLMM positions (0.59%, 0.37%) and a
  second PumpSwap pool (0.34%). The pool and the other venues are excluded, so the fee goes to wallets only.
- **Not paid: small balances, and wallets with 0 SOL.** Holders below the implied minimum are skipped (the minimum is
  a different supply share per coin, which fits a value threshold such as the secondary-source "$20 minimum"; the
  threshold's unit was not measured). Of 20 sampled 51Sfaa non-recipients above the minimum, 16 are owners with no
  account (0 SOL, where a payout below the rent-exempt minimum cannot land), 2 changed balance after the round
  started, and 2 are funded wallets with a static balance (unexplained: a bot list, a holding-age rule or a moving
  threshold are all possible). 2 zero-SOL owners per round were paid. For a MAL holder (a funded wallet, a stake far
  above the minimum) none of these exclusions would bite.
- **Bots.** Nothing in the data marks bots as excluded: every funded wallet above the minimum that held through a
  round was paid, apart from the 2 unexplained wallets above.

### Cadence

`getSignaturesForAddress` on each coin's holder-rewards PDA, rounds split at gaps over 120 s:

| Coin | History read | Rounds | Median gap between rounds | Txs per round (median) |
| --- | --- | --- | --- | --- |
| `51SfaAyr` | 10-27 18:24 to 20:43 (3,000 sigs, truncated) | 23 | 361 s | 133 |
| `2BJDwUdM` | 10-27 18:24 to 20:44 (full) | 28 | 323 s | 110 |
| `6CNSiWUE` | 10-27 20:22 to 20:40 (full) | 5 | 272 s | 7 |
| `H1fK1zrj` | 10-24 16:43 to 10-27 20:27 (truncated) | 174 | 542 s | 19 |
| `Bo4iBcLk` | 10-27 19:13 to 20:27 | 6 | 843 s | 4 |
| `7ttfwuBS` | 10-23 19:56 to 10-27 20:27 (full) | 162 | 980 s (1,978 s over the last 1,000 sigs) | 15 |
| `474cBLXZ` | 10-27 20:27 to 20:46 | 2 | 1,125 s | 6 |

Rounds run every 4.5 to 33 minutes per coin, continuously over days; there is no daily batch.

### Rate, pass-through and cost

- **Pass-through to holders.** 7ttfw 20:16 round: 199,052,659 lamports collected into the PDA, 199,117,968 paid out
  (the 65,309 difference is carry-over in the PDA). The authority pays itself back **9,900 lamports per round
  transaction** from the pool (exact on all four SOL rounds: 14, 15, 127, 124 txs), which is 0.05% (7ttfw) and 0.12%
  (51Sfaa) of the paid amount. On a tiny coin the same charge dominates: the `H1fK1` round sent 36 of 102 raw quote
  units to the authority.
- **Fee rates actually charged** (one trade each, fee basis-point fields only, see the seal note below): 7ttfw sell at
  20:57:22Z: `coin_creator_fee_bps = holder_rewards_bps = 90`, `lp_fee_bps = 20`, `protocol_fee_bps = 5`. 51Sfaa sell
  at 20:58:33Z: creator = holder rewards 75 bps, LP 20, protocol 5. So r is 0.90% and 0.75% here (the row assumed
  0.95%), and p + lp = 0.25% as the census assumed.

### Break-even turnover x days, with the measured rates

The census formula is turnover x days > 2(r + p + lp) / r = 2(1 + (p + lp)/r). Two measured corrections: holders are
paid on the eligible supply E, not the whole supply (carry per day = phi x r x turnover / f, with f = E / supply), and
phi = 1 minus the authority's per-transaction take. So the measured bar is 2 f (r + p + lp) / (phi r).

| Coin | r | p + lp | f | phi | Census formula | Measured bar | Carry per unit of daily turnover |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `7ttfwuBS` | 0.90% | 0.25% | 0.883 | 0.9995 | 2.556 | **2.258** | 1.019% a day |
| `51SfaAyr` | 0.75% | 0.25% | 0.861 | 0.9988 | 2.667 | **2.299** | 0.870% a day |

Priority fees and slippage are left out, as in the census; impact at size is not priced. The bar only covers the fee
round trip. P70's -12.4% mean 24 h drift on top-volume pools is about 12 to 14 times the carry at turnover 1, which is
why the row's first Step 1 output is drift alone.

### Kill line and status

- Step 0 kill clause, "no payout seen to sampled holders": **not met**. Payouts were seen to 3,971 recipient slots
  across 5 rounds of 3 coins.
- The revive trigger asks for this **and** at least 5 flagged days. The flagged-day count (holder-rewards coins by
  day with turnover >= 3) was not run: it needs the tape mint list and tape volume, which this lane does not read.
  The row stays `untested`, with the routing question answered and the carry formula corrected.

### Seal and data notes

- PumpSwap events were decoded for 2 trades only, and only their fee basis-point fields were read. The script
  (`feebps.py`) skips reserve and amount fields by offset and never prints or stores them. It accepts a trade only at
  least 360 s after a distribution round that had already swept that pool's PumpSwap creator fees, so both pools
  were past their first 360 s. No DBC account or event was read. No MAL tape, Q50, HV4 or EXP-024 file was opened.
- Distribution transactions hold no prices or reserves. Holder snapshots are token balances, and no price was
  derived from them.

### Not measured

The value threshold's unit; why 2 funded static wallets were skipped; the per-day count of holder-rewards coins or
their turnover; drift; any price, market cap or P&L; the 6 Jupiter swaps by the authority; pump-coin-quote rounds
beyond the one `H1fK1` round; whether pump.fun can pause or change distributions (the global switch only stops new
holder-rewards coins).
