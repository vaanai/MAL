> **TEMPLATE DRAFT, NOT A DEC, NOT IN FORCE. Lives in `ARTIFACTS/lab/c0-drafts/` on purpose: the executor and bridge glob `DEC/` for the owner line, so a placeholder must never sit in `DEC/`. Drafted 2026-10-26 (records only; READINESS N8, H7). Nothing here is a result. No HV4 output, ledger, decision row, `bots.json`, job log or status was opened; no book has cleared the promotion gate. This template says nothing about whether any HV4 arm makes money.**
>
> **MERGE AS `DEC/DEC-0NN-hv4-<arm>-c0-canary.md`** (for example `DEC-0NN-hv4-con-x-c0-canary.md`; the name must contain "canary" and must not contain "template"). The H5 executor's rule (`canary_record_refusal`, `tools/h5_arena_executor.py:418`) is the model: glob `DEC/DEC-*canary*.md`, regular file, and a bare line `OWNER_CANARY_CONFIRMED: bot_id=<ARM>` (own line, no bullet, no backticks, nothing after the id).
> **How the HV4 adapter finds it: NOT BUILT.** `tools/arena_live_bridge.py` and `tools/hv4_live_c0.py` at main contain no canary-record lookup and do not read `DEC/`. QP-PRERULING-1026 says that for the HV4 adapter "a root-file design from the start is acceptable", and PR 4 of the adapter is reading the record from a root-owned file instead. Both must be satisfied until PR 4 is final: keep the file in `DEC/` under the name above (the manager and reviewers read it there) and, if PR 4 reads a root-owned copy, Helm installs a byte-identical copy at the path PR 4 names, with its sha256 recorded here: `<AT-EVENT: PR 4 path, and the sha256 of the DEC file at the merge sha>`. Do not assume either mechanism until PR 4 merges.
>
> **How to use.** After a recorded PASS for one arm (and only then), a builder copies this file to `DEC/DEC-0NN-hv4-<arm>-c0-canary.md`, keeps the variant that applies (delete the other), fills the markers, and opens a PR. Text that says "DEC-032 §x" means the copy of DEC-032 is carried verbatim; this file is the overlay of declarations that DEC-032 and the #908 QP conditions require in the copy before the first send. QP OK on the exact head and a reviewer verdict on the PR come before the merge; workers do not merge.
>
> **Markers.**
> - `<FILL at PASS>`: a figure from that arm's read (read instant, n, dates, mean, CI, p, ex-best-day, worst-in-slot, N, Holm).
> - `<AT-EVENT: ...>`: a fact that does not exist until a later act (DEC number, PR head, review ids, install sha, job ids, wallet and owner public addresses, alert target, end_ms, dated owner lines). Not a read figure.
> - `<ARM>`: one of `hv4-CON-X`, `hv4-BIG-X`, `hv4-CON-F`, `hv4-BIG-F`.
> - `[X]` / `[F]`: text for the variant named; delete the other.
>
> **Sources.** QP-RULING-1025-hv4-read-rule R1 to R3 (R2.2 (a) to (g)); the #908 QP verdicts (condition list at head b6c73712, comments 6401680939 and the verdict after it) and #913 (a23bd7ab); DEC-032 §0 to §8 with Am.1 and Am.2; DEC-031 Am.15 (items 1 to 6); `tools/hv4_live_c0.py`, `tools/arena_live_bridge.py`, `tools/hv4_arms.py`, `docs/runbooks/arena-live-bridge.md`; `ARTIFACTS/lab/c0-readiness-2026-10-25.md` §7.

# DEC-<AT-EVENT: next free DEC number>: Arena live canary, HV4 `<ARM>` (`FILL:bot_id`), copy of DEC-032 (DRAFT)

| Field | Value |
| --- | --- |
| **Status** | DRAFT COPY of DEC-032 for `<ARM>`. Becomes a canary DEC only when every marker is filled, QP OK on the exact head, a reviewer verdict on the PR, the owner lines of section 6 are recorded from the owner's own message, and the manager merges. No book has cleared the promotion gate. A canary is an "unpromoted execution measurement" (DEC-032 §0). |
| **Decider** | Vaan (owner), for each live send, funding, tier and the fee line of section 4. No agent message, manager message or PR text is the owner's consent. |
| **Builds on** | DEC-032 (template, Am.1, Am.2); DEC-031 §2, Am.3 item 6, Am.6, Am.15; QP-RULING-1025-hv4-read-rule R2.2; HV4 registration `ARTIFACTS/lab/hv4-registration-1023.md` |
| **Does not amend** | The promotion gate, DEC-031's deciding cell, the read rule (Am.15), the confirmation rule, any seal, the ledger, or any sibling HV4 bot. |

