"""hv4_live_c0: the compiled DEC-032 C0 canary limits, stake rule and fee pin for an HV4 arm on `tools/arena_live_bridge`. PR 1 of the HV4 live adapter.

Pure functions and constants. No network, no key, no file read, no HV4 ledger. Installs nothing. The limits are CODE CONSTANTS: a config may only
restate or tighten them (`config_refusal`), never loosen. Scope doc: ARTIFACTS/lab/c0-readiness-2026-10-25.md section 7, items 1 to 5 and 9 (PR 2 adds the frozen_sha pin, the universe check, the
sell builder pin and the sell priority escalation).

Read-record gate runbook note (QP N9): the bot's recorded read is pinned (path, st_dev, st_ino, sha256) in the bridge state at the first good check. Rewriting or
replacing that record, even with identical bytes, latches `read_record` and halts the bridge FOR GOOD; only the manager clears a latch, with the bridge stopped.
The 14-day window start is the write-once file WINDOW_DIR/<bot_id>.json (shared by dry and live of the same bot_id).

PR5 install notes (QP re-check of #965; NOT implemented here):
  N-A  no bridge or agent account may write to the reads dir; the record's sha256 can additionally be pinned outside it (e.g. in CANARY_DEC.md).
  N-B  root creates the window file, or sets chattr +i on it.
  N-C  a dry rehearsal with a real bot_id starts the 14-day clock as soon as the window dir exists (the file is shared by dry and live).
  N-D  the logged priority is the one signed last for the mint; it may not be the priority of the tx that landed (a rebroadcast of an earlier tx can land).
A window file left empty or damaged (crash) is written atomically now (tmp + fsync + link), and an existing bad one refuses with window_file_unreadable_manual_recovery.

START GATE, OWNER FILE, LIVE_OK (QP #959 removal items 3, 9 and 15; this PR):
  * `start_refusal` holds the recorded-read start gate, switched by LIVE_REFUSAL_ACTIVE. True (shipped) = every start outside the synthetic dry path refuses
    `hv4_c0_incomplete`, as before. Flipping that one constant to False is the whole removal of the refusal; it is a separate, last PR (QP item 15). The facts the
    gate needs are passed in explicitly (`record_real_pass` from `real_pass_record`, `live_gates_passed` from main), never read from module state.
  * The bridge host must have the canonical reads dir (READS_DIR_CANON) with the recorded read, root-owned and not group/other writable, and a stat-able
    SEALED_ROOT_PREFIX (a missing prefix fails every alias check closed). Carrying the record there is the install pack's job.
  * The owner confirmation is one root-owned file per bot, OWNER_DEC_DIR/<bot_id>/CANARY_DEC.md = /etc/mal-hv4-c0/<bot_id>/CANARY_DEC.md (presence + the one exact
    line; NO sha256 of it in code), and LIVE_OK is LIVE_OK_DIR/<bot_id>/LIVE_OK = /etc/mal-arena-live/hv4-c0/<bot_id>/LIVE_OK. Both files: root:root, mode exactly 0644
    (tools.h5_executor.root_file_problem, called through arena_live_bridge.root_ok_problem), in a root-owned per-bot directory that is not group/other writable
    (0755), a regular file with link count 1. TIER stays shared. Two canaries also need their own wallet, state_dir, parity_dir, twins_file and tieout_dir in config.
  * OWNER FILE PIN (DEC-031 Am.21 item 4, QP #1100 comment 6527456651): at the first good check a LIVE hv4_c0 bridge pins the owner file's (path, st_dev, st_ino,
    sha256) in its state (`owner_pin`), as the read record is pinned (`read_pin`, QP N9). A later rewrite (other bytes, same inode), replacement (new inode, even
    identical bytes) or removal makes `owner_line_check` return OWNER_LINE_CHANGED and the bridge latches `owner_line`: a hard latch, new buys only, exits continue,
    it survives a restart, and only the manager clears it with the bridge stopped, by removing BOTH `latches.owner_line` and `owner_pin` from the state file (the
    next good check pins the file that is there then). A restart of a pinned bridge is not refused by main() for a changed owner file, so the bridge can run exits.
    Before the first good check a missing file is as before: entries DRY, and main() refuses a live start. A DRY bridge never pins or latches.

Arms: only F or X arms are canary candidates (the L arms rewrite their playbook nightly = a prompt change = a new bot_id under DEC-032 s1.4). A bot_id
ending in -L, -P or -R is refused.

Stake: C0 stake S = min(card stake, 0.05 SOL) regardless of the HV4 size (0.25 / 0.5 / 1.0 in `hv4_arms.SIZES`, which are SOL stakes on the paper card).
The paper twin is priced at the same S.

Fee: the pin is the compiled default 55,000 buy / 55,000 sell / 150,000 escalated priority (manager decision 2026-10-25: C0 uses the default, not
p155; it equals h5_executor.H5_DEFAULT and a test asserts that). A config naming any other fee is refused.

PR 5a notes (still `hv4_c0_incomplete`: nothing here removes the live refusal).
  * WINDOW FILE RECOVERY is manual. `window_file_unsafe` (the write-once window file is a link, hard-linked, not a regular file or has the wrong owner/mode)
    and `window_file_unwritable` (including a stale `.<bot_id>.<pid>.tmp` left by a crashed start) stop the start; the refusal text says MANUAL RECOVERY. The
    bridge never deletes or recreates the file; the owner restores the ORIGINAL first_start_ms. A tmp this process created is unlinked on a write/fsync failure.
  * HALT TIMING: in practice the feed halt fires at the forwarder's own `stalled` state (2,000 ms, QP), before the bridge's 5,000 ms frame bound; the bridge bound is
    the backstop for a forwarder that stops updating its status.
  * PRECONDITION 7 IS NOT MET FOR TAPE GAPS. What is built is the CONSUMER: the `feed_break` latch (hard), its clear (10 minutes counted from the END of the gap AND a
    root-owned review file naming EXACTLY the newest gap instant) and the recorder's `gap` marking. The PRODUCER of the book's own tape gap records (data_loss,
    process_restart, feed_restart, start, takeover, gap_silence_ms breaches): the frozen HV4 runner (9ed4c5ff) emits none and must not change, so a separate read-only
    relay, tools/hv4_gap_relay.py (kind and instant only), is built in this PR but NOT installed or carried to the bridge host, and its slot-jump (gap_slots) rule is not
    covered. Until it runs, `feed_break` is fed only by the forwarder's TRANSPORT gap log
    (stall, disconnect, reconnect, mismatch, error, torn tail) and a forwarder `stopped_mismatch`; that is a stricter extra guard, not the §5.2 halt.
  * `transport_stall:<why>` refuses new buys while the forwarder status is not live (no latch; clears by itself; an extra guard). `a3_pins_changed` latches on any
    A3 halt flag other than synthetic_share_high (DEC-032 Am.3: that one is decided by the pooled guard's a3-applied.jsonl, see a3_problem), an unreadable/stale record, or a missing record file in live (cleared only by the manager). Exits keep running under all of them.
  * RUNNER LIVENESS: `runner_silent` is INERT for buys in practice (the entry row that arrives resets it before it is judged; a stuck runner's old rows are refused by
    MAX_DECISION_AGE_MS). A dead runner is handled by the bridge's own time-stop and sl backstops, which need no runner row (DeadRunnerTests). Owed in the install pack:
    a host-side dead-man on the runner job and on SCAN-log `tick_timing` staleness, and a C0 read that flags every backstop-closed trade during runner silence.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import stat
from pathlib import Path
from typing import Any, Iterable, Mapping

PROFILE = "hv4_c0"
TIER_NAME = "T1"  # the TIER file must read exactly this for a C0 start (as the arena H5 canary)

C0_LIMITS: dict[str, int] = {  # DEC-032 s3 C0 column, lamports. total_loss_lamports counts committed loss (realized loss plus open stakes).
    "stake_lamports": 50_000_000,
    "max_open": 2,
    "max_trades_per_day": 30,
    "daily_loss_lamports": 100_000_000,
    "total_loss_lamports": 200_000_000,
}
FUNDED_MIN_LAMPORTS, FUNDED_MAX_LAMPORTS = 350_000_000, 1_000_000_000
FLOOR_BELOW_FUNDED_LAMPORTS = 250_000_000
MIN_WALLET_FLOOR_LAMPORTS = 50_000_000
BASE_FEE_LAMPORTS = 5_000
LAMPORTS_PER_SOL = 1_000_000_000
HV4_SIZES = (0.25, 0.5, 1.0)  # mirrors tools/hv4_arms.SIZES (a test compares them; not imported so this module stays light)

PRIORITY_PIN: dict[str, Any] = {"profile": "default", "buy": 55_000, "sell": 55_000, "escalated": 150_000}
# Reserves derived from the pin (base fee on top of each send): one trip = buy + one escalated sell; an open position keeps one escalated sell reserved.
TRIP_FEE_RESERVE_LAMPORTS = (PRIORITY_PIN["buy"] + BASE_FEE_LAMPORTS) + (PRIORITY_PIN["escalated"] + BASE_FEE_LAMPORTS)
SELL_FEE_RESERVE_LAMPORTS = PRIORITY_PIN["escalated"] + BASE_FEE_LAMPORTS

FROZEN_SHA = "9ed4c5ff63935696b2a89150e13565e123047754"  # the HV4 runner checkout the decision code runs at; the `start` row must repeat it (else latch frozen_sha_mismatch)
UNIVERSE_V_RANGE = (17_500_000_000, 17_700_000_000)  # lamports; same band as tools/arena_live_bridge.UNIVERSE_V_RANGE (a test compares them)
WSOL_MINT = "So11111111111111111111111111111111111111112"
SKIP_UNIVERSE_LABELS = ("universe_v_out_of_range", "quote_not_wsol")  # a universe skip is a SKIP (logged `universe_skip`), never a cap or a refusal count

# The sell + close of the token ATA in ONE transaction is built by the unmodified probe_live.LiveExecutor._start_sell (tx.sell_instructions: budget,
# WSOL ATA, PumpSwap sell, WSOL close; then probe_live.close_token_account(base_ata, user, base_tp) with base_tp = the mint's own token program).
# Pinned by sha256 of the source of each piece AND of every callee that shapes the transaction: the pumpswap_tx helpers (CALLEES_PTX: sell_data, ata,
# pda, create_ata_idempotent, sync_native, close_account, compute_budget_ixs, priority_price_for_total, swap_accounts and its PDA helpers,
# min_out_with_slippage), the pumpswap_tx program/discriminator/CU constants, and probe_live's signing guard (_sign, validate_message).
# `sell_pin_problem` recomputes them; it is checked at STARTUP only (a live hv4_c0 start refuses on any mismatch), not per send: code is not edited
# under a running process, and a changed tree needs a restart, which re-checks. A decode test is the backstop for what the pin cannot see.
# The file sha of probe_live.py is NOT pinned (an unrelated probe edit must not break the canary).
CALLEES_PTX = ("sell_instructions", "sell_data", "ata", "pda", "create_ata_idempotent", "sync_native", "close_account", "compute_budget_ixs",
               "priority_price_for_total", "swap_accounts", "_m", "creator_vault_authority", "event_authority", "fee_config", "pool_v2", "min_out_with_slippage")
CALLEES_PL = ("close_token_account", "validate_message")  # module-level functions of tools/probe_live.py
CONSTANTS_PTX = ("PUMPSWAP_PROGRAM", "FEE_PROGRAM", "GLOBAL_CONFIG", "WSOL_MINT", "TOKEN_PROGRAM", "TOKEN_2022_PROGRAM", "ATA_PROGRAM", "SYSTEM_PROGRAM",
                 "PUMP_PROGRAM", "DISC_SELL", "DEFAULT_SELL_CU_LIMIT")
SELL_BUILDER_PIN: dict[str, str] = {
    "path": "tools/probe_live.py", "symbol": "LiveExecutor._start_sell",
    "symbol_sha256": "a1931a785fe17f130aa18751229e1f7ee2358e9678a3626c7757b8b992deb57e",
    "_sign_sha256": "7cd13e70d5b27dd80c960b412de1b4ea8ae9cd48a95f8c4541677ff7fc9b6e5c",
    "close_token_account_sha256": "93119b2ea159bcb9defcb5e7d2424a5635df432f7ffc9b18d07355d2f364895a",
    "validate_message_sha256": "13e59229628a516a63e5439228bebb9b11cab4c69b6c3db4eb6b04ac72ce487b",
    "ptx.sell_instructions_sha256": "ad40960ef8cc18260f0682cf83989be8ab36f7366354cada1a84367194757eeb",
    "ptx.sell_data_sha256": "78fa481df272f366949b4d5e3d75e882ded7e7a7cbbf57ec9985cd3b94c6ac97",
    "ptx.ata_sha256": "c52d59af62f7820795964c0388b8c3780805ced454aa301bcc5a3b1823a64854",
    "ptx.pda_sha256": "b7296ab8ade148933f9684036d10ffaa2b9e713fb8422cd900fd77f0167532f3",
    "ptx.create_ata_idempotent_sha256": "8a242b5d7bb266d3b3f7e847d78f14d8b46a48971bf7917c59d0dddb86e5522e",
    "ptx.sync_native_sha256": "42c0ed957f2b7df26c6d76eb3f1c96fdb7e350f59e0149b8f371fd32bd6315f6",
    "ptx.close_account_sha256": "cd6c205f3612bdd1409ad6d1e634b0a6409c5f2a6bbff70033271f9962eb3756",
    "ptx.compute_budget_ixs_sha256": "0de51d40730bd5199a56d7bde0f8f291961cb6813b4a665de5119717461a969c",
    "ptx.priority_price_for_total_sha256": "2c9a5e350f7de92a43e6100e836175502600deadb833754eef90c481da3e286d",
    "ptx.swap_accounts_sha256": "88c8d26585820fe3799cf7859128b0236b0d07b3dc4512d1ec2b491e768ad090",
    "ptx._m_sha256": "ccac4adaa98ed9c14820cb6f8414e25d1296b188c159e2c32c9824875cb5e279",
    "ptx.creator_vault_authority_sha256": "ee4e434cdda7771de94cdb1eb6dc739258cf37a54f84b97ba06492ba65bf2ec2",
    "ptx.event_authority_sha256": "f1ad7f6331bd48abd3189647c4df915f1a34d03f5eacf720df725a0ec8b9ac8e",
    "ptx.fee_config_sha256": "6a7ab50d4dc406713f471e492c212ee9b05312b5ef7a5e9eb488448a878434de",
    "ptx.pool_v2_sha256": "28a04b8884d924854d997a7fbcad0c6aac8f36644ca52f6f2bf78a41fa81a40f",
    "ptx.min_out_with_slippage_sha256": "0573c7e1c465a744cbd3d843f6271a109c04f2e21e64bb36d3636e2e71a1c49f",
    "ptx.constants_sha256": "e2b66274132a760ee6d440ae8cbd03294bb0ba29f97a0d8b6aecd2b286dbe79c",
    "pe.exit_check_sha256": "7dcd289631cfe0418439df90f4b0901518d813dfd474f0cb2a6c660e3a51eb00",  # PR 3: the quote_out that min_out is a share of
    "c0.ladder_sha256": "06123a941d5a842fe9eccaed4277b898fe8c88ba7681c0feee5f059b397f3233",  # PR 3: the compiled 0.85 / 0.65 rungs and their escalation point (this module's own ladder source)
    # PR 5a (QP removal precondition 2): the exit snapshot builder and the decoders / quote helpers it calls
    "pe.fetch_snapshot_sha256": "4eca167a0f3c4fcd3e1d1a53cdab00fa4f7a183c3895cf1caca2ec9c666de4c5",
    "pe.entry_quote_sha256": "5d231ffbfb421cfc00c6208d45a3cf3f7be3284c37c12f0ee9b290fdc8741582",
    "pe.fee_ppm_for_sha256": "0417a9705faf7613bfd59cf623da1aa6e0ce7b0b910c61f8625ab482a8a67ca8",
    "pe.drift_vs_seed_sha256": "4021191cb90890acbd2f88f1ac2d555c968e493b985ca6b86f0f90169a5a34db",
    "pe.Snapshot.quote_priced_sha256": "efc1086ec946039fdeb6fc36625256037ed4eeffb66008f1c21766743ab077cb",
    "pe.StaticCache.get_sha256": "36048129769ae36c91d2f7f02eef0603c0fa9d3ff36e9b799bc0ba15f15b8213",
    "pe.StaticCache.put_sha256": "2a5c113f7be7b6833bd58df43fc2d91a493fb837ee9bf65bc3c93208041ed3af",
    "pe.StaticCache.refresh_sha256": "667e8dc300b1bd4a91848f5014433dedf6a9a751221a059a9a502d224021a6f7",
    "pe.Executor._snapshot_sha256": "b28fde5ad695f8844779ca1367ee8f4cf98ee414d1acfc51181a5141e0852d66",
    "pe.Executor._batched_exit_snapshots_sha256": "79ef509391ec9507c2cdfd6412cb9dfd2981951b50400471546555ae36094490",
    "pe.Executor._batch_stale_sha256": "e5424c20aa1befd27ecf5011b55d7cd748e4e7d9934d45140dc1495b91c6c7de",
    "pe.Executor._exit_snapshot_sha256": "a89e0d70519630cd5644e5a538e10deabb4588502b35a983f4ce68dae33607a6",
    "pe.Executor._batch_err_sha256": "563007db5c3aed08681bce766ef45813143384223a9231dbbfcfc7e193648a0c",
    "pe.Executor.poll_positions_sha256": "d050b315227c996d7832ececcd176cc2b0c4a581b9579fd5e972effc62c0cf17",
    "pe.constants_sha256": "55cb78be6737a4e4fef4f1eec0baca8fd2f15af18a983b609b7cf55239e4171d",
    "x.pumpswap_simulate._b64_sha256": "8f0715ae3b80b9cbd49d4bac7031bc47f8f794ace4c4b84efdbaa6c92d571263",
    "x.pumpswap_simulate.token_amount_sha256": "98738db24cacf2ed0addbbcc3e1d429090c97164169f42778f7467b2d415f36b",
    "x.pumpswap_tx.parse_pool_account_sha256": "3224308a1e95ba96128331817eb423b49099e6a414f88600cdb08bf408771f64",
    "x.pumpswap_tx.parse_global_config_sha256": "c7cfdbb98d3536d23ddbdc9416464986e6efbfff849bda53207bd4be9a6dfa1a",
    "x.pumpswap_tx.pool_state_from_accounts_sha256": "5b33bef7ba3782bc676f6d6f082cbdd12036ffdb2368560fcc43c710b19a17c6",
    "x.pumpswap_tx.PoolState_sha256": "a9af1c9cf6f1238eedf063cfd72f8b339263004922b48124c0c712e8d04a7d48",
    "x.pumpswap_tx.cp_buy_out_sha256": "9ffbd3c9b5347bdd52856f409a12c8a18999fbb98c98d3777b5f4e33d854c07e",
    "x.pumpswap_tx.cp_sell_out_sha256": "68b3fd36ddd0bbd9af359201b3af74bb6166021e60c8477ce992b73a4e51e7ce",
    "x.pumpswap_tx.canonical_pool_sha256": "5033af0e9b5a5ecf82f90fc5f6d1d788858dd114f2a6abb3e4854f96e5067e1d",
    "x.paper_curve_math.pumpswap_sol_fee_ppm_sha256": "79ffb58c440ea0fd26e89fb3f714936212e05dae693d517cb312c6a7aa5ab0cb",
    "x.paper_curve_math.market_cap_sol_sha256": "be7992d9ab49d48969617a8770444880f9e824890e6930dc69c92836c850760e",
    "x.paper_curve_math.spot_sol_per_ui_sha256": "9f3f81e0175d809c659de6e13915ee57031080646919de3def07af03a118dbf5",
    "x.paper_curve_math.reserves_with_our_buy_sha256": "4af4a1a8b790c3c9d069d6d541f4e69fc1e7e289f7031221490a35852f8cc052",
    "x.probe_executor.Snapshot_sha256": "91e9dc9a404e80d7c8e8026ebbf06eccf4f1082cfffbe6d80ac678c562cd5e7d",
    "x.probe_executor.check_sell_sha256": "91cd1bc48470e8fff032d5194c20c10ec3374de49e9552fd52e4138988c34e31",
    "x.probe_executor.check_halt_file_sha256": "04cf031902a941ef7797a0c183db4aeb2c08ebfa98e79b0553c69abc0ce94817",
    "x.probe_executor.Executor._prio_sha256": "4795b5b6c8d819de85779dbf66bb1e5a8996d712643105c86d1cede92fd2197a",
    "x.probe_executor.Executor._exit_prio_sha256": "fd3448f1bc88d4c4cee0396403f6ae0fc371e742a2cb7c2c8993cb28436833d6",
    "x.pumpswap_tx.parse_virtual_signed_sha256": "2636cf5c1ce25d493d96aa5a19646870f2af9245eb17ba330e6313dd4d4ceff7",
    "x.pumpswap_tx.global_volume_accumulator_sha256": "6640e41412852a01e05ca9bf304671b9c461cf78a53f4b7f29174f68eb725aea",
    "x.pumpswap_tx.user_volume_accumulator_sha256": "36da371bf169495396c3482111e1795f0f617e2951a947d8f12cdd9d123f33f3",
    "x.pumpswap_tx.fee_recipient_sha256": "930899fddb8b24e796bce00b5044322c9aad6a8903ef952b5592898782c8ed03",
    "x.pumpswap_tx.pool_is_mayhem_sha256": "74d043fcf2cd07d136f0d13eb5785105210e5060faeebbdafb745280d59923d7",
    "x.probe_executor.critical_sha256": "5202b98f88c7fd5999bb29e592f79a6909d449f05079084238871d7ccfea8f47",
    "x.probe_live.LiveExecutor._send_sha256": "0c8f34a313e985b85788ee94425ff32bfdf56e5fa1913eaf8ac9528bc567b91c",
    "x.probe_live.LiveExecutor._exit_candidates_sha256": "b77a23adb146cf51b559126431d1991fe7da477006312e7812736ce88ea03f37",
    "bridge.poll_positions_sha256": "b52b3d4778e16d0619c9749d186aa36361e70916372ffe932e2c3051ef87524d",
    "bridge.refresh_warm_sha256": "c3b7ced0776059708aa75d8dbc134149dbd8ed0eae27a1984e3a533f7b25c8f8",
}

# QP removal precondition 2 (#959): the EXIT SNAPSHOT builder is inside the pin too. min_out is a share of the quote that the snapshot yields, so the batched, single and
# full-fetch paths, the quote and the cached V rule, the two age bounds and the bridge's poll_positions / refresh_warm all shape the number that is signed.
PE_FUNCS = ("fetch_snapshot", "entry_quote", "fee_ppm_for", "drift_vs_seed")  # module-level functions of tools/probe_executor.py
PE_METHODS = (("Snapshot", "quote_priced"), ("StaticCache", "get"), ("StaticCache", "put"), ("StaticCache", "refresh"),
              ("Executor", "_snapshot"), ("Executor", "_batched_exit_snapshots"), ("Executor", "_batch_stale"), ("Executor", "_exit_snapshot"),
              ("Executor", "_batch_err"), ("Executor", "poll_positions"))
PE_CONSTANTS = ("EXIT_RULE", "UNPRICED_GRACE_MS", "BATCH_MAX_AGE_MS", "STATIC_TTL_MS", "STATIC_REFRESH_MS", "BATCH_ERR_LOG_EVERY_MS", "V_SEED_LAMPORTS", "V_SEED_TOLERANCE")
# Callees that DECODE the pool / vault / global-config data and compute the quote that min_out is a share of (reviewer + QP on #969), and the helpers the snapshot
# paths call. Spec "module:qualname" (a function, a class or a method); key `x.<module>.<qualname>_sha256`. tools/test_hv4_live_c0_pr5a.py walks the call graph
# of every pinned function and fails on a tools.* callee that is neither pinned nor on its reviewed allowlist.
PIN_EXTRA = ("pumpswap_simulate:_b64", "pumpswap_simulate:token_amount",
             "pumpswap_tx:parse_pool_account", "pumpswap_tx:parse_global_config", "pumpswap_tx:pool_state_from_accounts", "pumpswap_tx:PoolState",
             "pumpswap_tx:cp_buy_out", "pumpswap_tx:cp_sell_out", "pumpswap_tx:canonical_pool",
             "paper_curve_math:pumpswap_sol_fee_ppm", "paper_curve_math:market_cap_sol", "paper_curve_math:spot_sol_per_ui", "paper_curve_math:reserves_with_our_buy",
             "probe_executor:Snapshot", "probe_executor:check_sell", "probe_executor:check_halt_file",
             "probe_executor:Executor._prio", "probe_executor:Executor._exit_prio",
             "pumpswap_tx:parse_virtual_signed", "pumpswap_tx:global_volume_accumulator", "pumpswap_tx:user_volume_accumulator",
             "pumpswap_tx:fee_recipient", "pumpswap_tx:pool_is_mayhem",
             "probe_live:LiveExecutor._send", "probe_live:LiveExecutor._exit_candidates",
             "probe_executor:critical")  # critical: the @pe.critical wrapper around _start_sell (inspect.getsource unwraps it, so it needs its own key)
BRIDGE_METHODS = ("poll_positions", "refresh_warm")  # BridgeExecutor, nested in make_live_port: read by AST from the file on disk


def _fget(o: Any) -> Any:
    return o.fget if isinstance(o, property) else o


def extra_key(spec: str) -> str:
    return "x." + spec.replace(":", ".") + "_sha256"


def resolve_spec(spec: str) -> Any:
    """'module:qualname' under tools/ -> the live object (function, class or method)."""
    import importlib
    mod, qual = spec.split(":")
    obj: Any = importlib.import_module(f"tools.{mod}")
    for part in qual.split("."):
        obj = getattr(obj, part)
    return obj


def bridge_method_source(name: str, path: str | None = None) -> str:
    """Source text of `BridgeExecutor.<name>` (a class nested inside make_live_port), taken from the bridge file by AST. Raises KeyError when it is absent."""
    import ast
    src = Path(path or Path(__file__).with_name("arena_live_bridge.py")).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name == "make_live_port":
            for cls in ast.walk(fn):
                if isinstance(cls, ast.ClassDef) and cls.name == "BridgeExecutor":
                    for m in cls.body:
                        if isinstance(m, ast.FunctionDef) and m.name == name:
                            seg = ast.get_source_segment(src, m)
                            if seg is not None:
                                return seg
    raise KeyError(name)


REFUSED_ARM_SUFFIXES = ("-L", "-P", "-R")
ALLOWED_ARM_SUFFIXES = ("-F", "-X")
_CONFIG_FORBIDDEN = ("sell_priority_lamports", "escalated_priority_lamports")


ESCALATE_AFTER_REVERTS = 2  # DEC-032 s3: "after 2 reverts or a time bound"
ESCALATE_AFTER_MS = 345_000  # the time bound: s3 names none; C0 takes h5_executor.ESCALATE_S (345 s) from the first sell attempt, a declared choice


def sell_escalated(sell_attempts: int, stuck: bool = False, elapsed_ms: int | None = None) -> bool:
    """The ONE escalation point of a C0 sell (DEC-032 s3): after 2 reverts/expiries, or ESCALATE_AFTER_MS after the first sell build, whichever comes
    first; a stuck position is escalated too (a backstop). The priority and the min_out ladders both read this, so they step together."""
    late = elapsed_ms is not None and int(elapsed_ms) >= ESCALATE_AFTER_MS
    return bool(stuck or late or int(sell_attempts) >= ESCALATE_AFTER_REVERTS)


def sell_priority(sell_attempts: int, stuck: bool = False, elapsed_ms: int | None = None) -> int:
    """Priority lamports for a sell, aligned with DEC-032 s3: 55,000 until the sell has reverted/expired twice (sell_attempts >= 2) or ESCALATE_AFTER_MS
    have passed since the first sell attempt (elapsed_ms), whichever comes first; then the escalated 150,000. A stuck position is escalated too
    (stuck needs more attempts than 2, so it is a backstop). The first retry after ONE revert stays at 55,000."""
    return PRIORITY_PIN["escalated"] if sell_escalated(sell_attempts, stuck, elapsed_ms) else PRIORITY_PIN["sell"]


# DEC-032 s3 sell rule: min_out = 0.85 of the quote at the first build; 0.65 at the escalation point (higher priority); NEVER 0. Compiled constants, in bps of
# the quote that must still be received (the executor's `slip_bps` = 10,000 minus these). Nothing in config can reach them (see `_CONFIG_FORBIDDEN`).
SELL_MIN_OUT_KEEP_BPS: dict[str, int] = {"first": 8_500, "escalated": 6_500}
# DECLARED RESIDUAL (DEC copy): the pinned builder computes quote_out * keep // 10000, so a quote_out of exactly 1 lamport gives min_out 0 at the 0.85 rung. Dust only; the pinned builder is not touched.
SELL_MIN_OUT_FLOOR_BPS = SELL_MIN_OUT_KEEP_BPS["escalated"]  # no sell is ever built below 0.65 of the quote


def sell_keep_bps(sell_attempts: int, stuck: bool = False, elapsed_ms: int | None = None) -> int:
    """Share of the quote (bps) the sell must still receive: 8,500 until the escalation point, then 6,500. Never below SELL_MIN_OUT_FLOOR_BPS, never 0."""
    return SELL_MIN_OUT_KEEP_BPS["escalated" if sell_escalated(sell_attempts, stuck, elapsed_ms) else "first"]


def sell_slip_bps(sell_attempts: int, stuck: bool = False, elapsed_ms: int | None = None) -> int:
    """The `slip_bps` the pinned sell builder takes (`tx.min_out_with_slippage(quote_out, slip_bps)`): 1,500 first, 3,500 escalated."""
    return 10_000 - sell_keep_bps(sell_attempts, stuck, elapsed_ms)


def ladder_pin_sha256() -> str:
    """sha256 of the ladder's compiled source and constants. It sits inside the sell pin (SELL_BUILDER_PIN['c0.ladder_sha256']): editing a rung is a reviewed change."""
    src = "".join(inspect.getsource(f) for f in (sell_escalated, sell_keep_bps, sell_slip_bps)) + repr((SELL_MIN_OUT_KEEP_BPS, ESCALATE_AFTER_REVERTS, ESCALATE_AFTER_MS))
    return hashlib.sha256(src.encode()).hexdigest()


