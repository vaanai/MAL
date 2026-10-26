# P41 RESULTS: p37-cto-hold, detection + counts. COUNTS ONLY.

Written 2026-10-26T19:45Z (`date -u`). Rules: [DECLARE.md](DECLARE.md) (committed `00923489` before any count) and its Amendment 1 (`e839e880`, before the tape scan). No price, no return, no P&L. 0 Helius credits.

## Verdict: **KILL** (K0, rate), on both sets

| Set | Covered full UTC days | Events | Triggers | Median triggers / full day | K0 (≥ 3) | K1 alive24 (≥ 0.5) | K2 q_up24 (≥ 0.25) | K3 depth (≥ 0.5) | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| P: `admin_cto` (as declared) | 8 (09-13, 09-14, 09-19..09-24) | 101 | 77 | **2.0** | fail | 60/68 = 0.8823529411764706 | 33/68 = 0.4852941176470588 | 68/77 = 0.8831168831168831 | **KILL (K0)** |
| S: `admin_cto` ∪ `admin_set_creator` ∪ `admin_set_coin_creator` (Amendment 1) | 30 | 214 | 181 | **2.0** | fail | 140/161 = 0.8695652173913043 | 50/151 = 0.33112582781456956 | 162/181 = 0.8950276243093923 | **KILL (K0)** |

Amendment 1: ADVANCE needs both sets to pass. Both fail K0, so the lane verdict is KILL. Per IDEAS/DECLARE, the kill is final for these blocks; no threshold is re-tuned.

## Detection (step 2): how a CTO shows on chain, and coverage

- **The tape has no CTO events.** The clean views of all five blocks hold only `trade`, `create`, `complete` and `migration` records. Trade rows carry no creator field, and `observe/trade_decode.py` decodes no creator-change event.
- **Public RPC recovers them in full, cheaply.** Every admin CTO is signed by one key, `UqN2p5bAzBqYdHXcgB6WLtuVrdvmy9JSAtgqZb3CMKw`. It is pump `Global.admin_set_creator_authority` (offset 418) and also PumpSwap `GlobalConfig.admin_set_coin_creator_authority`, and it is a Squads multisig vault. A single `getSignaturesForAddress` page (1,000 signatures) reaches back to 2026-07-06. 333 successful in-window signatures were each decoded with `getTransaction`.
- **Instructions:**
  - From **2026-09-12T15:31:15Z**: pump `admin_cto`, which CPIs into PumpSwap `admin_cto_pool` and pump_fees.
  - Up to **2026-09-11T11:31:57Z**: the older pump `admin_set_creator` and PumpSwap `admin_set_coin_creator` (IDL at pump-public-docs `9c82f61cb7`).
  - The authority's other 118 transactions are Squads account closes, creator-fee collects and distributions, and fee-sharing resets. None is a trigger.
- **Coverage hole:** 1 in-window successful signature returned `null` from `getTransaction` (window A, 2026-08-24T00:18:50Z, slot 441274282). The day it falls on has 3 triggers in set S. One more event cannot move the 30-day median of 2.0.
- **Not ruled out:** a second admin key used in parallel. The key was active on every day of the three windows, and both programs name the same key today.
- **Public RPC calls:** 415 of the 1,500 cap. That is 333 `getTransaction` ok, 78 `getTransaction` HTTP 429 (counted against the cap), 3 `getAccountInfo` and 1 `getSignaturesForAddress`. Every call is in [rpc_calls.log](rpc_calls.log). The pre-09-12 IDL came from GitHub (`gh api`), which is not RPC.

## Counts (step 3)

Tape scan: MiScusi job #1674 @ `3ac9af89` over 776 hours (A 336, B 288, C 152). It found 7,042,179 trade rows, 43 create rows and 78 migration rows for the 213 event mints. The scan finished. The job's chained counts step then ran out of memory (it loaded every row), so the counts were re-run in streaming form by job #1681 @ `7eb602cc`. Input sha256: trades.jsonl `4240d9c6…48787`, counts.json `5afa867d…1416d`. Full numbers are in [counts.json](counts.json) and [scan_manifest.json](scan_manifest.json).