## 1. The arm and the read it stands on
| Field | Value |
| --- | --- |
| `FILL:bot_id` | `<ARM>` |
| Arm eligibility (R2.2 (a)) | CON-F, CON-X, BIG-F or BIG-X only. L arms need a DEC-032 amendment first; CON-P and every R arm are never canaried. Enforced in code: `hv4_live_c0` refuses `-L`, `-P`, `-R` and any `hv4-` id without profile `hv4_c0`. |
| `FILL:frozen_sha` | 9ed4c5ff63935696b2a89150e13565e123047754 (`FROZEN_SHA`, `tools/hv4_live_c0.py`) |
| Config pin | sha256 1e611a5a636dfe6bd563ab5258f6b498cdb8906500ee8bf2f0264a2621621986 (HV4 registration §, compiled in; the `start` row repeats it) |
| `FILL:clock_start_ms` | 1792886400000 (2026-10-25T00:00:00Z) |
| Read | DEC-031 Am.15 margin read, B = `<FILL at PASS: read instant, from the write-once hv4_read_instant record>`; `FILL:n` `<FILL at PASS>`, dates `<FILL at PASS>` |
| In-sample figures (labelled in-sample, selected) | mean `<FILL at PASS>` SOL per trade, CI90 lower bound `<FILL at PASS>`, p `<FILL at PASS>`, ex-best-day total `<FILL at PASS>` SOL; multiplicity N `<FILL at PASS>`, Holm rank `<FILL at PASS>`; worst-in-slot mean `<FILL at PASS>`, CI90 lower bound `<FILL at PASS>` (from `<bot_id>.wis.json`; "not measured" or a mean at or below 0 means no proposal) |
| Deciding stake | 0.25 SOL (HV4 deciding cell, `hv4_arms.SIZES` 0.25 / 0.5 / 1.0; confirm from the registry at PASS) |
| QP OK on the read | `<AT-EVENT: PR/comment id, exact files and head sha>` |
| Mint fetch | `<AT-EVENT: job id, mints, close-blocked count, credits>` (report-only for HV4) |
| Sell simulate | `<AT-EVENT: tools/arena_sell_simulate.py output on one recorded Token and one Token-2022 mint, sell closes the base ATA>` |

All figures above are in-sample. A screen pass is the best of N for selection. HV4 has 9 registered bots; the read states N. Entries at or after 1794096000000 (2026-11-08T00:00Z) do not count in any read; HV4 END is 2026-11-08T13:05Z.