# --- the paper twin (DEC-032 s5 "Twin comparator"): arena_twin_writer re-prices the book's paper position at S, nofail leg, 1.9 s, E1 excluded, at the fees
# the trip actually sent (landed buy fee; landed sell fee plus earlier failed sells, escalated resends included). The pins below are what its run record must carry.
HV4_STACK_PIN = {"e1_floor_per_005": 384022, "exit_lag_s": 0.55, "feed_delay_slots": 7, "guard": 1.15, "h_in": 0.002608, "h_out": 0.0016, "k_rounding": "ceil",
                 "portal_ppm": 0, "press_intercept_provisional": -1.4197242838379789, "rent": 0, "send": 505000, "strand_rule": "both"}  # StackConfig.as_dict() at 9ed4c5ff, delay 7
CONFIG_PIN_SHA256 = "1e611a5a636dfe6bd563ab5258f6b498cdb8906500ee8bf2f0264a2621621986"  # sha256 of HV4_STACK_PIN, sort_keys, compact separators (ARTIFACTS/lab/hv4-registration-1023.md 2.3)
TWIN_WRITER_SHA256 = "1e9cb00e08ec69df269b19b9774d02feed7d3e91a61c7f727b7abbdc5fe560bb"  # sha256 of tools/arena_twin_writer.py (the same writer the H5 arena canary pins)


