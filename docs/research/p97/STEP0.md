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
