# P44 revive-trigger sweep

Status: plan fixed 2026-10-26T17:47Z (`date -u`), before the count run of record. Results are added below the plan in a later commit.

## Plan (fixed before the count run)

- Counter: `p44_count.py` sha256 `56d5a21878624971a0b1b1244772d784ea19bd4bc4d2fc53638e7ddad450a7a8`, a copy of hunt24 L3 `scripts/l3_count.py` (sha256 `c45bfbfa...7424d`) with five marked changes (see its docstring).
- Signature list: `data/agent_buyback_signer_sigs_1021_1026.txt` sha256 `b1027804a5a8117d5497c364b53748e12d92cfa49ccaf012401724d560de8c32`. These are the 194 successful signatures of the Agent Payments buyback signer `GmFrDZT2cdrqykgTikVdXbe8EtCgzUDM9VsDhQnwsUsG` with blockTime at or after 2026-10-21T00Z, from one public-RPC `getSignaturesForAddress` call at 2026-10-26T17:45Z.
- Data: closed arena tape hours 2026-10-21T00 .. 2026-10-24T23 (96 files, 4 full UTC days). These hours are walk-2-hour information (DEC-030 §2 R1) and are disclosed as such. No hour at or after 2026-10-25T00Z is read by the counter (path guard).
- The counter drops G0 (WSOL quote, V0 in [17.5e9, 17.7e9], the H5 / EXP-024 / Q50 universe) at its first print. No G0 trigger or BOOST test is computed.
- Outputs: pools, BOOSTed pools and H5-structure trigger counts per day for G1 (off-band WSOL), Gz, and G2 (PUMP, USDC and other quotes). Create, complete and pool_create rows by quote class and mayhem flag. Agent-signature trade rows by SOL size.
- Counts only. No price after any decision. No fill, exit, outcome or return. No ledger, bots.json, shadow, canary or sealed path is opened. Per C4 of the QP verdict on #900, these counts are ops information. They never decide or exclude an hour for HV4, Q50 or DBC m10.
- Run: MiScusi job, `run_p44_counts.sh`. Output goes to `/data/mal/hunt-1008/push-1016/p44-revive-sweep/p44_counts.json`.