def stack_sha256(stack: Mapping[str, Any]) -> str:
    """The twin writer's `stack_hash` rule: canonical JSON of the stack block without the per-position `sps_*` slot rates."""
    core = {k: v for k, v in stack.items() if not str(k).startswith("sps_")}
    return hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def config_pin_problem(stack: Any) -> str | None:
    """None when `stack` hashes to the registered HV4 config pin (every key, value for value)."""
    try:
        return None if isinstance(stack, Mapping) and stack_sha256(stack) == CONFIG_PIN_SHA256 else "config_pin_mismatch"
    except (TypeError, ValueError):
        return "config_pin_mismatch"


def twin_pins() -> tuple[str, str]:
    """(config_hash, code_sha256) the twin writer's run record must carry for a C0 book."""
    return CONFIG_PIN_SHA256, TWIN_WRITER_SHA256


def fees_sent(buy_sends: int, sell_priorities: Iterable[int]) -> tuple[int, int]:
    """The twin's per-trip fees from the schedule actually sent: (buy fee, sell fee). A send costs its priority plus the 5,000 base. `buy_sends` is 1 for the
    landed buy (earlier failed buy sends are not part of the trip, as arena_twin_writer's FEE_BASIS says). Every sell send counts, escalated resends included."""
    return buy_sends * (PRIORITY_PIN["buy"] + BASE_FEE_LAMPORTS), sum(int(p) + BASE_FEE_LAMPORTS for p in sell_priorities)


def universe_problem(quote_mint: Any, v_lamports: Any) -> str | None:
    """WSOL quote and stored V inside the band. Labels are in SKIP_UNIVERSE_LABELS. At the frozen sha 9ed4c5ff, hv4_cards `filters` apply the same
    v_band [17.5e9, 17.7e9] (inclusive) and a separate `v_missing`, so the canary universe equals the read's. One residual difference: a missing V is
    `v_missing` in HV4 but `universe_v_out_of_range` here (also a logged skip)."""
    if str(quote_mint) != WSOL_MINT:
        return "quote_not_wsol"
    if isinstance(v_lamports, bool) or not isinstance(v_lamports, (int, float)) or not UNIVERSE_V_RANGE[0] <= v_lamports <= UNIVERSE_V_RANGE[1]:
        return "universe_v_out_of_range"
    return None


