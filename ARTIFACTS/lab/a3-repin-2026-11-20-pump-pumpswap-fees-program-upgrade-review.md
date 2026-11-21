# Review: pump, PumpSwap AND fees program redeploys of 2026-11-20T19:27:54Z to 19:28:12Z (slots 471854409, 471854454, 471854493), re-pin

Builder: Claude worker, 2026-11-21 (started `date -u` 07:38Z), mal-research-0 worktree. Public RPC (api.mainnet-beta.solana.com) and unauthenticated GitHub only. 0 Helius credits, no key or env file. No tape, arena, book, ledger, shadow, declog, tip or FLOOR file opened; no price path, outcome or P&L computed. Scripts and small outputs: `ARTIFACTS/lab/program-upgrade-1121/` (`out/*.json`); raw responses are off-repo in `/data2/mal/scratch/a3-repin-1121/`. This is a reading of chain and repo facts. No transaction was built, signed, simulated or sent by this builder. No sentence here says any book makes money; no book has cleared the promotion gate.

Standard: the new ruling `/data/mal/hunt-1008/ops/QP-RULING-1121-a3-repin-triple.md` was NOT present when this review was started (checked at 07:40Z). Until it exists the standard is `QP-RULING-1115-a3-repin-dual.md` (sha256 64e37d09...) plus `-ADD1.md` (sha256 cfb13f96...) with the new slots, which carry `QP-RULING-1114-a3-repin-pump.md` Q1 items 1-13. When the 1121 ruling exists it controls, and this review states where it differs. Each section says which bar it answers.

## Verdict

PENDING (written at the end).

## 1. Chain facts read (ruling Q1 item 11, (d), (e))

Read from public RPC at slots 472058146 to 472058173 (2026-11-21 about 07:39Z; `out/s01_programdata.json`). Each confirms the manager's fact:

| Program | programdata | deploy_slot old (pin) to new | UTC of new | sha256 old to new (prefix) | len |
| --- | --- | --- | --- | --- | --- |
| pump `6EF8rrec...` | `B5MvUwXd...` | 469811025 to **471854454** | 2026-11-20T19:28:03Z | `bfd2a2bd4a062f03` to `46bf6c8ee4587208` (full new `46bf6c8ee4587208ba682e525184f3cb2ce4fdb5e618cd0cb3b4af246492fac8`) | 10485760 unchanged |
| PumpSwap `pAMMBay6...` | `6naEzKeU...` | 469811067 to **471854493** | 2026-11-20T19:28:12Z | `78046072fdf83eee` to `92d73e652befd849` (full new `92d73e652befd8495a83af09958f333ceff7507ec102c7e6beef1edf47efa6d1`) | 10485760 unchanged |
| fees `pfeeUxB6...` | `75Uu23mq...` | 456132171 to **471854409** | 2026-11-20T19:27:54Z | `3cf9ee9a380c0ced` to `519e166ee513fc12` (full new `519e166ee513fc12254ad33d66a23ceba6ab1875320e0684dfcac918889e6868`) | 948989 unchanged |

The UTC instants are the monitor's block times (`out/s02_pins_diff.json`, written by the monitor's own `--write-pins` path at slot 472058264). `bonding_fee_config`, `pumpswap_fee_config` and `pumpswap_global_config` equal their pinned sha256 and length. Pins diff (s02, slot 472058264, 2026-11-21T07:39:17Z): exactly `programs.pump`, `programs.pumpswap`, `programs.fees` (deploy_slot, deploy_utc, sha256; data_len unchanged in all three) and `pinned_utc`, `pinned_at_slot`; no unexpected key.

Docs and IDL (s03, `out/s03_docs_idl.json`): pump-public-docs `main` is `bc357961...` (2026-11-20T21:34:02Z), one commit ahead of `639b87fb` touching only `README.md` and `docs/FEE_RECIPIENT_ROTATION.md`. The sha256 of the pump, pump_amm and pump_fees IDLs on `main` and at `639b87f` are equal to each other and to the pinned values in `tools/a3_redeploy_pins.json` (pump `7616681e...`, pump_amm `b7d8c57a...`, pump_fees `6b9fa3ec...`). So the IDLs did not move with the redeploys.
