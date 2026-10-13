# Item 6 run: declaration before the run

Written 2026-10-13T07:34:09Z (`date -u`), before any run of `tools/h5_upgrade_dryrun.py` on these pools.

- Tool: `tools/h5_upgrade_dryrun.py` at main `deb5098f9ae0741bfb9216e1d2ca524c98fb6015` (PR #628, Reviewer OK 6140635256 at 8efa312).
- Deploy slot of the redeployed fees program: 456132171 (2026-10-12T17:40:24Z).
- Pool source: the review's graduation lists `raw/s04_post_new.json` (sha256 e6c3948477ae6d5eafe4780ea3986b8f51917e203b4ae0dc108ad2e2e387a8ba) and `raw/s04_post_early.json` (sha256 ca046a4b1de4247d7b872b25c992df37f7e0dfcb0ccecbb86bc500fb32388b33).
- Selection rule, fixed here: rows with migrate slot > 456132171, WSOL quote, InitBoost present, not mayhem, `synthetic` false; sorted by slot descending; the first 6 of each list. 12 distinct pools. No pool is added, dropped or replaced after a run.
- Run: one MiScusi job on mal-research-0, public RPC `https://api.mainnet-beta.solana.com`, `--sol 0.02`, default `--min-round-trips 5`, default slippage 300 bps.
- Reading rule: the tool's verdict over all 12 pools is the result. PASS needs 0 simulate errors and 0 fee mismatches on all 12.
- One repeat only, on the same 12 pools, and only if the first run has no fee mismatch and no simulate error and its only problems are RPC errors or `tier_boundary` (a pool that crossed a tier between snapshot and simulation). Both runs are reported.
- A fee mismatch or a simulate error on any pool in any run: item 6 is not met, H5 stays stopped, and the decision goes to the owner (DEC-024 Amendment 7 item 2, item 6).
- The reviewer's 10 disclosure limits (PR #628 comment 6140635256) are quoted with the result, including `tiers_covered`.

| # | list | pool | migrate slot | block_time |
|---|---|---|---|---|
| 1 | s04_post_new | `D4jdDGjFyVZso4sYmKVRqnbKczoURtRuYVcynTDHGVb8` | 456354425 | 1791875378 |
| 2 | s04_post_new | `6sa9Sf1z1eGb1i4jhRZhzL1yb5cCnFV3t21Uo25jJfFm` | 456352982 | 1791875066 |
| 3 | s04_post_new | `2ChJNh8mjX8dkZtM1e5kaUGswCtW1GFFfE5EwvgHZ4w2` | 456351765 | 1791874802 |
| 4 | s04_post_new | `6XdSLSyqWSDQ748iRw6a4LsiSJXADAyx2Y8cFSci2a3J` | 456351260 | 1791874692 |
| 5 | s04_post_new | `3Gkawhzb2sjwixXSats1oMRaC2SSGEPZMmSCKAUScyHt` | 456351128 | 1791874663 |
| 6 | s04_post_new | `FknBQPKM2WsN8nn3q8K1W11fnnqEV2tvLNeu9atVLqn4` | 456350760 | 1791874584 |
| 7 | s04_post_early | `DbZMpAFAatREVbu3WewxVxyxZUT5moUhs3PWFetFUaJW` | 456180548 | 1791837420 |
| 8 | s04_post_early | `AnrCHvfJUzMtos8DGjpT2XKurVPPYAVLDturASNwXcd4` | 456179982 | 1791837296 |
| 9 | s04_post_early | `WJieCbpb7BbcQdFrm1j86DkK3EK2HtWErj9fXTbx242` | 456179824 | 1791837261 |
| 10 | s04_post_early | `ARd5zyCtLV1dsbfbuQUPzNPEPb4ZGCqk8e6xAyaVTyeV` | 456179712 | 1791837237 |
| 11 | s04_post_early | `52sn87Ne3Td2EBpRKB1RVyNdyZ9Uc1Ujs5cCizB1cyc8` | 456179300 | 1791837147 |
| 12 | s04_post_early | `H6wK6oQFa9iN63SdkziLBPrWxB4KDTJGRismgyf8rDRf` | 456179037 | 1791837089 |