def frozen_sha_refusal(cfg: Mapping[str, Any]) -> str | None:
    return None if cfg.get("frozen_sha") == FROZEN_SHA else "frozen_sha_not_pinned"


def _sell_pin_items() -> dict[str, Any]:
    """name -> callable returning the sha256 of the current code. Imports the solana stack."""
    from tools import probe_live as pl
    from tools import probe_executor as pe
    from tools import pumpswap_tx as ptx
    sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
    items: dict[str, Any] = {"symbol_sha256": lambda: sha(inspect.getsource(pl.LiveExecutor._start_sell).encode()),
                             "_sign_sha256": lambda: sha(inspect.getsource(pl.LiveExecutor._sign).encode())}
    for n in CALLEES_PL:
        items[f"{n}_sha256"] = (lambda n=n: sha(inspect.getsource(getattr(pl, n)).encode()))
    for n in CALLEES_PTX:
        items[f"ptx.{n}_sha256"] = (lambda n=n: sha(inspect.getsource(getattr(ptx, n)).encode()))
    items["pe.exit_check_sha256"] = lambda: sha(inspect.getsource(pe.exit_check).encode())
    for n in PE_FUNCS:
        items[f"pe.{n}_sha256"] = (lambda n=n: sha(inspect.getsource(getattr(pe, n)).encode()))
    for cn, mn in PE_METHODS:
        items[f"pe.{cn}.{mn}_sha256"] = (lambda cn=cn, mn=mn: sha(inspect.getsource(_fget(getattr(getattr(pe, cn), mn))).encode()))
    items["pe.constants_sha256"] = lambda: sha(repr([(n, getattr(pe, n)) for n in PE_CONSTANTS]).encode())
    for spec in PIN_EXTRA:
        items[extra_key(spec)] = (lambda spec=spec: sha(inspect.getsource(_fget(resolve_spec(spec))).encode()))
    for n in BRIDGE_METHODS:
        items[f"bridge.{n}_sha256"] = (lambda n=n: sha(bridge_method_source(n).encode()))
    items["c0.ladder_sha256"] = ladder_pin_sha256
    items["ptx.constants_sha256"] = lambda: sha(repr([(n, getattr(ptx, n)) for n in CONSTANTS_PTX]).encode())
    return items


def sell_pin_problem(pin: Mapping[str, str] | None = None) -> str | None:
    """Recompute SELL_BUILDER_PIN from the code on disk. None when all match; else the first item that differs (also an item missing from the pin).
    Imports the solana stack, so a dry bridge never calls it. Startup check only."""
    pin = pin or SELL_BUILDER_PIN
    for k, fn in _sell_pin_items().items():
        try:
            if fn() != pin.get(k):
                return f"sell_builder_pin_mismatch:{k}"
        except (OSError, TypeError, AttributeError, KeyError, SyntaxError):
            return f"sell_builder_pin_unreadable:{k}"
    return None


def arm_refusal(bot_id: Any) -> str | None:
    """Only an F or X arm. Anything ending -L, -P or -R, or not ending -F/-X, is refused."""
    if not isinstance(bot_id, str) or not bot_id.startswith("hv4-"):
        return "arm_not_hv4"
    if bot_id.endswith(REFUSED_ARM_SUFFIXES):
        return "arm_not_canary_candidate"
    return None if bot_id.endswith(ALLOWED_ARM_SUFFIXES) else "arm_not_f_or_x"


def funded_refusal(funded: Any) -> str | None:
    if funded is None:
        return "funded_lamports_missing"
    if isinstance(funded, bool) or not isinstance(funded, int) or not FUNDED_MIN_LAMPORTS <= funded <= FUNDED_MAX_LAMPORTS:
        return "funded_lamports_out_of_range"
    return None


def wallet_floor_lamports(funded: int, configured: int = 0) -> int:
    """max(configured, 0.05 SOL, funded - 0.25 SOL): config can raise the floor, never lower it."""
    return max(int(configured), MIN_WALLET_FLOOR_LAMPORTS, funded - FLOOR_BELOW_FUNDED_LAMPORTS)


def priority_refusal(cfg: Mapping[str, Any]) -> str | None:
    """The pin is compiled. A config may restate it (`priority_pin`, `buy_priority_lamports`) and nothing else."""
    decl = cfg.get("priority_pin")
    if decl is not None and decl != PRIORITY_PIN:
        return "priority_pin_mismatch"
    for k in _CONFIG_FORBIDDEN:
        if k in cfg:
            return f"{k}_not_configurable"
    if "buy_priority_lamports" in cfg and cfg["buy_priority_lamports"] != PRIORITY_PIN["buy"]:
        return "buy_priority_lamports_not_pin"
    if "priority_lamports" in cfg and cfg["priority_lamports"] != PRIORITY_PIN["buy"]:
        return "priority_lamports_not_pin"
    return None


def config_refusal(cfg: Mapping[str, Any], live: bool) -> str | None:
    """The first reason a C0 config is refused, else None. `funded_lamports` is required when live."""
    why = arm_refusal(cfg.get("bot_id")) or frozen_sha_refusal(cfg) or priority_refusal(cfg)
    if why:
        return why
    if live or cfg.get("funded_lamports") is not None:
        return funded_refusal(cfg.get("funded_lamports"))
    return None


def guard_problem() -> str | None:
    """Tripwire run at a C0 start: the compiled constants are still at or inside DEC-032 s3. None when they hold; it only refuses."""
    spec = {"stake_lamports": 50_000_000, "max_open": 2, "max_trades_per_day": 30, "daily_loss_lamports": 100_000_000, "total_loss_lamports": 200_000_000}
    for k, cap in spec.items():
        v = C0_LIMITS.get(k)
        if isinstance(v, bool) or not isinstance(v, int) or not 0 < v <= cap:
            return f"c0_guard:{k}"
    checks = (
        ("funded_range", 350_000_000 <= FUNDED_MIN_LAMPORTS <= FUNDED_MAX_LAMPORTS <= 1_000_000_000),
        ("floor_below_funded", 0 < FLOOR_BELOW_FUNDED_LAMPORTS <= 250_000_000),
        ("tier", TIER_NAME == "T1"),
        ("priority_pin", (PRIORITY_PIN["buy"], PRIORITY_PIN["sell"], PRIORITY_PIN["escalated"]) == (55_000, 55_000, 150_000)),
        ("fee_reserve", TRIP_FEE_RESERVE_LAMPORTS == 215_000 and SELL_FEE_RESERVE_LAMPORTS == 155_000),
        ("frozen_sha", isinstance(FROZEN_SHA, str) and len(FROZEN_SHA) == 40 and FROZEN_SHA.startswith("9ed4c5ff")),
        ("daily_le_total", C0_LIMITS["daily_loss_lamports"] <= C0_LIMITS["total_loss_lamports"]),
    )
    for name, ok in checks:
        if not ok:
            return f"c0_guard:{name}"
    return None


def live_ok_problem(text: str, bot_id: str) -> str | None:
    """LIVE_OK names the bot_id (exactly one line)."""
    lines = text.strip().splitlines()
    return None if len(lines) == 1 and lines[0].strip() == bot_id else "live_ok_other_book"


def c0_stake(row: Mapping[str, Any], cap_lamports: int = C0_LIMITS["stake_lamports"]) -> tuple[int | None, dict[str, Any]]:
    """(stake lamports or None, mapping record). S = min(card stake, cap) whatever the size is. The card stake is the entry row's `stake_sol`
    (falls back to `size`); a `size` that is not one of 0.25 / 0.5 / 1.0, or a card stake that is missing or not positive, is refused (None)."""
    cap = min(int(cap_lamports), C0_LIMITS["stake_lamports"])
    card = row.get("stake_sol", row.get("size"))
    size = row.get("size")
    rec: dict[str, Any] = {"card_stake_sol": card, "size": size, "cap_lamports": cap}
    if size is not None and (isinstance(size, bool) or not isinstance(size, (int, float)) or float(size) not in HV4_SIZES):
        rec["refused"] = "size_invalid"
        return None, rec
    if isinstance(card, bool) or not isinstance(card, (int, float)) or card != card or card <= 0:
        rec["refused"] = "card_stake_invalid"
        return None, rec
    card_lamports = int(round(float(card) * LAMPORTS_PER_SOL))
    s = min(card_lamports, cap)
    rec["stake_lamports"] = s
    rec["twin_stake_lamports"] = s  # the paper twin is priced at S, equal by construction
    rec["clamped"] = card_lamports > cap
    return s, rec


LIVE_REFUSAL = "hv4_c0_incomplete"  # a live start of this profile is refused until a later PR (sell escalation, parity pricing) removes it
LIVE_REFUSAL_ACTIVE = True  # THE switch (QP removal item 15): True = every start outside the synthetic dry path refuses LIVE_REFUSAL. False = the recorded-read start gate in start_refusal decides.
START_NO_REAL_PASS = "start_no_real_pass_record"  # a start on a non-synthetic root needs a recorded PASS read for THIS bot, read from the canonical reads dir (QP removal item 3)
START_SEALED_ROOT = "start_root_or_decisions_sealed"  # the root, decisions/<bot_id> or a day file lies under or aliases /data/mal/arena (only with LIVE_REFUSAL_ACTIVE False)
START_LIVE_ON_SYNTHETIC_ROOT = "start_live_on_synthetic_root"  # a root carrying SYNTHETIC_MARKER never starts live (only with LIVE_REFUSAL_ACTIVE False)
START_LIVE_GATES = "start_live_gates_not_passed"  # a live start also needs every PR4 gate (parity paths, a3 record file, owner line pin, read record, window cap)


SYNTHETIC_MARKER = "SYNTHETIC_FIXTURE_ROOT"
SEALED_ROOT_PREFIX = "/data/mal/arena"


# --- PR4 gates: read record, owner line, model id, per-trade loss, 14-day window ---------------------------------------------------------------------------
MODEL_ID = "claude-haiku-5-5"  # the one model id an F arm may have been read under (the read record's `model_ids`)
OWNER_DEC_DIR = "/etc/mal-hv4-c0"  # <dir>/<bot_id>/CANARY_DEC.md: ROOT-OWNED, outside the installed tree (the Q50 re-install problem: a DEC/ file in the tree is overwritten by a deploy). One file per bot, so two canaries can run at once
OWNER_DEC_NAME = "CANARY_DEC.md"
OWNER_DEC_MAX_BYTES = 65_536
OWNER_LINE_FMT = "OWNER_CANARY_CONFIRMED: bot_id={bot_id}"
LIVE_OK_DIR = "/etc/mal-arena-live/hv4-c0"  # <dir>/<bot_id>/LIVE_OK (root-owned, content exactly the bot_id). TIER stays the one shared /etc/mal-arena-live/TIER (T1)
LIVE_OK_NAME = "LIVE_OK"
PER_TRADE_LOSS_PCT = 30  # per-trade loss latch: 30% of the order's own S (integer lamports, floor)
MAX_WINDOW_MS = 14 * 86_400_000  # a C0 canary window is 14 days from the first start; end_ms may not reach past it
READS_DIR_CANON = "/data/mal/arena/reads"  # where arena_score.record_read writes `<bot_id>.json`; the only place under the sealed prefix a record may be read from
READ_RECORD_KEYS = ("bot_id", "status", "read_boundary_ms", "attempts", "model_ids")


def per_trade_loss_lamports(stake: int, configured: int | None = None) -> int:
    """30% of S (floor). A config value may only tighten it."""
    cap = int(stake) * PER_TRADE_LOSS_PCT // 100
    return cap if configured is None else min(cap, int(configured))


