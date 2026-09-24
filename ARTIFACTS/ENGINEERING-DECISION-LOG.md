# Engineering decision log

Persistent trail for **meaningful** infra / implementation changes. **DEC/** remains the lock. This log lets a future agent reload **what changed** without reconstructing chat.

Handoff source: Oracle Phase-0 §19 — for each meaningful change record **what / why / tested / verification / current state / rollback / unresolved / architectural implications**.

**Newest first.** No secrets, CIDRs, passwords, or keys.

## Entry format

| Field | Intent |
| --- | --- |
| **ID** | `EDL-NNN` (monotonic) |
| **Date** | Calendar date |
| **What changed** | Concrete delta |
| **Why** | Decision driver |
| **What was tested** | How it was checked (or “docs-only / N/A”) |
| **Verification** | Result |
| **Current state** | What is true now |
| **Rollback** | How to undo or freeze |
| **Unresolved** | Open asks / risks |
| **Implications** | Architecture / ops consequences |
| **Pointers** | DEC / EXP / artifacts |

---

## EDL-011 — Fill-sim incomplete-RPC honesty schema align (Proposed)

| Field | Value |
| --- | --- |
| **ID** | EDL-011 |
| **Date** | 2026-09-24 |
| **What changed** | Registered **Proposed** `paper-fill-sim-incomplete-rpc-honesty-schema-align-v0`: [PAPER-FILL-SIM-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md](PAPER-FILL-SIM-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md). Tightened [paper-fill-sim-hot-packet-evaluate-v0.schema.json](paper-fill-sim-hot-packet-evaluate-v0.schema.json) so `graph_lift_status` follows embedded `input.stamp.input.packet.graph.cold`. Scoreboard, batch, and host-local fill-sim schemas already const-refused the same honesty bits as their CLIs; tests cite them. No new runtime schema. No CLI rewrite. |
| **Why** | Mirror parent #54 on the fill-sim citeable chain (#57–#60). Schema must refuse dishonest shapes those CLIs already refuse before Soft GATE merge. |
| **What was tested** | `python3 -m unittest tools.test_paper_fill_sim_incomplete_rpc_honesty_schema_align_v0` plus existing #57–#60 unit tests. Honest fixtures pass schema and CLI. Dishonest copies fail both. No RPC. No host read. `observe/client.py` untouched. |
| **Verification** | Soft GATE **pending** on draft PR. |
| **Current state** | Proposed registration; `schema_looser_than_cli` **CLOSED** for watched fill-sim shapes once merged. |
| **Rollback** | Revert schema `allOf` on fill-sim hot-packet evaluate and drop align contract/tests. |
| **Unresolved** | Soft GATE PASS required before merge. |
| **Implications** | Fill-sim JSON Schema matches CLI honesty caps; no change to runtime CLIs or EXP-006 harness. |
| **Pointers** | [PAPER-FILL-SIM-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md](PAPER-FILL-SIM-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md), [PAPER-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md](PAPER-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md) |

---

## EDL-010 — Host-local dry-run receipt capture (Proposed)

| Field | Value |
| --- | --- |
| **ID** | EDL-010 |
| **Date** | 2026-09-24 |
| **What changed** | Registered **Proposed** `paper-host-local-dry-run-receipt-capture-v0`: [PAPER-HOST-LOCAL-DRY-RUN-RECEIPT-CAPTURE-V0.md](PAPER-HOST-LOCAL-DRY-RUN-RECEIPT-CAPTURE-V0.md), [paper-host-local-dry-run-receipt-capture-v0.schema.json](paper-host-local-dry-run-receipt-capture-v0.schema.json), five capture fixtures, `python -m tools.paper_host_local_dry_run_receipt_capture_v0` (example / validate). The #53 dry-run CLI is called, not rewritten. |
| **Why** | #54 left host dry-run receipt capture waiting. Incomplete-RPC honesty on the #53 path needs citeable refuse and receipt records: what was refused, what was recorded, and what is knowable at T. |
| **What was tested** | `python3 -m unittest tools.test_paper_host_local_dry_run_receipt_capture_v0`. Honest captures pass schema and CLI. Dishonest copies fail both. No RPC. No SSH. No `/var/lib/mal` read. `observe/client.py` untouched. |
| **Verification** | Unit tests pass. `sealed_book_rpc_slice` stays `incomplete`. `closed_book_claim` stays false. `measure.kind` stays `none`. Graph stays cold. A host-path refuse does not embed host bytes. |
| **Current state** | Contract is **Proposed**. Not a scored measure. Not an executed host dry-run. Soft GATE is required before merge. Soft watches stay non-blocking (`blocking=false`). `global_95bps` and `launchlab_init` stay Proposed. |
| **Rollback** | Revert this registration. The #53 CLI, parent CLIs, and `observe/client.py` are untouched. |
| **Unresolved** | Sealed book stays incomplete. No invented EV. No observe-wiring. |
| **Implications** | Proof cites these captures for the #53 refuse and receipt shapes only. Merge is not a sealed-book close and not alpha. |
| **Pointers** | [PAPER-HOST-LOCAL-DRY-RUN-RECEIPT-CAPTURE-V0.md](PAPER-HOST-LOCAL-DRY-RUN-RECEIPT-CAPTURE-V0.md), [PAPER-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md](PAPER-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md), [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md) |

---

## EDL-009 — Paper incomplete-RPC honesty schema align (Proposed)

| Field | Value |
| --- | --- |
| **ID** | EDL-009 |
| **Date** | 2026-09-24 |
| **What changed** | Registered **Proposed** `paper-incomplete-rpc-honesty-schema-align-v0`: [PAPER-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md](PAPER-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md). Tightened [hot-packet-v0.schema.json](hot-packet-v0.schema.json), [paper-evaluate-hot-packet-v0.schema.json](paper-evaluate-hot-packet-v0.schema.json), and batch source-row names in [paper-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json](paper-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json). Scoreboard and host-local schemas already const-refused the same honesty bits; tests cite them. No new runtime schema. No CLI rewrite. |
| **Why** | JSON Schema is the citeable shape and was looser than the paper-chain CLIs on closed book, measure kind, host-path open claims, and numeric horizons read as returns. |
| **What was tested** | `python3 -m unittest tools.test_paper_incomplete_rpc_honesty_schema_align_v0`. Honest #49–#53 fixtures pass schema and CLI. Dishonest in-memory copies fail both. No RPC. No `/var/lib/mal` read. `observe/client.py` untouched. |
| **Verification** | `schema_looser_than_cli` is **CLOSED** for those dishonest shapes. `sealed_book_rpc_slice` stays `incomplete`. `closed_book_claim` stays false. `measure.kind` stays `none`. Graph stays cold. |
| **Current state** | Contract is **Proposed**. Not a scored measure. Soft GATE is required before merge. Watches that stay true stay non-blocking. `global_95bps` and `launchlab_init` stay Proposed. |
| **Rollback** | Revert this registration and the parent schema diffs. Parent CLIs and `observe/client.py` are untouched. |
| **Unresolved** | Sealed book stays incomplete. Host dry-run receipt capture still waits. No invented EV. |
| **Implications** | Proof can cite schema validation for the honesty refusals the CLIs already made. Merge is not a sealed-book close and not alpha. |
| **Pointers** | [PAPER-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md](PAPER-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md), [PAPER-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md](PAPER-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md) |

---

## EDL-008 — Host-local sealed JSONL dry-run receipt, incomplete RPC (Proposed)

| Field | Value |
| --- | --- |
| **ID** | EDL-008 |
| **Date** | 2026-09-23 |
| **What changed** | Registered **Proposed** `paper-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0`: [PAPER-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md](PAPER-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md), [paper-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json](paper-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json), synthetic receipts, `python -m tools.paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0` (example / validate / receipt). Parent stamp `243e11b` is not rewritten. |
| **Why** | Scout asked for a citeable operator-local dry-run shape: the merged paper-batch CLI against host-local sealed observe JSONL for `2026-09-20` / `2026-09-21` on `mal-core-vnic`, with incomplete-RPC honesty held even when a receipt notes `host_jsonl_read=true`. |
| **What was tested** | `python3 -m unittest tools.test_paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0`. Receipt CLI validate / receipt on synthetic fixtures. No RPC. No SSH. No `/var/lib/mal` read. No host extract in git. `observe/client.py` untouched. |
| **Verification** | Unit tests pass. `sealed_book_rpc_slice` stays `incomplete`. `closed_book_claim` stays false. `measure.kind` stays `none` when `host_jsonl_read` is true. Graph stays cold. DEC-007 both arms unchanged. No measure exit. |
| **Current state** | Contract is **Proposed**. Not a scored measure. Not an executed host dry-run. Soft GATE is required before merge. Soft watches from #52 stay non-blocking (`blocking=false`). `global_95bps` and `launchlab_init` stay Proposed. |
| **Rollback** | Revert this registration. The parent batch CLI, parent artifact, and `observe/client.py` are untouched. |
| **Unresolved** | No observe-wiring, no encoder promote, no enum production lock, no Discovery / Graph revive, no EXP-002c retune, no filled `Δ_exec`, no closed sealed book, no executed host result in git. |
| **Implications** | Proof cites this receipt for the host-local dry-run shape only. Merge is not a sealed-book measure and not alpha. |
| **Pointers** | [PAPER-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md](PAPER-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md), [PAPER-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md](PAPER-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md), [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md), [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md) |

---

## EDL-007 — Oracle sealed-day paper batch, incomplete RPC (Proposed)

| Field | Value |
| --- | --- |
| **ID** | EDL-007 |
| **Date** | 2026-09-23 |
| **What changed** | Registered **Proposed** `paper-batch-oracle-sealed-day-incomplete-rpc-v0`: [PAPER-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md](PAPER-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md), [paper-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json](paper-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json), synthetic `2026-09-20` / `2026-09-21` JSONL, `python -m tools.paper_batch_oracle_sealed_day_incomplete_rpc_v0` (example / validate / batch). |
| **Why** | Proof needs a citeable day-aligned paper path from sealed observe JSONL through hot-packet, paper-evaluate, and paper-scoreboard, with incomplete-RPC honesty explicit. |
| **What was tested** | `python3 -m unittest tools.test_paper_batch_oracle_sealed_day_incomplete_rpc_v0`. Fixture CLI validate / batch. No RPC. No host JSONL. `observe/client.py` untouched. |
| **Verification** | Unit tests pass. Both days keep runner and reject. `sealed_book_rpc_slice` stays `incomplete`. `closed_book_claim` stays false. Graph stays cold. Horizons stay null. No measure exit. |
| **Current state** | Contract is **Proposed**. Not a scored measure. Soft GATE is required before merge. Soft watches stay non-blocking. `global_95bps` and `launchlab_init` stay Proposed. |
| **Rollback** | Revert this registration. Sealed `ingest_hot` rows and `observe/client.py` are untouched. |
| **Unresolved** | No observe-wiring, no encoder promote, no enum production lock, no Discovery / Graph revive, no EXP-002c retune, no filled `Δ_exec`, no closed sealed book, no executed host result in git. |
| **Implications** | Proof cites this batch for a synthetic day-aligned projection only. Merge is not a sealed-book measure and not alpha. |
| **Pointers** | [PAPER-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md](PAPER-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md), [PAPER-SCOREBOARD-SEALED-FIXTURE-V0.md](PAPER-SCOREBOARD-SEALED-FIXTURE-V0.md), [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md), [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md) |

---

## EDL-006 — Paper scoreboard on sealed-day fixtures (Proposed)

| Field | Value |
| --- | --- |
| **ID** | EDL-006 |
| **Date** | 2026-09-23 |
| **What changed** | Registered **Proposed** `paper-scoreboard-sealed-fixture-v0`: [PAPER-SCOREBOARD-SEALED-FIXTURE-V0.md](PAPER-SCOREBOARD-SEALED-FIXTURE-V0.md), [paper-scoreboard-sealed-fixture-v0.schema.json](paper-scoreboard-sealed-fixture-v0.schema.json), synthetic fixtures, `python -m tools.paper_scoreboard_sealed_fixture_v0` (example / validate / score). |
| **Why** | Proof needs a citeable local-set count of `paper_evaluate_hot_packet_v0` stamps against a checked-in sealed-day expectation, with both arms retained and no invented EV. |
| **What was tested** | `python3 -m unittest tools.test_paper_scoreboard_sealed_fixture_v0`. Fixture CLI validate / score. No RPC. No host JSONL. `observe/client.py` untouched. |
| **Verification** | Unit tests pass. Mixed set keeps the identity reject. All-runner keeps a zero reject row. Horizons and `delta_exec` stay null (`fixture_joined_null_explicit`). `sealed_book_rpc_slice` stays `incomplete`. No measure exit. |
| **Current state** | Contract is **Proposed**. Not a scored measure. Soft GATE is required for Proof. Inherited soft watches stay non-blocking. `global_95bps` and `launchlab_init` stay Proposed. |
| **Rollback** | Revert this registration. Sealed `ingest_hot` rows and `observe/client.py` are untouched. |
| **Unresolved** | No observe-wiring, no encoder promote, no enum production lock, no Discovery / Graph revive, no EXP-002c retune, no filled `Δ_exec`, no closed sealed book. |
| **Implications** | Proof cites this scoreboard for fixture counts only. Merge is not a sealed-book measure and not alpha. |
| **Pointers** | [PAPER-SCOREBOARD-SEALED-FIXTURE-V0.md](PAPER-SCOREBOARD-SEALED-FIXTURE-V0.md), [PAPER-EVALUATE-HOT-PACKET-V0.md](PAPER-EVALUATE-HOT-PACKET-V0.md), [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md) |

---

## EDL-005 — Paper evaluate→runners on hot-packet v0 (Proposed stamp)

| Field | Value |
| --- | --- |
| **ID** | EDL-005 |
| **Date** | 2026-09-23 |
| **What changed** | Registered **Proposed** `paper-evaluate-hot-packet-v0`: [PAPER-EVALUATE-HOT-PACKET-V0.md](PAPER-EVALUATE-HOT-PACKET-V0.md), [paper-evaluate-hot-packet-v0.schema.json](paper-evaluate-hot-packet-v0.schema.json), synthetic fixtures, `python -m tools.paper_evaluate_hot_packet_v0` (example / validate / evaluate). |
| **Why** | LAYA / DEC-006 need a citeable evaluate→runners stamp whose only decode input is `hot_packet_v0`. |
| **What was tested** | `python3 -m unittest tools.test_paper_evaluate_hot_packet_v0`. Fixture CLI validate. No RPC. No host JSONL. `observe/client.py` untouched. |
| **Verification** | Unit tests pass. Sealed cold and enriched Proposed fee / LaunchLab shapes stamp `runner` with null lift. `mint=UNK` stamps `reject` and keeps the arm. Horizons and `delta_exec` stay null (`null_ok`). |
| **Current state** | Contract is **Proposed**. Not a scored measure. `global_95bps` and `launchlab_init` stay Proposed. Graph lift stays null. Soft watches from hot-packet PR #49 stay non-blocking. |
| **Rollback** | Revert this registration. Sealed `ingest_hot` rows and `observe/client.py` are untouched. |
| **Unresolved** | No observe-wiring, no encoder promote, no enum production lock, no Discovery / Graph revive, no EXP-002c retune, no filled `Δ_exec`. |
| **Implications** | Evaluate designers cite this stamp. Merge is not wiring and not a book score. |
| **Pointers** | [PAPER-EVALUATE-HOT-PACKET-V0.md](PAPER-EVALUATE-HOT-PACKET-V0.md), [HOT-PACKET-V0.md](HOT-PACKET-V0.md), [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md), [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md) |

---

## EDL-004 — Hot-packet v0 Proposed paper contract

| Field | Value |
| --- | --- |
| **ID** | EDL-004 |
| **Date** | 2026-09-23 |
| **What changed** | Registered **Proposed** `hot_packet_v0`: [HOT-PACKET-V0.md](HOT-PACKET-V0.md), [hot-packet-v0.schema.json](hot-packet-v0.schema.json), synthetic fixtures, `python -m tools.hot_packet_v0` (example / validate). |
| **Why** | LAYA design needs a citeable capped decode packet (L1 spine, regime locks, graph slots) without continuous observe-wiring. |
| **What was tested** | `python3 -m unittest tools.test_hot_packet_v0`. Fixture CLI validate. No RPC. No host JSONL. |
| **Verification** | Unit tests pass. Examples match fixtures. Proposed tags rejected on sealed overlay. Burst slot and non-null H-G4 rejected. |
| **Current state** | Contract is **Proposed**. `global_95bps` and `launchlab_init` stay Proposed. Graph default is cold. Sealed book RPC slice stays incomplete. DEC-005 remains draft PR #8. |
| **Rollback** | Revert this registration. Sealed `ingest_hot` rows are untouched. |
| **Unresolved** | Encoder / observe-wiring not authorized. Enum production lock not authorized. DEC-005 not merged. No Discovery promote. |
| **Implications** | Decode designers cite this packet. Do not treat merge as wiring, encoder promote, or a scored measure. |
| **Pointers** | [HOT-PACKET-V0.md](HOT-PACKET-V0.md), [EXP-007e](../EXP/EXP-007e-instr-quote-residual-v0.md), [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md) |

---

## EDL-003 — DEC-011 access LIVE + mal-core-0 paper bootstrap

| Field | Value |
| --- | --- |
| **ID** | EDL-003 |
| **Date** | 2026-09-23 |
| **What changed** | DEC-011 path **LIVE** for Cursor Cloud agents: CF Access Service Auth + Runtime Secrets; smoke `mal-core-vnic` / aarch64 / paper-only. Host bootstrap: `/var/lib/mal` subdirs, `meme_core` ops/state stub schema, sealed JSONL ingest path (user systemd `mal-observe` + run script), healthcheck, on-host `eng/BOOTSTRAP.md`. Lab memory no longer claims agents lack SSH. |
| **Why** | Overnight greenlight: owner implemented Access plumbing; agents must bootstrap the blank workshop without the owner PC. |
| **What was tested** | Agent hop: cloudflared 2026.9.1 Access TCP → SSH ubuntu. Host: layout, `sudo -u postgres` schema apply (or `BLOCKED:needs_db_password`), healthcheck, observe unit. Key fingerprint matched. Temp key/`cloudflared` cleaned after session. |
| **Verification** | See PR + `/var/lib/mal/eng/BOOTSTRAP.md` + `/var/lib/mal/logs/health-latest.json`. EXP-002c facts **unchanged** (no promotion, no optimize-to-gate). |
| **Current state** | Agents **can** SSH via Access. Postgres localhost-only. JSONL = provenance spine. Paper-only. No trading/X keys on host. |
| **Rollback** | Docs: revert this PR. Host dirs/unit/schema: stop `mal-observe`, leave PG data; do **not** open `:22` or public Postgres. Do not delete Always Free resources. |
| **Unresolved** | Helm morning review. Optional real paper-trading utility (ask first). Trading/wallet surface — **no pick**. Backups not scheduled yet. |
| **Implications** | Human PC is not a hop. Runtime Secret **names** may appear in runbooks; **values** never in git. Access TCP hostname is gated, not public SSH. |
| **Pointers** | [DEC-011](../DEC/DEC-011-cursor-oracle-access-cf-tunnel.md), [ORACLE-HOST-BOOTSTRAP.md](ORACLE-HOST-BOOTSTRAP.md), [oracle_ssh_smoke.md](../tools/oracle_ssh_smoke.md), [sql/meme_core/](../sql/meme_core/) |

---

## EDL-002 — Cursor↔Oracle access decided (DEC-011)

| Field | Value |
| --- | --- |
| **ID** | EDL-002 |
| **Date** | 2026-09-22 (decision); recorded 2026-09-23 |
| **What changed** | DEC-010 open access ask → **DEC-011 lock.** Chosen: Cloudflare Tunnel (`cloudflared`) on `mal-core-0` + Cloudflare Access gating SSH + dedicated `mal-cursor` ed25519 deploy key (not owner personal). Backup: Tailscale on Oracle VM + ephemeral agent auth keys. Rejected: PC jump host, owner-key share, public Postgres, `:22` to the world. My Machines on-box **parked** as phase-0 default. |
| **Why** | Helm recommendation + Scout/Graph/Proof soft-OK + owner accepted. Need agent path to the live box without a human PC hop or a public DB. |
| **What was tested** | Docs-only / N/A. No tunnel, Access app, or `mal-cursor` key claimed in this PR. |
| **Verification** | Decision recorded. Owner will implement. Agents still have **no** SSH. |
| **Current state** | **Superseded by EDL-003** (path LIVE 2026-09-23). This entry is the decision-time snapshot. |
| **Rollback** | Docs: revert this PR / new DEC. Infra: do **not** tear down Always Free resources. Do not open `:22` or public Postgres as a workaround. |
| **Unresolved** | Owner implementation of tunnel/Access/`mal-cursor`. Optional real paper-trading utility (ask first). Trading/wallet execution surface (Axiom / Phantom / etc.) — **no pick**. |
| **Implications** | Postgres remains localhost-only (never a public/tunneled DB). JSONL stays provenance spine. Human PC is not a hop. No owner personal key to agents. |
| **Pointers** | [DEC-011](../DEC/DEC-011-cursor-oracle-access-cf-tunnel.md), [DEC-010](../DEC/DEC-010-oracle-phase0-handoff-autonomy.md), [ORACLE-PHASE0-HANDOFF.md](ORACLE-PHASE0-HANDOFF.md) |

---

## EDL-001 — Oracle Phase 0 host provisioned (handoff)

| Field | Value |
| --- | --- |
| **ID** | EDL-001 |
| **Date** | 2026-09-23 |
| **What changed** | Lab memory: `mal-core-0` **pending** → **provisioned and verified**. DEC-010 locks inventory, JSONL vs Postgres roles, manager/Cursor workflow, DM status-card rule, no-PC-dependency access, escalate-before-spend/security, paper-only. |
| **Why** | Vaan provisioned Always Free inventory and handed autonomy to Grok+Cursor within fences. Pre-create DEC-009 text was stale. |
| **What was tested** | Owner-verified: SSH baseline, NSG, Postgres 16.15 localhost bind, `mal_app` on `meme_core`, data dir on `/var/lib/mal`. This PR is **docs-only** (no agent SSH). |
| **Verification** | Owner: host live, paper-only, no wallet/trading keys on box. Agents: **no** host access yet. |
| **Current state** | Secure blank Oracle workshop. Access architecture **locked in DEC-011** (owner implementing; tunnel not live). Then bootstrap dirs/schema/ingest. EXP-002c facts **unchanged**. |
| **Rollback** | Docs: revert that PR. Infra: do **not** destroy Always Free resources without Vaan. Do not revert to “laptop is 24/7 host.” |
| **Unresolved** | Owner implementation of DEC-011. Optional real paper-trading utility (ask first). Trading/wallet execution surface research (Axiom / Phantom / etc.) — **no pick**. |
| **Implications** | JSONL stays provenance spine; Postgres is ops/state only. Human PC must not be a permanent networking hop. DM Vaan a Cursor status card on every Cursor launch. |
| **Pointers** | [DEC-011](../DEC/DEC-011-cursor-oracle-access-cf-tunnel.md), [DEC-010](../DEC/DEC-010-oracle-phase0-handoff-autonomy.md), [DEC-009](../DEC/DEC-009-oracle-always-free-phase0-host.md), [ORACLE-PHASE0-HANDOFF.md](ORACLE-PHASE0-HANDOFF.md), [ORACLE-ALWAYS-FREE-BOM-v0.md](ORACLE-ALWAYS-FREE-BOM-v0.md) |