**Events per window:**
- Set P: B 84, C 17.
- Set S: A 104 (103 `admin_set_creator`, 1 `admin_set_coin_creator`), B 93, C 17.

**Triggers per full UTC day:**
- Set P: 09-13 20, 09-14 11; 09-19..09-24 2 each.
- Set S:
  - Window A (08-15..08-27): 7, 2, 12, 7, 11, 1, 7, 5, 5, 3, 7, 8, 9. Median 7.
  - Window B (09-04..09-14): 1, 2, 1, 0, 1, 2, 0, 1, 32, 20, 11. Median 1.
  - Window C (09-19..09-24): 2 each. Median 2.0.

The rate fell from about 7/day in mid-August to 0-2/day in September. The 09-12..09-14 burst is the holder-rewards launch: 87 of the 101 `admin_cto` events only switch a coin to holder rewards. They name no new creator, so they are not a new steward.

**Venue at the event (triggers):**
- P: pumpswap 65, pump_bonding 12.
- S: pumpswap 124, pump_bonding 56, none 1.

**Quote depth at the event (triggers):** PumpSwap is pre-trade vault quote, with V not added. Bonding is post-trade virtual SOL.

| Set | Venue | n | p25 | Median | p75 |
|---|---|---|---|---|---|
| P | PumpSwap | 65 | 56.237171295 | 147.306673494 | 393.109990033 |
| P | Bonding | 12 | 31.671058421 | 47.116621452000004 | 66.601276719 |
| S | PumpSwap | 124 | 48.60239935 | 83.9796440555 | 240.229943573 |
| S | Bonding | 56 | 47.151200709 | 59.183916261 | 72.82943187 |

**Pool or curve age at the event, in hours.** Many ages are left-censored lower bounds:

| Set | Venue | Median age (h) | Left-censored |
|---|---|---|---|
| P | PumpSwap | 702.6969444444444 | 46 of 65 |
| P | Bonding | 75.29569444444445 | 5 of 12 |
| S | PumpSwap | 183.765 | 65 of 124 |
| S | Bonding | 22.231805555555557 | 49 of 56 |

**Censored:** events whose t+25h falls past their window end. P 9, S 20.

**Count label only, no verdict: the `new_creator` subset (a new steward key, which is the idea's own definition of a CTO).**

| Set | Events | Triggers | Median per full day | alive24 | q_up24 | depth_ok |
|---|---|---|---|---|---|---|
| P | 14 | 10 | 1.0 | 7/8 = 0.875 | 2/8 = 0.25 | 7/10 = 0.7 |
| S | 127 | 114 | 2.0 | 87/101 = 0.8613861386138614 | 19/91 = 0.2087912087912088 | 101/114 = 0.8859649122807017 |

The S subset's q_up24 is below the K2 line of 0.25.

## Disclosures

- **Dry run before the scan.** A code check of the counts tool on the first 32 decoded events, against window C tape, printed partial labels before Amendment 1 (12 triggers; alive24 9 of 11; q_up24 2 of 11; depth_ok 10 of 12). Amendment 1 records this. No rule was changed.
- **Amendment 1 was written after seeing the authority's per-day transaction counts and instruction types.** These are public structure, not tape. It added set S. It did not change any threshold.
- **Seal contact.** Before DECLARE, a `find /` looking for IDL files went into `/data/mal/meta-capture/` and printed one filename (an `img/2026-10-24T00/*.png` path). No file there was opened. That tree was excluded from every later search.
- **Not measured:** any price path, return, exit or P&L. There is no October hour, no arena tape, no forward-1016, and no meta-capture read. No join was made to HV4 or Q50 outputs.
- **Memo text.** One authority signature carried a memo whose text looks like markup. It was treated as data and ignored.

No book has cleared the promotion gate.