def window_cap_ms(first_start_ms: int) -> int:
    return int(first_start_ms) + MAX_WINDOW_MS


def end_cap_problem(end_ms: int, first_start_ms: int) -> str | None:
    """end_ms may not be later than the first start + 14 days (the first start is persisted in the bridge state, so a restart does not extend it)."""
    return "end_ms_over_14d" if int(end_ms) > window_cap_ms(first_start_ms) else None


WINDOW_UNREADABLE = "window_file_unreadable_manual_recovery"  # empty or damaged window file (e.g. a crash mid-write): the start refuses until the manager inspects and removes it
WINDOW_DIR = "/var/lib/mal-hv4-c0/window"  # write-once `<bot_id>.json` per bot, outside the bridge's state dir: a new state dir, a deleted state file or a mode switch cannot reset it


WINDOW_MANUAL_RECOVERY = ("window_file_unsafe", "window_file_unwritable")
WINDOW_MANUAL_NOTE = ("MANUAL RECOVERY: do not delete or recreate the window file to get a start. The owner inspects WINDOW_DIR (a window file that is a link, not a regular "
                      "root/service-owned file, or hard-linked; or a stale .<bot_id>.<pid>.tmp left by a crashed start) and restores the ORIGINAL first_start_ms; "
                      "a new first start would restart the 14-day cap")


READS_DIR_NOTE = ("on the bridge host (mal-fast-0) root checks /data/mal/arena and /data/mal/arena/reads and creates each ONLY IF ABSENT: "
                  "`install -d -m 0755 -o root -g root /data/mal/arena /data/mal/arena/reads` (both root:root 0755; never chown or chmod an existing directory: "
                  "one that exists with another owner or mode is a stop for the manager; /data/mal/arena must exist, the sealed-alias check fails closed without it), "
                  "then installs the arm's recorded read as a "
                  "byte copy `/data/mal/arena/reads/<bot_id>.json` root:root 0444 whose sha256 equals the read job's RECORD line (docs/runbooks/hv4-c0-bridge.md)")
READS_DIR_LABELS = ("read_record_dir_absent", "read_record_missing", "read_record_dir_owner_mode", "read_record_owner_mode")


def refusal_text(why: str) -> str:
    """The label, plus the manual-recovery note for the two window labels that must never be cleared by the bridge, and the reads-dir provisioning note for the
    labels a missing or wrongly owned /data/mal/arena/reads on the bridge host gives."""
    if why in WINDOW_MANUAL_RECOVERY:
        return f"{why} ({WINDOW_MANUAL_NOTE})"
    if why in READS_DIR_LABELS:
        return f"{why} ({READS_DIR_NOTE})"
    return why


def bot_id_problem(bot_id: Any) -> str | None:
    """A bot_id is a path component (decisions/<bot_id>, window/<bot_id>.json, status files). Refuse a non-string, an empty one, '/', a backslash, '..', NUL, or a
    leading '.'."""
    if not isinstance(bot_id, str) or not bot_id or len(bot_id) > 128:
        return "bot_id_invalid"
    if "/" in bot_id or "\\" in bot_id or ".." in bot_id or "\x00" in bot_id or bot_id.startswith(".") or bot_id != bot_id.strip():
        return "bot_id_invalid"
    return None