## 2. Limits (DEC-032 §3 C0 column; code constants in `tools/hv4_live_c0.py`; config may only tighten)
| Item | Value |
| --- | --- |
| Stake per entry | S = min(card stake, 0.05 SOL) = **0.05 SOL** (50,000,000 lamports). The card sizes 0.25 / 0.5 / 1.0 SOL all map to S: a declared size mapping, not a cap (R2.2 (g)). The twin stake equals S by construction. Impact at the deciding stake is NOT tested. |
| Max open | 2 |
| Buy attempts per UTC day | 30 (guard reverts count) |
| Daily stop (realized loss in a UTC day) | 0.10 SOL |
| Total stop (committed loss, DEC-032 §3 and the bridge: realized loss floored at 0, plus each open, pending or in-flight order at stake + TRIP_FEE_RESERVE 215,000 + rent reserve 2,100,000 + SELL_FEE_RESERVE 155,000, plus the new buy's worst case; must be at most the stop) | 0.20 SOL |
| Wallet | funded 0.35 to 1.00 SOL; floor = max(0.05, funded - 0.25); worst case 0.25 SOL |
| **Per-trade loss latch (declared; not in DEC-032 §5)** | `per_trade_loss_lamports` default **15,000,000 lamports = 0.015 SOL = 30% of S**. One closed trade losing more than that latches `per_trade_loss_cap` (no new buys; exits of open positions continue) until the manager clears it. It is tighter than §5, so allowed; it is declared here because one stop-loss trip can end the canary. `<AT-EVENT: keep 15,000,000, or a lower value; a higher value is refused by the code>`. [BIG arms] A BIG paper hard stop is -40%, which exceeds 30% of S, so one BIG hard-stop exit trips the latch by design; report it as such. |
| Duration | at most 14 days from the first send, by an explicit `end_ms` (section 7) |

Fail closed, files, paper twin, halts (DEC-032 §5), parity P1 to P7 and the daily tie-out (§6) are carried from the DEC-032 copy unchanged.

## 3. R2.2 (a) to (g): declarations made before the first send
1. **(a) Arm.** `<ARM>` is one of CON-F, CON-X, BIG-F, BIG-X. [F] The F arm is a frozen LLM trader (no nightly rewrite), so DEC-032 §1 item 4 (any prompt change is a new bot_id) holds.
2. **(b) Pins.** bot_id, frozen_sha 9ed4c5ff63935696b2a89150e13565e123047754 and config pin sha256 1e611a5a… are compiled in; the `start` row repeats them; a mismatch latches `frozen_sha_mismatch`. [F] The API-returned model id must be `claude-haiku-5-5`, or one single id for the whole window; a change of model id ends the canary (hard halt, new bot_id). [X] No model; rule-book arm, `FILL:executor` pins the code path only.
3. **(c) Own directory only, read-only bind.** The bridge reads `decisions/<ARM>/` only, on a read-only bind mount of that one directory (`<AT-EVENT: Helm bind-mount path and mode, HELM-SHEET>`). Siblings are not mounted. **Declared residual:** a bind mount, or any arena alias with `st_nlink == 1` and no symlink, is invisible to the code's path checks (`start_refusal`, `<root>/decisions/<bot_id>` realpath check). The marker file `SYNTHETIC_FIXTURE_ROOT` must never be put in a root that could hold HV4 rows. The refusal at the read-record gate is a code check; the bind mount is a Helm act and its correctness is the manager's check, not the code's.
4. **(d) Rows only after the recorded read instant.** Only rows whose entry decision is at or after the instant the read was recorded (`<AT-EVENT: recorded instant>`) become orders; earlier rows are chain-verified only. Before the read record no bridge process, dry or live, reads any HV4 decision row (R2.1); pre-read tests use synthetic fixtures or a StubModel root only.
5. **(e) Siblings untouched.** No HV4 bot is stopped, restarted or re-pinned because of canary output until every sibling read is recorded or ended, except the pinned 2026-11-07T12:05Z handover and Am.6 item 4 crash restarts. Every sibling read discloses the canary's start instant `<AT-EVENT: first send instant>`.
6. **(f) Confirmation exposure.** From the first send the canary wallet is public and watched in real time, and the arm's decisions are acted on, so the confirmation read for `<ARM>` is no longer blind. Declared and allowed (DEC-032 §7; DEC-031 Am.3 item 6 "In parallel"); it does not change any read. A canary halt stops the canary only, never a change to the paper book. Every report carries "unpromoted execution measurement". The owner is told.
7. **(g) Universe.** The canary universe equals the read's: quote WSOL and V in [17.5e9, 17.7e9] lamports, inclusive. At 9ed4c5ff `hv4_cards.py:458-465` applies the same V band, so no subset declaration is needed (QP-RULING-1025 R2.2 (g) corrects the earlier desk note). One residual: a missing V is `v_missing` in HV4 but `universe_v_out_of_range` in the canary; both are logged skips, and a universe skip is never a cap and never a reason to pick another decision. Token-2022 extensions outside {metadataPointer, tokenMetadata}, or any freeze authority, are refused before the send.
8. **Seal statement (QP's OK on the read).** HV4 trades the arena tape on lane G; no H5 / Look-2, C1-NF or D2/D3/D4 class applies (lane H is off, Note N2); this is a statement for QP's OK and is not a waiver of R2.1. QP statement: `<AT-EVENT: comment id>`; ledger checked `<AT-EVENT: date -u>`.

## 4. Fee, escalation and the owner's fee line
- **Compiled pin.** buy 55,000 / sell 55,000 / escalated 150,000 lamports of priority, plus the 5,000 base fee per send (`PRIORITY_PIN` in `tools/hv4_live_c0.py`; equal to `h5_executor.H5_DEFAULT`, a test asserts it). This is the compiled default, not the "p155" pin (160,000 per send), which is a different config and would need its own review. Reserves: one trip 215,000 (60,000 buy + 155,000 escalated sell), one open position 155,000.
- **Sell escalation (DEC-032 §3).** `min_out` 0.85 of the quote; the sell goes at 55,000 priority until it has reverted or expired twice (`ESCALATE_AFTER_REVERTS` = 2) **or** 345,000 ms have passed since the first sell attempt (`ESCALATE_AFTER_MS`, the time bound; DEC-032 §3 names none, so 345 s is taken from `h5_executor.ESCALATE_S`, a declared choice); then it resends at `min_out` 0.65 with the escalated 150,000. Never `min_out` 0. One revert alone stays at 55,000. A stuck position is escalated too. The twin is priced at the per-send fees actually sent, escalated resends counted as sent. `<AT-EVENT: the install sha X must contain the PR that builds the live sell ladder and removes hv4_c0_incomplete; until it does, this paragraph is a precondition, not a fact>`.
- **Gap to the registered stack.** The registered stack (DEC-031-rev2) is 505,000 per send; a round trip at 60,000 per timed send is 890,000 lamports = 178 bps of S less than the registered stack. The read and the confirmation stay at the registered stack; the canary changes neither. Residual declared: an escalated or emergency sell resend costs 95,000 more than the twin's 60,000 send.
- **The installed executor's own priority is 500,000.** The bridge's installed `LiveExecutor` priority (DEC-032 §4) is 500,000. 55,000 is "a priority other than the executor's installed one", so DEC-032 §4 requires a reviewed config change named here and **the owner's own line naming the fee**. Neither a manager decision nor DEC-032 Am.1 item 3 (the standing OK names no fee) is that line. See section 6, line B.
- **Mismatch risk.** If the installed 500,000 priority were left in place, each send would cost about 505,000 lamports against the twin's 60,000, which is 2 x 445,000 = 890,000 lamports = 178 bps of S = 0.05 SOL per round trip in live's disfavour (arithmetic, not measured). The fee pin must therefore show 55,000 / 55,000 / 150,000 on the sent transactions before the first buy, from the first dry run: `<AT-EVENT: Helm dry-run output>`.

## 5. Tier, files and install
- **TIER reads T1, not T0.** DEC-032 §3 "Files" says a valid TIER is "T0 here". The bridge is fail-closed and a C0 start needs the file to read exactly `T1` (`TIER_NAME`); a literal `T0` file refuses the start. This is a change from the template, listed in "Changes from the template" below. The compiled C0 table is the ceiling whatever the file says (min per field).
- Files (root:root 0644, no symlink, root parent): `/etc/mal-arena-live/TIER` = `T1`; `/etc/mal-arena-live/LIVE_OK` with content exactly `<ARM>`; the wallet-wide `/var/lib/mal-live/STOP` remains the kill switch (decision on its current state: `<AT-EVENT: manager's written STOP decision, Helm>`). The manager takes no STOP, HALT or unit action; Helm does.
- Install: `<AT-EVENT: install sha X>` under `/opt/mal-arena-bridge/X/` on mal-fast-0; dry run of at least 1 h with 0 build errors `<AT-EVENT: job id>`; md5 "orders equal rows" decision-equivalence proof on a SYNTHETIC log only `<AT-EVENT: job id>`. Parity P1 to P7 wired into the bridge gate, feed-gap and A3 halts, tie-out exporter, the sell-and-close pin (`SELL_BUILDER_PIN`, rechecked at every live start), and the live port sending the order's S (not `cfg.stake_lamports`) are preconditions of X: `<AT-EVENT: PR numbers with QP OK>`.
- Decision-row transport: the HV4 rows live on mal-research-0 and the bridge runs on mal-fast-0. `<AT-EVENT: chain-preserving forwarder or bridge location, measured hop latency; the bridge refuses entries older than 5 s>`.
- Twin: `arena_paper` at S, nofail leg, 1.9 s latency, **E1 excluded decides**, E1-included reported beside it (DEC-032 §5). Pinned; changed only by amendment.
- Wallet: a fresh canary wallet `<AT-EVENT: public address>`; withdraw holder Helm; funded `<AT-EVENT: 0.35 to 1.00 SOL, owner>`; starting balance `<AT-EVENT>`.
- **Owner deposit addresses (DEC-032 Am.2, `FILL:owner_deposit_addresses`).** Only finalized transfers into the canary wallet from these pinned public addresses count as deposits: `<AT-EVENT: owner public address(es)>`.

## 6. Owner lines (recorded verbatim from the owner's own message; nobody else fills them)
A. The standing C0 OK (DEC-032 Am.1 item 3), quoted verbatim, then on a line of its own with nothing after the id, the line the bridge matches exactly:

OWNER_CANARY_CONFIRMED: <AT-EVENT: at the merge, replace this ENTIRE line with the bare line OWNER_CANARY_CONFIRMED: bot_id=<ARM> (own line, no bullet, no backticks, nothing after the id), only when the manager uses the standing line for this arm>

B. **The owner's fee line.** `<AT-EVENT: the owner's own words naming the fee: buy 55,000 / sell 55,000 / escalated 150,000 lamports of priority plus the 5,000 base fee per send, and that this replaces the executor's installed 500,000. Notebook id.>` Without this line no live send.

C. The ownership of the wallet funding and of Helm's install stays separate acts (Am.1 item 3 "Does not cover").

## 7. Duration and `end_ms`
- At most **14 days from the first send** and not past HV4 END: `end_ms` <= min(first send + 14 d, 1794143100000 = 2026-11-08T13:05:00Z). `FILL:end_ms`: `<AT-EVENT: explicit value, set in config by Helm>`.
- **Declared code gap:** for profile `hv4_c0` the code accepts any positive `end_ms` (QP #908 condition d); the 14-day cap is not enforced by the bridge. Until a code cap lands, the value is verified by the manager and by Helm's `--status` print before the first buy, and a start with a larger value is a hard halt by this DEC. `<AT-EVENT: the PR that enforces the cap, if built>`.
- Extension is the owner's decision. Entries at or after 1794096000000 (2026-11-08T00:00Z) are outside the HV4 read and are not requested by this DEC.

## 8. Variant-specific text
**[X] Rule-book arm (`hv4-CON-X` or `hv4-BIG-X`).** The decision is the persona's v0 plan, in code, no model. CON: stop -7%, target +12%, time stop 45 min; code limits hard stop -15%, max hold 120 min, max open 4, 4 entries per hour. BIG: stop -25%, target +100%, trail 30%, time stop 360 min; code limits hard stop -40%, max hold 720 min, max open 6, 3 entries per hour (`tools/hv4_arms.py` PERSONA_SPECS at the frozen sha; confirm the values at 9ed4c5ff). The canary caps (max open 2, 30 buys a day, S) are tighter than the persona's; a decision skipped for the canary's caps is logged with its reason and never replaced by another decision. The bridge holds a position at most `max_hold_ms` (`<AT-EVENT: config value>`, at least the persona's max hold so the paper exit row can run).
**[F] Frozen-LLM arm (`hv4-CON-F` or `hv4-BIG-F`).** The decision is the frozen Haiku trader at the frozen prompt and playbook v0; model id `claude-haiku-5-5` for the whole window (section 3 item 2). The API is used by the HV4 runner, not by the bridge. Cost caps of the runner are unchanged. A reply more than 10 s after `as_of_ms` is discarded (runner rule). The canary never feeds the model its own fills. Declared: the F arm's decision latency adds to the 5 s signal-age bound; the match test needs landing p50 at most 1.9 s from the decision instant.

## Changes from the template (listed and justified, as DEC-032's status line requires)
1. §3 "Files" and §8 step 7: `TIER` T0 -> **T1** (bridge fail-closed; a T0 file refuses). Same change as the Q50 copy.
2. Section 4: the fee 55,000 / 55,000 / 150,000 differs from the installed 500,000 executor priority; it needs the owner's line B.
3. Section 2: the per-trade loss latch of 30% of S is declared (tighter than DEC-032 §5).
4. Section 7: the 14-day cap is by explicit `end_ms`, with the code gap declared.
5. Section 3 (c): the bind-mount residual is declared.
No limit is raised, no halt threshold is loosened.

## Open points (not facts)
- Everything marked `<AT-EVENT>` is unbuilt or unrecorded at drafting; in particular the HV4 live adapter items (twin at S, parity wiring, tie-out exporter, A3 and feed-gap halts, systemd unit, md5 proof, live sell ladder, live port S, removal of `hv4_c0_incomplete`) and the research-0 to fast-0 decision-row transport were not built when this was written (READINESS §2). This template is valid only for an install sha that contains them.
- The 0.20 SOL total stop and halts P3, P6, P7 are the manager's and quant-proof's values; the owner has not stated them (Am.1 item 3).
- Per-trade loss latch value and the 345 s escalation bound are declared choices for QP to confirm.
