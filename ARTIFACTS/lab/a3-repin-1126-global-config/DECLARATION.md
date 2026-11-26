# A3 re-pin 2026-11-26: pumpswap_global_config only — rule line (declared before any fetch)

Ruling: `/data/mal/hunt-1008/ops/QP-RULING-1126-a3-global-config-repin.md`, sha256 `5521b95df7487dc2…` (0444). Route: QP-RULING-1121 F6 account-data-diff; DEC-035 §4 (manager resumes alone).

Trigger (A3 record run_utc 2026-11-26T07:10:06Z, `pins_changed` reason verbatim): "pumpswap_global_config sha256 a698009a419d -> a4c67d6dc665". Only halt flag; bonding_fee_config and pumpswap_fee_config pins ok; programs pump 471854454 / PumpSwap 471854493 / fees 471854409 unchanged.

Bars (fixed here, before the fetches):
1. Byte diff of the old account (`tools/fixtures/pumpswap/global_config.json`, sha256 a698009a…) vs one fresh finalized read: exactly one range [57, 312] (the 8 protocol_fee_recipients) and current sha256 a4c67d6dc665973c…. Any other range or sha = back to QP.
2. Tx evidence: slot 473891616, err null, instruction `UpdateFeeConfig`, admin FFWtrEQ4 (Squads vault).
3. Live sends after 2026-11-25T21:08:54Z (script `sends-count-1126.py`, one manager job on mal-fast-0, counts only, QP-RULING-1121 Q2 form): ≥ 3 landed buys and ≥ 3 landed sells, all landed slot > 473891616, distinct pools ≥ 2 per side, 0 failed sends with an instruction / account / constraint word; plus the count of sends inside [21:08:54Z, 21:13:54Z) (300 s StaticCache window, disclosed). Short of the bar: one keyless sim per side at the installed tree (never on open lane positions).
4. Re-pin files exactly: `tools/pump_structure_pins.json` (only the pumpswap_global_config sha256), `tools/fixtures/pump_structure_monitor/config_accounts.json` (GlobalConfig entry values[2], re-recorded by a committed script), `tools/test_pump_structure_monitor.py` line ~567 (new prefix a4c67d6dc665973c). Old-account fixtures untouched. Test pass/skip counts equal at old and new pin.

Labels: no re-price, no fee-model label for FLOOR / open sims / books (fee bps byte-identical). Disclosure: "PumpSwap GlobalConfig protocol-fee-recipient rotation 2026-11-25T21:08:54Z (slot 473891616, UpdateFeeConfig, bytes 57..312 only, fee bps unchanged), review ARTIFACTS/lab/a3-repin-1126-global-config/@<sha>". DEC-031 arena books bound by the account-pin hard-halt text follow their own rule (manager enumerates; not waived).
