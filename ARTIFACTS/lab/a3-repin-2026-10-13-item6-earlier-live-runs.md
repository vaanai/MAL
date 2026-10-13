# Live runs of `tools/h5_upgrade_dryrun.py` before the declared item-6 run

Written 2026-10-13T07:56:48Z by the manager from the builder's own list (its transcript; it recorded no timestamps) and the two saved output files. All runs were on 2026-10-13, after the fees-program deploy (2026-10-12T17:40:24Z), against `https://api.mainnet-beta.solana.com`, during PR #628, before the declaration of 07:34:09Z. Order as the builder gave it.

How the builder found its pools: the first account of PumpSwap instructions in the newest transactions touching PumpSwap GlobalConfig (most recently traded pools, not selected by migration). Whether each migrated after slot 456132171 was not checked by the builder. Two of them are in the declared 12 and are post-upgrade by the review's lists: `6XdSLSyqWSDQ748iRw6a4LsiSJXADAyx2Y8cFSci2a3J` and `3Gkawhzb2sjwixXSats1oMRaC2SSGEPZMmSCKAUScyHt`.

| # | tree state | pools | `--min-round-trips` | round trips ok | simulate errors | tool's `n_fee_mismatch` | per-pool problems | verdict / exit | saved output |
|---|---|---|---|---|---|---|---|---|---|
| 1 | first commit, before the inner-instruction decoder fix | 5 (one address mistyped) | 5 | 0 | 0 | 5 | 3 `events_not_decoded` (simulate `err` null, CU 207642 / 212658 / 189640; the decoder did not yet accept the public node's `programId` form), 1 `not_canonical_wsol_pool`, 1 `snapshot_no_pool` (the mistyped address) | FAIL / 3 | overwritten by run 2 |
| 2 | uncommitted decoder fix, before `eb83c1c` | 3 | 1 (allowed then) | 3 | 0 | 0 | none | PASS / 0 | `builder-smoke-run2.json` (file mtime 07:25:34Z) |
| 3 | `8efa312` | 10 | 5 | 6 | 0 | 4 | 4 `not_canonical_wsol_pool` (no simulation attempted, observed and expected bps null) | FAIL / 3 | `builder-smoke-run3.json` (file mtime 07:31:43Z) |
| 4 | `8efa312` | the 6 that passed in run 3 | 5 | 6 | 0 | 0 | none | PASS / 0 | none (summary line only) |

- In the two saved files, every pool that produced events shows observed bps equal to expected bps on the buy and on the sell (3 of 3 in run 2; 6 of 6 in run 3). The tool's `n_fee_mismatch` counter counts every pool that did not match, including pools with a problem and no events; in runs 1 and 3 all the counted pools are problem pools as listed. For run 1 that statement rests on the builder's list only (its file was overwritten).
- Ad-hoc calls outside the CLI (the builder's `dbg.py`, tool functions only): one crash before the decoder fix (`KeyError: 'data'`), one structure print (simulate `err` null), one print of fee fields on 2 pools (bps (20,5,5) and (20,5,75), the ceil relation held on all legs). One `getAccountInfo` on the PumpSwap FeeConfig before the tool existed (sha256 equal to the pin).
- Consequence for the declaration: it was written knowing the tool had returned PASS on live programs after the deploy, on 6 pools in its last earlier run, 2 of which are among the declared 12.
