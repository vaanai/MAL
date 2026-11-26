# A3 re-pin 1126, pumpswap_global_config only: review file

Ruling: `/data/mal/hunt-1008/ops/QP-RULING-1126-a3-global-config-repin.md` (sha256 5521b95df7487dc2…). Rule line: `DECLARATION.md` (committed at bbe05882 before any fetch). Paper only, no keys, no live sends from this PR. No seal opened, no book row read.

## Q1 item 1: the trigger record
A3 record run_utc 2026-11-26T07:10:06Z, `pins_changed` reason, verbatim as quoted in DECLARATION.md: "pumpswap_global_config sha256 a698009a419d -> a4c67d6dc665". Only halt flag; bonding_fee_config and pumpswap_fee_config pins ok; programs pump 471854454 / PumpSwap 471854493 / fees 471854409 unchanged. (The record file itself was not re-read by this worker; the text is taken from the declared rule line and the ruling.)

## Q1 item 2: byte diff (script `gc_diff.py`, output `gc_diff-output.txt`)
Committed before it was run; one finalized getAccountInfo, slot 474089869, 2026-11-26T08:59:30Z.
- old fixture `tools/fixtures/pumpswap/global_config.json`: sha256 a698009a419d0b8c56ff257626d15cba305c4989a602fa86e65ca252b2c200f4, len 949
- new: sha256 a4c67d6dc665973c16ae2b4c41400e0eb8d728414f358ea41ddd925d058ca011, len 949
- diff ranges (inclusive): exactly [[57, 312]], 256 bytes; bytes outside equal: True. BAR PASS.
- admin, lp_fee_bps 20, protocol_fee_bps 5, disable_flags 0, coin_creator_fee_bps 5, admin_set_coin_creator_authority: old == new. Only the 8 protocol_fee_recipients changed (all 8 replaced; lists in the output file).

## Q1 item 3: tx evidence (script `tx_evidence.py`, output `tx_evidence-output.txt`)
Sig 3CPjvGX4JoEZzm8jjSovbk1ACxmVCXsVhNoSE29CWzutjmWBnJJjM32EjcdNS2U4XzxLGbq8cjw8mpZp1zuHc5Yy: slot 473891616, blockTime 2026-11-25T21:08:54Z, meta.err null, log `Program log: Instruction: UpdateFeeConfig`, a PumpSwap inner instruction targets ADyA8h… (GlobalConfig), FFWtrEQ4… (admin, Squads vault) is in the account keys. BAR PASS. Not shown: that the vault is a Squads vault (taken from the ruling).

## Q1 item 4: live sends after the change
See section "Live sends (manager job)" below. Not measured by this PR.

## Q1 item 5: re-pin files
- `tools/pump_structure_pins.json`: only the `pumpswap_global_config` sha256 (len 949 unchanged); 1 line changed.
- `tools/fixtures/pump_structure_monitor/config_accounts.json`: GlobalConfig entry values[2] re-recorded by `rerecord_config_accounts.py` (fresh finalized read at slot 474089937, asserted sha == a4c67d6d…, range [57,312] vs old entry). Verified by JSON compare against the parent: only index 2 of 11 differs; `context` equal (slot 454509826 left as is).
- `tools/test_pump_structure_monitor.py` line 567: prefix a4c67d6dc665973c, comment points at config_accounts.json.
- Not touched: `tools/fixtures/pumpswap/global_config.json`, `tools/fixtures/boost_watch/global_config_account.json`, `tools/fixtures/pumpswap/t22_mayhem_accounts.json`.

### Test counts (same command at parent bbe05882^ = origin/main 6b571cdc, and at head)
- `pytest -q -p no:cacheprovider tools/test_pump_structure_monitor.py tools/test_a3_redeploy_check.py tools/test_a3_successor_checks.py tools/test_boost_slice_watch.py tools/test_h5_shadow_synthetic.py`: parent 392 passed, 1 skipped; head 392 passed, 1 skipped.
- Wider set (every tools/test_*.py mentioning pump_structure_pins, config_accounts or pump_structure_monitor, minus tools/test_r4bt_decode_evidence.py): parent 4 failed, 1418 passed, 2 skipped; head 4 failed, 1418 passed, 2 skipped. The 4 failures (tools/test_boostfloor_read.py among them) are identical at the parent, so pre-existing and not caused by this change; not investigated. tools/test_r4bt_decode_evidence.py cannot be collected in this venv (ModuleNotFoundError: certifi) at either commit.

## Q2: executor cache window (disclosure)
The executor takes GlobalConfig from `StaticCache` in `tools/probe_executor.py` (refresh 60 s, never used past `STATIC_TTL_MS` = 300 s), then `tools/pumpswap_tx.py` `fee_recipient()` picks `protocol_fee_recipients[1]`. A send could therefore have used a stale recipient and failed 6013 for at most 300 s (typically <= 60 s) after 2026-11-25T21:08:54Z, i.e. in [21:08:54Z, 21:13:54Z). The manager's sends job reports the count of sends inside that window and whether they landed. No code change is owed. This worker did not re-read those blobs; it relies on the ruling.

## Labels (from the ruling)
"PumpSwap GlobalConfig protocol-fee-recipient rotation 2026-11-25T21:08:54Z (slot 473891616, UpdateFeeConfig, bytes 57..312 only, fee bps unchanged), review ARTIFACTS/lab/a3-repin-1126-global-config/@<sha>." No re-price and no "fee model changed" label for FLOOR or any book. DEC-031 arena books bound by the account-pin hard-halt text follow their own rule; this PR does not waive it.

## Live sends (manager job)
(To be filled by the manager from `sends-count-1126.py`: landed buys/sells, slot > 473891616, distinct pools per side, failed sends with an instruction/account/constraint word, count inside [21:08:54Z, 21:13:54Z).)