def window_start_ms(bot_id: str, now_ms: int, create: bool) -> tuple[int | None, str | None]:
    """(first start ms, problem). The first start of this bot_id, kept in WINDOW_DIR/<bot_id>.json (created O_EXCL, 0444; the dir must exist and be owned by
    root or the running user, not group/other writable). `create=False` (main's pre-check) returns (None, None) when there is no file yet."""
    import os
    import stat
    if not isinstance(bot_id, str) or "/" in bot_id or bot_id.startswith("."):
        return None, "window_bot_id"
    try:
        if not _owner_mode_ok(os.stat(WINDOW_DIR)):
            return None, "window_dir_owner_mode"
    except OSError:
        return None, "window_dir_missing"
    path = os.path.join(WINDOW_DIR, f"{bot_id}.json")
    for attempt in (0, 1):
        try:
            ls = os.lstat(path)
        except FileNotFoundError:
            if not create:
                return None, None
            tmp = os.path.join(WINDOW_DIR, f".{bot_id}.{os.getpid()}.tmp")  # atomic: write + fsync a private tmp, then link it into place (fails if it exists)
            made = False
            try:
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)  # an existing tmp (a crashed run with this pid) refuses: manual recovery
                made = True
                with os.fdopen(fd, "w") as fh:
                    fh.write(json.dumps({"bot_id": bot_id, "first_start_ms": int(now_ms)}, sort_keys=True) + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                try:
                    os.link(tmp, path)
                except FileExistsError:
                    continue
                finally:
                    os.unlink(tmp)
                    made = False
                dfd = os.open(WINDOW_DIR, os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
            except OSError:
                return None, "window_file_unwritable"
            finally:
                if made:  # a write / fsync / link failure leaves no stale tmp behind (only a tmp this call created is removed)
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
            return int(now_ms), None
        except OSError:
            return None, WINDOW_UNREADABLE
        if stat.S_ISLNK(ls.st_mode) or not stat.S_ISREG(ls.st_mode) or ls.st_nlink != 1 or not _owner_mode_ok(ls):
            return None, "window_file_unsafe"
        try:
            rec = json.loads(open(path, "rb").read())
        except (OSError, ValueError):
            return None, WINDOW_UNREADABLE
        v = rec.get("first_start_ms") if isinstance(rec, dict) else None
        if not isinstance(rec, dict) or rec.get("bot_id") != bot_id or isinstance(v, bool) or not isinstance(v, int):
            return None, WINDOW_UNREADABLE
        return v, None
    return None, WINDOW_UNREADABLE


def model_problem(bot_id: str, model_ids: Any) -> str | None:
    """F arm: the recorded model_ids must be exactly [claude-haiku-5-5]. X arm: empty, or every id that single model. Anything else is refused."""
    ok = isinstance(model_ids, list) and all(isinstance(m, str) for m in model_ids)
    if not ok:
        return "model_ids_unreadable"
    if str(bot_id).endswith("-F"):
        return None if model_ids == [MODEL_ID] else "model_id_not_pinned"
    return None if all(m == MODEL_ID for m in model_ids) else "model_id_not_pinned"


def _same_inode(a: Any, b: Any) -> bool:
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def _alias_of_sealed(path: str, extra: Iterable[str] = ()) -> bool:
    """True when `path`, or any ancestor of its realpath, is the SAME (st_dev, st_ino) as the sealed prefix or one of `extra` sealed paths (a bind mount or
    a hard-linked directory shows the sealed inode under a /data2 or other name; realpath alone cannot see it). Stat only, nothing is listed or opened."""
    import os
    sealed = []
    for sp in (SEALED_ROOT_PREFIX, *extra):
        try:
            sealed.append(os.stat(sp))
        except (FileNotFoundError, NotADirectoryError):
            if sp == SEALED_ROOT_PREFIX:
                return True  # fail closed: without the prefix's identity no alias can be ruled out
        except OSError:
            return True  # fail closed: a sealed path that cannot be stat-ed is not a pass
    cur = os.path.realpath(path)
    while True:
        try:
            st = os.stat(cur)
            if any(_same_inode(st, x) for x in sealed):
                return True
        except OSError:
            pass
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def _owner_mode_ok(st: Any) -> bool:
    """Owned by root or by the running user, and not writable by group or other."""
    import os
    return st.st_uid in (0, os.geteuid()) and not (st.st_mode & 0o022)


def synthetic_root(root: Any) -> bool:
    """A root that carries SYNTHETIC_MARKER (a regular file) and does not resolve to, or alias, the sealed prefix."""
    import os
    import stat
    try:
        if _under_sealed(str(root)):
            return False
        return stat.S_ISREG(os.lstat(os.path.join(os.path.realpath(str(root)), SYNTHETIC_MARKER)).st_mode)
    except OSError:
        return False


def read_record_problem(reads_dir: Any, bot_id: Any, pinned: Mapping[str, Any] | None = None, synthetic_ok: bool = False) -> tuple[str | None, dict[str, Any]]:
    """The gate before ANY decision row of an hv4- bot is read (QP #913): a recorded PASS read for THIS bot (arena_score.record_read output,
    `<reads_dir>/<bot_id>.json`, canonical JSON with bot_id, status PASS, read_boundary_ms, attempts, model_ids, not boundary_source forced, am15 block).
    Returns (problem or None, identity). The identity {path, dev, ino, sha256} of the first good read is passed back as `pinned` on every later check, and the
    file must still be the same inode with the same bytes (a swap, a rewrite or an unlink after the first pass is a refusal).
    Checks: reads_dir absolute; the record's realpath equals reads_dir realpath + name (no symlink hop); lstat regular, not a symlink, st_nlink == 1;
    opened O_NOFOLLOW and fstat (dev, ino) equal the lstat; the realpath is not under the sealed arena prefix except exactly READS_DIR_CANON, and no ancestor
    is the same (st_dev, st_ino) as the sealed prefix (a /data2/mal bind or alias); then the content. Applies to any hv4- bot_id.
    PROVENANCE (QP #965 B1): the record must be the pinned scorer's write-once file, so realpath(reads_dir) must EQUAL READS_DIR_CANON; the only exception is
    `synthetic_ok` (a DRY start on a root with SYNTHETIC_MARKER; the caller decides). The record's `frozen_sha` must equal FROZEN_SHA, and the reads dir and the
    record must be owned by root or the running user and not writable by group or other.
    RUNBOOK NOTE (QP N9): the identity is also persisted in the bridge state. Rewriting or replacing the record (a new inode or other bytes) latches `read_record`
    and halts the bridge FOR GOOD: only the manager clears a latch, with the bridge stopped. That is correct, not a bug."""
    import os
    import stat
    ident: dict[str, Any] = {}
    if not isinstance(bot_id, str) or not bot_id.startswith("hv4-") or "/" in bot_id or bot_id.startswith("."):
        return "read_record_bot_id", ident
    if not isinstance(reads_dir, str) or not reads_dir or not os.path.isabs(reads_dir):
        return "read_record_dir_missing", ident
    real_dir = os.path.realpath(reads_dir)
    path = os.path.join(real_dir, f"{bot_id}.json")
    under = real_dir == SEALED_ROOT_PREFIX or real_dir.startswith(SEALED_ROOT_PREFIX + "/")
    if under and real_dir != READS_DIR_CANON:
        return "read_record_dir_sealed", ident
    if real_dir != os.path.realpath(READS_DIR_CANON) and not synthetic_ok:
        return "read_record_dir_not_canonical", ident
    if real_dir != READS_DIR_CANON and _alias_of_sealed(real_dir, (READS_DIR_CANON,)):
        return "read_record_dir_sealed_alias", ident
    if not os.path.isdir(real_dir):  # names the provisioning step (refusal_text) instead of the generic read_record_missing; still a refusal
        return "read_record_dir_absent", ident
    try:
        if not _owner_mode_ok(os.stat(real_dir)):
            return "read_record_dir_owner_mode", ident
        ls = os.lstat(path)
    except OSError:
        return "read_record_missing", ident
    if stat.S_ISLNK(ls.st_mode) or not stat.S_ISREG(ls.st_mode):
        return "read_record_not_regular", ident
    if not _owner_mode_ok(ls):
        return "read_record_owner_mode", ident
    if ls.st_nlink != 1:
        return "read_record_hardlink", ident
    if os.path.realpath(path) != path:
        return "read_record_path_alias", ident
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return "read_record_unreadable", ident
    try:
        fs = os.fstat(fd)
        if not _same_inode(fs, ls) or fs.st_nlink != 1 or not stat.S_ISREG(fs.st_mode):
            return "read_record_swapped", ident
        chunks = []
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            chunks.append(b)
        data = b"".join(chunks)
    finally:
        os.close(fd)
    ident = {"path": path, "dev": fs.st_dev, "ino": fs.st_ino, "sha256": hashlib.sha256(data).hexdigest()}
    if pinned and {k: pinned.get(k) for k in ident} != ident:
        return "read_record_changed", ident
    try:
        rec = json.loads(data)
    except ValueError:
        return "read_record_unreadable", ident
    if not isinstance(rec, dict) or any(k not in rec for k in READ_RECORD_KEYS):
        return "read_record_unreadable", ident
    if rec["bot_id"] != bot_id:
        return "read_record_other_bot", ident
    if rec.get("frozen_sha") != FROZEN_SHA:
        return "read_record_frozen_sha", ident
    if rec["status"] != "PASS" or rec.get("fail_label") or rec.get("boundary_source") == "forced" or rec.get("pending"):
        return "read_record_not_pass", ident
    if isinstance(rec["read_boundary_ms"], bool) or not isinstance(rec["read_boundary_ms"], int) or not isinstance(rec.get("am15"), dict):
        return "read_record_not_pass", ident
    why = model_problem(bot_id, rec["model_ids"])
    return (why, ident)


def owner_dec_path(bot_id: Any) -> str:
    """The owner confirmation file of one bot: OWNER_DEC_DIR/<bot_id>/CANARY_DEC.md. Raises ValueError for a bot_id that is not a plain path component."""
    why = bot_id_problem(bot_id)
    if why:
        raise ValueError(why)
    return os.path.join(OWNER_DEC_DIR, bot_id, OWNER_DEC_NAME)


def live_ok_path(bot_id: Any) -> str:
    """The LIVE_OK file of one bot: LIVE_OK_DIR/<bot_id>/LIVE_OK. Raises ValueError for a bot_id that is not a plain path component."""
    why = bot_id_problem(bot_id)
    if why:
        raise ValueError(why)
    return os.path.join(LIVE_OK_DIR, bot_id, LIVE_OK_NAME)


OWNER_LINE_LATCH = "owner_line"  # the bridge latch name (arena_live_bridge.LATCHES, hv4_c0_parity.HARD_LATCHES)
OWNER_LINE_CHANGED = "owner_line_changed"  # owner_line_check's label for a pinned file that was rewritten, replaced or removed
OWNER_PIN_KEYS = ("path", "dev", "ino", "sha256")


def owner_line_check(path: Any, bot_id: str, root_check: Any = None, pinned: Mapping[str, Any] | None = None) -> tuple[str | None, dict[str, Any]]:
    """The owner's confirmation: the file `owner_dec_path(bot_id)` exists, is a regular file (not a symlink, st_nlink 1), is ROOT-OWNED and not writable by group or
    other, and holds EXACTLY ONE line equal to `OWNER_CANARY_CONFIRMED: bot_id=<id>`. Returns (problem or None, identity). The identity {path, dev, ino, sha256} is
    of the inode that was checked and the bytes that were searched. There is NO sha256 of the owner file or of a DEC copy in code (DEC-031 Am.21 item 4).
    `root_check(path)` is the bridge's root:root check (None = not run, tests only). The file is opened O_NOFOLLOW and the bytes searched are the bytes of the
    inode that was checked. A prefix, a suffix, CRLF, a second copy, or another bot's line never matches.

    PIN (DEC-031 Am.21 item 4, as QP N9 does for the read record): the bridge stores the identity of the first good check in its state and passes it back as
    `pinned` on every later check. With a pin, a different (path, st_dev, st_ino, sha256), a file that is missing, unreadable, a symlink, not regular or swapped
    during the check, or a file that grew past OWNER_DEC_MAX_BYTES returns OWNER_LINE_CHANGED (the bridge latches `owner_line`). Rewriting the file with other
    bytes, replacing it with identical bytes (a new inode) and removing it all change the identity. A problem that leaves the identity alone (link count, mode or
    owner label from `root_check`) keeps its own label: that is a DRY entry, not a latch. The pin is compared before the line, so a rewrite that drops the line is
    a change, not owner_line_missing. Residual, the same as the read-record pin: the identity is the DEC's (st_dev, st_ino, sha256), so a file deleted and then
    created again with identical bytes on a freed, reused inode number keeps its identity.

    Labels: owner_dec_missing, _symlink, _not_regular, _hardlink, _unreadable, _swapped, _too_large, and owner_dec_<root_check label>, owner_line_missing,
    owner_line_changed (pin only)."""
    import os
    import stat as st_
    ident: dict[str, Any] = {}
    if bot_id_problem(bot_id):
        return "owner_dec_bot_id", ident
    pin = dict(pinned) if pinned else None
    gone = OWNER_LINE_CHANGED if pin else None  # once pinned, a path that no longer names the pinned file is a change, whatever it names now
    if pin and pin.get("path") != os.fspath(path):
        return OWNER_LINE_CHANGED, ident
    try:
        ls = os.lstat(path)
    except FileNotFoundError:
        return gone or "owner_dec_missing", ident
    except OSError:
        return gone or "owner_dec_unreadable", ident
    if st_.S_ISLNK(ls.st_mode):
        return gone or "owner_dec_symlink", ident
    if not st_.S_ISREG(ls.st_mode):
        return gone or "owner_dec_not_regular", ident
    if pin and (ls.st_dev, ls.st_ino) != (pin.get("dev"), pin.get("ino")):
        return OWNER_LINE_CHANGED, ident  # a new inode, even with identical bytes
    if ls.st_nlink != 1:
        return "owner_dec_hardlink", ident
    if root_check is not None:
        why = root_check(path)
        if why:
            return f"owner_dec_{why}", ident
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return gone or "owner_dec_missing", ident
    except OSError:
        return gone or "owner_dec_unreadable", ident
    try:
        fs = os.fstat(fd)
        if (fs.st_dev, fs.st_ino) != (ls.st_dev, ls.st_ino) or not st_.S_ISREG(fs.st_mode):
            return gone or "owner_dec_swapped", ident  # another inode at the path between the lstat and the open
        if fs.st_nlink != 1:
            return "owner_dec_swapped", ident  # a hard link appeared between the lstat and the open: the same inode, so not a change of the pinned file
        if fs.st_size > OWNER_DEC_MAX_BYTES:
            return gone or "owner_dec_too_large", ident  # the pinned bytes were within the bound, so these are other bytes
        data = b""
        while True:
            b = os.read(fd, 1 << 16)
            if not b:
                break
            data += b
            if len(data) > OWNER_DEC_MAX_BYTES:
                return gone or "owner_dec_too_large", ident
    finally:
        os.close(fd)
    ident = {"path": os.fspath(path), "dev": fs.st_dev, "ino": fs.st_ino, "sha256": hashlib.sha256(data).hexdigest()}
    if pin and {k: pin.get(k) for k in OWNER_PIN_KEYS} != ident:
        return OWNER_LINE_CHANGED, ident
    want = OWNER_LINE_FMT.format(bot_id=bot_id)
    return (None if sum(1 for ln in data.decode("utf-8", "replace").split("\n") if ln == want) == 1 else "owner_line_missing"), ident


def owner_line_problem(path: Any, bot_id: str, root_check: Any = None, pinned: Mapping[str, Any] | None = None) -> str | None:
    """The problem label of `owner_line_check` (None = the owner's confirmation holds). With `pinned`, a changed file is OWNER_LINE_CHANGED."""
    return owner_line_check(path, bot_id, root_check, pinned)[0]


# --- halts (QP removal precondition 7): FEED-GAP and A3. Both stop NEW buys only; exits keep running. ---------------------------------------------------------
FEED_STATUS_MAX_AGE_MS = 10_000  # the forwarder rewrites <bot_id>.status.json every 250 ms; a status older than this means the forwarder itself is down
FEED_FRAME_MAX_MS = 5_000  # decision stream silence bound: no frame (a row or the forwarder's 500 ms remote heartbeat) for this long refuses buys. A pinned choice, 2.5x the forwarder's own 2,000 ms stall
RUNNER_SILENT_MS = 1_800_000  # 30 min: no decision row of ANY kind in the bot's log for this long = the runner is presumed dead (see runner_silent_problem). A pinned choice
FEED_SKEW_MS = 5_000  # a status stamped this far in the future is a wrong clock, not a fresh feed
FEED_STATUS_MAX_BYTES = 65_536
FEED_SCHEMA = "decision_forwarder_v1"  # tools/decision_forwarder.py (PR #967), status file format
A3_LATCH = "a3_pins_changed"  # the name tools/h5_arena_gates.HARD_ARENA_HALTS already carries, so the parity state counts it as an unresolved hard halt
A3_MAX_AGE_MS = 36 * 3_600_000  # same bound as tools/h5_arena_executor.A3_MAX_AGE_MS: the monitor writes daily
A3_FUTURE_SKEW_MS = 300_000
A3_POLL_MS = 60_000
A3_GUARD_FLAGS = ("synthetic_share_high",)  # DEC-032 Am.3: decided by the DEC-031 Am.10 pooled guard (a3-applied.jsonl), never by the raw record
A3_APPLIED_NAME = "a3-applied.jsonl"  # scripts/ops/a3-daily.sh: $A3_OUT/a3-applied.jsonl, the same directory as the daily record
A3_GUARD_VERDICTS = ("NONE", "PASS", "HALT", "FAIL", "ERROR")  # == tools.a3_halt.VERDICTS (pinned by a test)
A3_GUARD_CLEAR = ("NONE", "PASS")  # PASS never clears a raw pins_changed: the raw check runs first and latches on it
A3_GUARD_LAG_MS = 2 * 3_600_000  # declared choice, not measured: a pending guard refuses buys; past this it latches
A3_APPLIED_MAX_BYTES = 16 * 1024 * 1024


def feed_status_path(root: str, bot_id: str) -> str:
    return os.path.join(root, "transport", f"{bot_id}.status.json")


def feed_gap_problem(path: str, bot_id: str, now_ms: int) -> str | None:
    """None when the forwarder's status file says the feed is live; else the reason (a refusal of new buys, never a latch: a stall that resumes clears itself).
    Fails closed: a missing, unreadable, linked, oversize, malformed, foreign-bot, stale, future-stamped or non-`connected` status is a gap. The format is
    decision_forwarder.write_status: {schema, bot, state, ms, last_frame_ms, last_remote_ms, seq, ...}; `ms` is the write instant, `last_frame_ms` the newest frame."""
    try:
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            return "feed_status_not_regular"
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            if os.fstat(fd).st_ino != st.st_ino:
                return "feed_status_swapped"
            data = os.read(fd, FEED_STATUS_MAX_BYTES + 1)
        finally:
            os.close(fd)
    except FileNotFoundError:
        return "feed_status_missing"
    except OSError:
        return "feed_status_unreadable"
    if len(data) > FEED_STATUS_MAX_BYTES:
        return "feed_status_oversize"
    try:
        rec = json.loads(data)
    except ValueError:
        return "feed_status_malformed"
    if not isinstance(rec, dict) or rec.get("schema") != FEED_SCHEMA or rec.get("bot") != bot_id:
        return "feed_status_foreign"
    ms, last = rec.get("ms"), rec.get("last_frame_ms")
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (ms, last)):
        return "feed_status_malformed"
    sm = rec.get("stall_ms")
    if isinstance(sm, bool) or not isinstance(sm, int) or sm > FEED_MAX_STALL_MS or sm <= 0:
        return "feed_stall_ms_not_pinned"
    if rec.get("state") != "connected":
        return f"feed_state_{str(rec.get('state'))[:24]}"
    if ms - now_ms > FEED_SKEW_MS or last - now_ms > FEED_SKEW_MS:
        return "feed_status_future"
    if now_ms - ms > FEED_STATUS_MAX_AGE_MS:
        return "feed_status_stale"
    if last <= 0 or now_ms - last > FEED_FRAME_MAX_MS:
        return "feed_silent"
    return None


# --- DEC-032 s5.2 FEED-GAP HALT: the book's OWN gap records. A hard latch `feed_break` (already in HARD_ARENA_HALTS). Restart needs 10 gap-free minutes AND the manager's
# written review, as a root-owned clear file. Only the KIND and INSTANT of a gap record are read (QP seal ruling): no other field, no join to any outcome.
GAP_FREE_MS = 600_000
GAP_BREAK_KINDS = ("stall", "disconnect", "reconnect", "mismatch", "local_torn_trimmed", "error", "gap", "data_loss", "resubscribe", "silence", "process_restart", "feed_restart", "feed_start", "start", "takeover", "gap_silence")  # forwarder kinds + the s5.2 list; "resume" is not a gap
GAP_CLEAR_DIR = "/etc/mal-hv4-c0/gap-clear"  # <bot_id>.json: {"bot_id": ..., "reviewed_gap_ms": N}; root-owned (h5.root_file_problem), N == the newest gap instant
FEED_BREAK_LATCH = "feed_break"
FEED_MAX_STALL_MS = 2_000  # a forwarder status that reports a larger stall_ms is refused: a misconfigured forwarder must not widen the bound (QP ruling a)


GAP_LOG_MAX_BYTES = 8_000_000


def gap_log_ms(path: str) -> tuple[list[int], list[int], str | None]:
    """(gap instants, resume instants, problem) from the forwarder's <bot_id>.gaps.jsonl. Reads ONLY `kind` and `ms` of each line. A missing file is no gaps; a link, an
    unreadable file, a file over GAP_LOG_MAX_BYTES (never silently truncated) or a COMPLETE line that is not an object with an int ms is a problem (fail closed).
    Bytes after the last newline are a write in progress and are ignored."""
    try:
        ls = os.lstat(path)
    except FileNotFoundError:
        return [], [], None
    except OSError:
        return [], [], "gap_log_unreadable"
    if not stat.S_ISREG(ls.st_mode) or ls.st_nlink != 1:
        return [], [], "gap_log_not_regular"
    if ls.st_size > GAP_LOG_MAX_BYTES:
        return [], [], "gap_log_too_large"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as fh:
            data = fh.read(GAP_LOG_MAX_BYTES + 1)
    except OSError:
        return [], [], "gap_log_unreadable"
    if len(data) > GAP_LOG_MAX_BYTES:
        return [], [], "gap_log_too_large"
    gaps: list[int] = []
    resumes: list[int] = []
    lines = data.split(b"\n")
    lines.pop()  # the torn tail (or the empty string after the last newline)
    for ln in lines:
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except ValueError:
            return gaps, resumes, "gap_log_malformed"  # a COMPLETE (newline-ended) malformed line latches
        if not isinstance(r, dict) or isinstance(r.get("ms"), bool) or not isinstance(r.get("ms"), int) or not isinstance(r.get("kind"), str):
            return gaps, resumes, "gap_log_malformed"
        if r["kind"] in GAP_BREAK_KINDS:
            gaps.append(r["ms"])
        elif r["kind"] == "resume":
            resumes.append(r["ms"])
    return gaps, resumes, None


def gap_records_ms(path: str) -> tuple[list[int], str | None]:
    g, _r, bad = gap_log_ms(path)
    return g, bad


def gap_clear_problem(path: str, bot_id: str, last_gap_ms: int, now_ms: int, root_check: Any = None, free_since_ms: int | None = None) -> str | None:
    """None when (1) the feed has been gap-free for GAP_FREE_MS counted from the END of the gap (`free_since_ms`: the newest gap OR resume instant; default the
    gap instant) and (2) the manager's root-owned review file names EXACTLY the newest gap instant (equality: a blanket or future-dated review never clears a later
    gap, DEC-032 s5.2 "the manager has seen the gap record"). Else the reason the latch stays."""
    since = last_gap_ms if free_since_ms is None else max(free_since_ms, last_gap_ms)
    if now_ms - since < GAP_FREE_MS:
        return "gap_free_window_not_elapsed"
    if root_check is not None:
        why = root_check(path)
        if why:
            return f"gap_clear_{why}"
    try:
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            return "gap_clear_not_regular"
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as fh:
            rec = json.loads(fh.read(4096))
    except FileNotFoundError:
        return "gap_clear_missing"
    except (OSError, ValueError):
        return "gap_clear_unreadable"
    v = rec.get("reviewed_gap_ms") if isinstance(rec, dict) else None
    if not isinstance(rec, dict) or rec.get("bot_id") != bot_id or isinstance(v, bool) or not isinstance(v, int):
        return "gap_clear_malformed"
    return None if v == last_gap_ms else "gap_not_reviewed"


def runner_silent_problem(last_row_ms: int | None, started_ms: int, now_ms: int) -> str | None:
    """RUNNER LIVENESS (QP on #967, item 6): the forwarder's heartbeat proves the SERVER is alive, not the HV4 runner. The bridge measures silence from the newest
    decision row it consumed (any kind), or from its own start when it has seen none. FROM CODE (tools/hv4_runner.py): the runner's periodic rows (`scan`, `scan_timing`,
    `tick_timing`, every 5 min / 1 min) go to the SCAN log, NOT to decisions/<bot_id>; the bot's own log carries entry / exit / skip / halt / llm_call rows only,
    so a quiet bot and a dead runner look the same here. Hence the bound is long and the rule is conservative (it can only withhold buys). A dead runner writes no
    entry rows anyway, so the rule is a belt; the PROTECTION for an open position whose exit row never comes is the bridge's own time-stop backstop
    (max_hold + BACKSTOP_EXTRA_MS) and the sl backstop, which need no runner row (tested with a dead-runner log). Open item: a forwarded runner heartbeat (SCAN-log tick_timing)."""
    base = started_ms if last_row_ms is None else max(last_row_ms, started_ms)
    if now_ms < base:
        return None
    return "runner_silent" if now_ms - base > RUNNER_SILENT_MS else None


def a3_halt_problem(path: str, now_ms: int) -> str | None:
    """None when the NEWEST line of the A3 daily record (tools/pump_structure_monitor.py output, the file tools/h5_arena_executor.a3_record_problem reads) shows no halt.
    Else the reason: `pins_changed` (or any other `halt.flags.*.halt` true, or a flag not evaluated or malformed), `program_changed` warn, or a record that is
    missing, unreadable, flag-less, stale (36 h) or future-stamped. Fail closed; the bridge latches A3_LATCH on any return value."""
    from datetime import datetime, timezone
    try:
        ls = os.lstat(path)
        if not stat.S_ISREG(ls.st_mode) or ls.st_nlink != 1:
            return "a3_record_not_regular"  # a link or hard link is not the monitor's own file
        if ls.st_uid not in (0, os.getuid()) or ls.st_mode & 0o002:
            return "a3_record_owner_mode"  # root or the running user, not world-writable
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as fh:
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        rec = json.loads(lines[-1])
    except (OSError, ValueError, IndexError):
        return "a3_record_unreadable"
    flags = ((rec.get("halt") or {}).get("flags")) if isinstance(rec, dict) and isinstance(rec.get("halt"), dict) else None
    if not isinstance(flags, dict) or not flags:
        return "a3_record_no_halt_flags"
    try:
        run_ms = int(datetime.strptime(rec["run_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except (KeyError, ValueError, TypeError):
        return "a3_record_no_run_utc"
    if run_ms - now_ms > A3_FUTURE_SKEW_MS:
        return "a3_record_future"
    if now_ms - run_ms > A3_MAX_AGE_MS:
        return "a3_record_stale"
    why = sorted(k for k, v in flags.items() if k not in A3_GUARD_FLAGS
                 and (not (isinstance(v, dict) and isinstance(v.get("halt"), bool)) or v["halt"] or v.get("evaluated") is False))
    pc = (((rec.get("warn") or {}).get("flags") or {}).get("program_changed")) if isinstance(rec.get("warn"), dict) else None
    if isinstance(pc, dict) and pc.get("warn") is True:
        why.append("program_changed")
    return ("a3_flags:" + ",".join(why)) if why else None


def _iso_ms(v: Any) -> int:
    from datetime import datetime, timezone
    return int(datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)


def a3_problem(record_path: str, now_ms: int) -> tuple[str | None, str | None]:
    """(latch, refuse) under DEC-032 Am.3. The raw record is checked first (a3_halt_problem: every flag but synthetic_share_high, program_changed, stale,
    future, unreadable): a raw reason is the latch. Otherwise the newest line of dirname(record)/a3-applied.jsonl (the DEC-031 Am.10 pooled guard) for the SAME
    run_utc decides: HALT / FAIL / ERROR latch, NONE / PASS clear. A guard behind the record by at most A3_GUARD_LAG_MS refuses buys only (a3_guard_pending, no
    latch); a missing, unreadable, linked, oversize, malformed or ahead log, or a guard behind for longer, latches."""
    raw = a3_halt_problem(record_path, now_ms)
    if raw:
        return raw, None
    try:
        with open(record_path, "rb") as fh:
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        rec_ms = _iso_ms(json.loads(lines[-1])["run_utc"])
        rec_mtime_ms = int(os.stat(record_path).st_mtime * 1000)
    except (OSError, ValueError, IndexError, KeyError, TypeError):
        return "a3_record_unreadable", None
    ap = os.path.join(os.path.dirname(record_path), A3_APPLIED_NAME)
    try:
        st = os.lstat(ap)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > A3_APPLIED_MAX_BYTES:
            return "a3_guard_unreadable", None
        fd = os.open(ap, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            st2 = os.fstat(fd)
            if (st2.st_ino, st2.st_dev) != (st.st_ino, st.st_dev) or st2.st_size > A3_APPLIED_MAX_BYTES:
                return "a3_guard_unreadable", None
            data = b""
            while len(data) <= A3_APPLIED_MAX_BYTES:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    break
                data += chunk
        finally:
            os.close(fd)
        if len(data) > A3_APPLIED_MAX_BYTES:
            return "a3_guard_unreadable", None
        line = json.loads([ln for ln in data.splitlines() if ln.strip()][-1])
    except (OSError, ValueError, IndexError):
        return "a3_guard_unreadable", None
    try:
        app_ms = _iso_ms(line["run_utc"])
        verdict = line.get("verdict")
    except (KeyError, ValueError, TypeError, AttributeError):
        return "a3_guard_malformed", None
    if verdict not in A3_GUARD_VERDICTS:
        return "a3_guard_malformed", None
    if app_ms > rec_ms:
        return "a3_guard_ahead", None
    if app_ms < rec_ms:
        if now_ms - max(rec_ms, rec_mtime_ms) <= A3_GUARD_LAG_MS:
            return None, "a3_guard_pending"
        return "a3_guard_missing", None
    if verdict not in A3_GUARD_CLEAR:
        return "a3_guard:" + str(verdict), None
    return None, None


def start_row_fields() -> dict[str, Any]:
    """Pins the bridge writes into its own START log row (`bridge_start`). The key `frozen_sha` is the key the HV4 runner's own `start` row uses
    (tools/hv4_runner.py: {"kind": "start", "binding", "frozen_sha", "lane_h"}); the runner row carries no config or ladder sha (the runner is frozen), so
    these pins live in the bridge's row."""
    return {"frozen_sha": FROZEN_SHA, "config_sha256": CONFIG_PIN_SHA256, "ladder_sha256": ladder_pin_sha256(), "twin_writer_sha256": TWIN_WRITER_SHA256,
            "sell_pin_sha256": hashlib.sha256(json.dumps(SELL_BUILDER_PIN, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "priority_pin": dict(PRIORITY_PIN), "model_id": MODEL_ID, "window_ms": MAX_WINDOW_MS, "per_trade_loss_pct": PER_TRADE_LOSS_PCT}




def _under_sealed(path: str) -> bool:
    import os
    real = os.path.realpath(path)
    return real == SEALED_ROOT_PREFIX or real.startswith(SEALED_ROOT_PREFIX + "/") or _alias_of_sealed(path)  # realpath OR the same (st_dev, st_ino): one rule for every caller


def decisions_path_refusal(root: str, bot_id: str | None) -> str | None:
    """QP PR1 condition 1: a marked synthetic root can still hold a symlink into the arena. Refuse when the realpath of `<root>/decisions/<bot_id>`, or of
    any day file in it, lies under the sealed arena prefix (no PASS is recorded; none exists yet). Reads directory entry names and resolves links only;
    it opens no file. Fail closed: an unlistable decisions dir is not a pass."""
    import os
    if not bot_id:
        return None
    d = os.path.join(root, "decisions", bot_id)
    if _under_sealed(d):
        return LIVE_REFUSAL
    try:
        names = os.listdir(d) if os.path.isdir(d) else []
    except OSError:
        return LIVE_REFUSAL
    return LIVE_REFUSAL if any(_under_sealed(os.path.join(d, n)) for n in names) else None


class SealedPath(Exception):
    """A decision-log read hit a path that could be an alias of the sealed arena (symlink, hard link, swapped directory or inode, realpath under the arena)."""


def guard_dir(root: str, bot_id: str) -> str:
    """Check `<root>/decisions/<bot_id>` before listing or reading it: realpath not under the sealed prefix, and neither `decisions` nor the bot dir is
    a symlink (lstat) or anything but a directory. Returns the dir path. Missing is fine (nothing to read); raises SealedPath otherwise."""
    import os
    import stat
    dec = os.path.join(root, "decisions")
    d = os.path.join(dec, bot_id)
    if _under_sealed(d) or _alias_of_sealed(d, (SEALED_ROOT_PREFIX + "/decisions", SEALED_ROOT_PREFIX + "/decisions/" + bot_id)):
        raise SealedPath("realpath_under_arena")
    for x in (dec, d):
        try:
            st = os.lstat(x)
        except FileNotFoundError:
            return d
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise SealedPath("symlink_or_not_dir")
    return d


def guarded_listdir(root: str, bot_id: str) -> list[str]:
    """Sorted `*.jsonl` names in the bot dir, after `guard_dir`. Names only; no file is opened."""
    import os
    d = guard_dir(root, bot_id)
    try:
        return sorted(n for n in os.listdir(d) if n.endswith(".jsonl"))
    except FileNotFoundError:
        return []


def guarded_read(root: str, bot_id: str, name: str) -> bytes:
    """Read one day file, running EVERY check on EVERY read (QP #913): dir guard; lstat must be a regular file, not a symlink, with st_nlink == 1 (a
    hard link to an arena file has 2); realpath not under the sealed prefix; open with O_NOFOLLOW and compare fstat (dev, ino, nlink) with the lstat,
    so a swap between the check and the open is caught. Raises SealedPath on any hit; reads through the fd it checked."""
    import os
    import stat
    d = guard_dir(root, bot_id)
    path = os.path.join(d, name)
    if _under_sealed(path):
        raise SealedPath("realpath_under_arena")
    try:
        ls = os.lstat(path)
    except FileNotFoundError:
        return b""
    if stat.S_ISLNK(ls.st_mode):
        raise SealedPath("symlink")
    if not stat.S_ISREG(ls.st_mode):
        raise SealedPath("not_regular")
    if ls.st_nlink != 1:
        raise SealedPath("hardlink")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise SealedPath("open_nofollow_failed") from None
    try:
        fs = os.fstat(fd)
        if (fs.st_dev, fs.st_ino) != (ls.st_dev, ls.st_ino) or fs.st_nlink != 1 or not stat.S_ISREG(fs.st_mode):
            raise SealedPath("inode_swapped")
        try:  # a bind or alias of an arena day file: same (st_dev, st_ino) as the sealed counterpart of this name
            sealed_file = os.stat(os.path.join(SEALED_ROOT_PREFIX, "decisions", bot_id, name))
            if (sealed_file.st_dev, sealed_file.st_ino) == (fs.st_dev, fs.st_ino):
                raise SealedPath("inode_is_arena_file")
        except OSError:
            pass
        chunks = []
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            chunks.append(b)
        return b"".join(chunks)
    finally:
        os.close(fd)


def real_pass_record(problem: str | None, ident: Mapping[str, Any] | None, bot_id: Any) -> bool:
    """True only when the recorded-read gate PASSED (`problem` is None, from read_record_problem) AND the record it read is the canonical scorer file for
    THIS bot: `<realpath(READS_DIR_CANON)>/<bot_id>.json`. A record that passed only through the synthetic exception (a dry start on a marked root reading any
    directory) is not real. This is the fact start_refusal is handed; it is derived from the gate's own result and holds no state."""
    if problem is not None or not isinstance(ident, Mapping) or not isinstance(bot_id, str) or bot_problem(bot_id):
        return False
    return ident.get("path") == os.path.join(os.path.realpath(READS_DIR_CANON), f"{bot_id}.json")


def bot_problem(bot_id: Any) -> str | None:
    """bot_id_problem plus the hv4- prefix the read gate requires (a start gate for any other id is never a real pass)."""
    return bot_id_problem(bot_id) or (None if str(bot_id).startswith("hv4-") else "bot_id_not_hv4")


def start_refusal(root: str, live: bool, bot_id: str | None = None, record_real_pass: bool = False, live_gates_passed: bool = False) -> str | None:
    """CLI start gate (QP-RULING-1025 R2.1: no bridge process, dry or live, reads an HV4 decision row before that arm's recorded read PASS). QP removal item 3.
    Only checks paths and its two explicit arguments; it opens nothing in the root and keeps no state.

    LIVE_REFUSAL_ACTIVE (module constant, True): every start outside the synthetic dry path refuses LIVE_REFUSAL, exactly as before this gate existed; the gate
    below is not evaluated. Flipping that one constant to False is the whole removal of `hv4_c0_incomplete` (QP removal item 15) and turns the gate on:
      * a root at, under or aliasing /data/mal/arena (SEALED_ROOT_PREFIX; realpath, abspath, or any ancestor with the prefix's (st_dev, st_ino)) is always refused,
        marker or not, record or not;
      * a root that carries SYNTHETIC_MARKER (a regular file): a DRY start is allowed as before (paths checked by decisions_path_refusal), a live start never;
      * any other root, dry or live, starts only if `record_real_pass` (the caller's read_record_problem returned None for THIS bot and the record is the
        canonical scorer file; see real_pass_record), the bot is an F or X arm, and `<root>/decisions/<bot_id>` and every day file in it neither resolve under
        nor alias the arena (decisions_path_refusal + guard_dir, the checks the Bridge repeats on every read);
      * a live start additionally needs `live_gates_passed` (main's gates: three parity paths, a3 record file, owner confirmation file, read record, 14-day cap)."""
    import os
    active = LIVE_REFUSAL_ACTIVE is not False  # anything but an explicit False keeps the refusal
    real = os.path.realpath(root)
    if real == SEALED_ROOT_PREFIX or real.startswith(SEALED_ROOT_PREFIX + "/") or os.path.realpath(os.path.abspath(root)).startswith(SEALED_ROOT_PREFIX + "/"):
        return LIVE_REFUSAL if active else START_SEALED_ROOT
    if os.path.isfile(os.path.join(real, SYNTHETIC_MARKER)):  # synthetic dry path: unchanged
        if live:
            return LIVE_REFUSAL if active else START_LIVE_ON_SYNTHETIC_ROOT
        return decisions_path_refusal(real, bot_id)
    if active:
        return LIVE_REFUSAL
    if bot_problem(bot_id):
        return START_NO_REAL_PASS
    why = arm_refusal(bot_id)  # only an F or X arm: -L, -P and -R arms never start on a real root, whatever record exists
    if why:
        return why
    if record_real_pass is not True:  # an explicit True: a truthy non-bool is not a pass
        return START_NO_REAL_PASS
    if _under_sealed(real) or decisions_path_refusal(real, bot_id):  # root or an ancestor aliases the arena; decisions/<bot_id> or a day file lies under or aliases it
        return START_SEALED_ROOT
    try:
        guard_dir(real, str(bot_id))  # decisions/ and decisions/<bot_id> are real directories, not links, and not an alias of the sealed decisions dirs
    except SealedPath:
        return START_SEALED_ROOT
    if live and live_gates_passed is not True:
        return START_LIVE_GATES
    return None


def position_worst_case_lamports(stake: int, rent_reserve: int) -> int:
    """One position's worst case, the terms the wallet-floor check uses (`TRIP_FEE_RESERVE_LAMPORTS`, the bridge's rent reserve,
    `SELL_FEE_RESERVE_LAMPORTS`): stake at zero + buy fee + rent reserve + reserved sell fee. Applies to an open, pending or in-flight buy and to the new buy."""
    return int(stake) + TRIP_FEE_RESERVE_LAMPORTS + int(rent_reserve) + SELL_FEE_RESERVE_LAMPORTS


def committed_loss_lamports(realized_loss: int, open_stakes: Iterable[int], rent_reserve: int) -> int:
    """DEC-032 s3 (Total stop includes open exposure), quoted: "committed loss = realized loss since the first send (floored at 0) + every open or
    pending position's stake at zero + its buy fee + rent reserve + reserved sell fees + every in-flight buy's worst case." Open and in-flight
    orders are one list here (the bridge's `open_orders` includes buy_sending), each at `position_worst_case_lamports`."""
    return max(0, int(realized_loss)) + sum(position_worst_case_lamports(s, rent_reserve) for s in open_stakes)


def total_stop_problem(realized_loss: int, open_stakes: Iterable[int], new_stake: int, rent_reserve: int) -> str | None:
    """DEC-032 s3, quoted: "No new buy is sent unless committed loss plus that buy's worst case is at most the total stop." (0.20 SOL at C0.)
    The daily stop is not touched: DEC-032 s3 defines it as "realized loss in a UTC day" only, which the bridge already checks."""
    need = committed_loss_lamports(realized_loss, open_stakes, rent_reserve) + position_worst_case_lamports(new_stake, rent_reserve)
    return "total_loss_committed" if need > C0_LIMITS["total_loss_lamports"] else None
