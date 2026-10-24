# P13 LAUNCHPAD CENSUS (2026-10-24, desk + public RPC, no keys, 0 paid credits)

Data: `census_sample.json` (MiScusi job #1464, research-0, 600 s of public `logsSubscribe` mentions, 8 programs, one socket) and `sigs_rate.json` (getSignaturesForAddress, newest 1000 per program). Scripts: `census_sample.py`, `sigs_rate.py`. One 10-minute afternoon sample: indicative only. The socket was cut 6 times (1002 then 429 on the fallback), so about 540 s of the 600 s is covered; per-day = count x 160. Exploration reading, not gate evidence.

## Program ids (getAccountInfo executable=true on mainnet, 10-24; this proves a program exists, not that it is the named product)
| Venue | Program id | Source of the name |
|---|---|---|
| Raydium LaunchLab | LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj | official IDL (P8) |
| Meteora DBC | dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN | Meteora docs |
| Raydium CPMM | CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C | P8 |
| Meteora DAMM v2 | cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG | P8 |
| Orca Whirlpool | whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc | memory, not cross-checked |
| Heaven | HEAVENoP2qxoeuF8Dj2oT1GHEnu49U5mJYkdeC8BAX2o | memory, name UNVERIFIED |
| Boop | boop8hVGQGqehUK2iVEMEnMrL5RbjywRzHKBmBE7ry4 | Bitquery via REPORT.md, name unverified |
| Moonit | MoonCVVNZFSYkqNXP6bxHLPL6QQJiMagDL3qcqUQTrG | secondary, name unverified |

## Measured (public RPC)
| Venue | 10-min notices (failed) | ok swap notices x160 = trades/day | getSignatures newest-1000 rate, fail share | Logs expose |
|---|---|---|---|---|
| DAMM v2 | 15,666 (5,238) | ~1.6M, mostly aggregator routes | 2.8M/day mentions, 24% fail | instruction names; swaps only via CPI, no amounts |
| Whirlpool | 7,911 (2,874) | ~0.9M, router noise | 1.1M/day, 29% | names only |
| DBC | 3,564 (913) | ~550k (swap+swap2) | 565k/day, 27% | names only (P8): no amounts/reserves/signer |
| CPMM | 2,637 (875) | ~290k, router noise | 356k/day, 30% | names only |
| LaunchLab | 94 (20) | ~10k incl. fails (P8: ~5k) | 78k/day mentions, 82% fail (spam/fail-heavy) | full TradeEvent: pre and post reserves, fees |
| Heaven | 0 | 0 | ~31 mentions/day, newest 31 min old | n/a |
| Moonit | 0 | 0 | ~80 mentions/day, 2% fail | n/a |
| Boop | 0 | 0 | ~9 mentions/day, newest 11 h old | n/a |

Not measured: launches/day for Whirlpool/CPMM/DAMM v2/DBC. Name matching on `Initialize*` also hits token-account setup, so my "creates" counter is not usable and is not reported. The "swaps within 2 slots of a create" proxy came out at about 50% to 70% on the noisy venues; with the broken create counter it is meaningless. Bot competition is therefore NOT measured; only failure share is: 21 to 30% on busy venues (failed tx = bots racing), 82% by signature count on LaunchLab.

## Fees (documented, third-party unless noted)
Heaven: 1% under $100k mcap, then 0.25% (community) or 0.5% (creator), ~35 SOL virtual liquidity (The Block, Substack; official docs not opened). Boop: ~400 SOL graduation, 2% post-grad fee to holders (Solana Compass; conflicts with Raydium model). Moonit: 1%, 80% LP fees to creators (REPORT.md). LaunchLab: protocol, platform, creator, referral fees per trade, CPMM-only since 08-17 (docs.raydium.io). DBC/DAMM v2: dynamic fee per config; not read. Whirlpool: per-pool tier.

## Ranked (guess for tradeable fraction: judgement, not measured)
| Rank | Venue | Why | Cost to record |
|---|---|---|---|
| 1 | CPMM pools born from LaunchLab graduation | ~19 grads/day (desk, Aug); known BOOST-like post-grad flow analog; already tied to launchpad-landscape-2 | free logs; needs a per-pool mentions filter from LaunchLab migrate rows, then amounts need token-balance fetch (paid or rate-limited) |
| 2 | DAMM v2 pools born from DBC migration | DBC is the only high-volume curve venue (~550k swap notices/day); graduation is a hard event | free logs for pool list, same per-pool filter; amounts need tx fetch |
| 3 | DBC amounts (upgrade P12) | volume huge, logs have no amounts | paid tx fetch |
| 4 | Whirlpool new pools | router-dominated, no launchpad flow | free but noisy; low tradeable fraction |
| 5 | Heaven / Moonit / Boop | no activity in sample; at most dozens of mentions/day | not worth a recorder; recheck monthly |

## Recommendation (at most 2)
1. LaunchLab-graduated CPMM pools: record only pools listed in LaunchLab migration rows (per-pool filter, not program-wide mentions: program-wide is 99% router noise).
2. DBC-graduated DAMM v2 pools by the same method, after P12 shows migration rows.
Do not record Heaven, Boop, Moonit now. Caveats: names of the three small ids unverified; 10-minute single-afternoon sample; no edge claimed.
