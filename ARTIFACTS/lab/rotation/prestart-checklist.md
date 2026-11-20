# DEC-036 rotation lane: pre-start checklist for the first book (draft)

Worker draft for manager24, 2026-11-20 (`date -u` 16:40:41Z), at main 02afa4fc. Records only: this file starts, funds or configures nothing. No book has cleared the promotion gate, and nothing here says any book makes money.

Status words: **done** (with its record), **owed** (manager, worker or QP work), **owner** (only the owner can do it), **Helm** (Helm's install work, asked through a HELM-ASK).

Candidates named by the manager: `osb-ce1` and `osb-e1` (open sim books, DEC-035 Am.9). Proposed rotation bot_ids: `rot-ce1-v1` and `rot-e1-v1` (cards in `docs/rotation/books/`).

| # | Condition | Status | Citation / evidence |
| --- | --- | --- | --- |
| 1 | DEC-036 approved, §11 items 1-4 numbers set | done | DEC-036 Approval 1 (owner, 2026-11-18T18:34:17Z) |
| 2 | §6 calibration targets (20% / 5% over 100 trades, n0 25, winsor 2.5/97.5) confirmed per book | owed (QP) | DEC-036 Approval 1 "Limits of this record" bullet 1; §6 item 4 "[owner to set; QP to confirm]" |
| 3 | Executor code PR (§9 items 1-6, 10) merged with T1 verdicts and md5 proof | done | PR #1466, main 0c5e4d76 (QP VERDICT 285f3b7e OK, REVIEWER VERDICT 285f3b7e APPROVE; DSL md5 f6260e64 equal) |
| 4 | Per-book decay monitor (§9 item 9) merged | done | PR #1468, main 0cf0f6ef (QP VERDICT ed172ac1 OK, REVIEWER VERDICT ed172ac1 APPROVE); QP m6 on #1466 made it a start condition |
| 5 | Rotation wallet created, key root-only | done | HELM-REPLY-1119-1 §1: public key `B6r9nd3cwc45yVJsVtYScDhH8cdiC97Geq8SZv2Eye6A`, unfunded |
| 6 | Executor tree installed | done (at 0cf0f6ef) | HELM-REPLY-1119-1 §2: `/usr/local/lib/mal-rotation-lane/0cf0f6ef…`, clean. A new tree is needed if the own-code port of row 12 lands (a HELM-ASK after its T1 PR, §9 item 7) |
| 7 | Venv that imports the executor | owed (Helm; manager's choice for the GO) | HELM-REPLY-1119-1 §2: the example's `mal-h5-exec/venv` lacks numpy; manager24 state 2026-11-19T04:53:00Z chose a dedicated `/usr/local/lib/mal-rotation-lane/venv` |
| 8 | Template unit installed, read-only on FLOOR's ledger | done | HELM-REPLY-1119-1 §3, §5: unit sha256 dd8a8117…, sandbox `test -w` rc=1 on FLOOR's ledger |
| 9 | Live drop-in and LIVE_OK | Helm | HELM-REPLY-1119-1 "Still open for the later GO" |
| 10 | Rotation wallet funded (FLOOR first) | owner | DEC-036 §4 item 5, §5 item 10 (first funding 1.5 SOL, not an earmark: Approval 1 limits bullet 3); manager24 state 2026-11-20T16:36:09Z "owner ask: SOL earmark for rotation" |
| 11 | Method card per book, complete (§3 items 1-8) | owed: drafts in this PR (`docs/rotation/books/rot-ce1-v1.md`, `rot-e1-v1.md`) | DEC-036 §3; a card alone is T0 with a QP OK line (§7) |
| 12 | **Decision host path (§3 item 7)** | **owed: BLOCKER (code)** | Both books run l1-floor-v2 logic with `TP 1.3` (and CE1 `Q_STAR 1.0e9`). FORGE cannot express FLOOR's rule (floor-siblings-design.md §2). The merged rotation executor follows FORGE DSL host rows only (`RotationLaneBridge(D.DslLaneBridge)`; `spec_sha256` required in `make_config`). `tools/floor_lane_host.py` takes `q_star_lamports` only through l1-floor-v3 (PR #1454) and has no TP setting. Owed: a rule with a pinned TP 1.3, a second keyless FLOOR-style host instance writing to a decision root that the unit can see (`ProtectHome=tmpfs` hides `/home`), and a rotation port that follows that host's decision log. Each is a T1 code PR with an md5 decision-equivalence replay proof (§7, §9 item 1) |
| 13 | **Same-pool overlap with floor-lane-1 (§9 item 6)** | **owed (QP ruling on the sim basis)** | E1 enters exactly FLOOR's entries (same rule, only the exit differs), and CE1's entries include every FLOOR entry (vault ≤ 0.5 SOL is inside vault ≤ 1.0 SOL). With the same-pool refusal ON, the live book trades only pools FLOOR does not hold open. So the board totals (all entries) are not the sim of what the live book would trade. Also a race: both hosts decide on the same print, and the refusal sees FLOOR only after FLOOR's `send_intent` row is written, so some entries can go through on both books. Details are in the cards, §1 item 3 |
| 14 | Sim has run as the card says (§4 item 1) | owed | CE1: pre-registered read from 2026-11-24T00:00:00Z, NOT_DECIDABLE at 2026-12-01T00:00:00Z (QP-RULING-1115-ce1-prereg P4). E1: no pre-registered read; the card sets one (TODO-QP) |
| 15 | CE1 route under its pre-registration | owed (QP ruling) | QP-RULING-1115-ce1-prereg P4: "A pass leads only to an l1-floor-v3 draft plus a DEC-035 amendment (floor-lane-2)". Using CE1 as a DEC-036 rotation book is a different route; QP must rule whether the pre-registration allows it. The manager's plan (state 2026-11-20T16:36:09Z: "CE1 read ~11-24 -> vault1.0 FLOOR switch") would also move FLOOR onto CE1's universe, which grows row 13's overlap to nearly all of CE1's entries |
| 16 | Shrunk net sim mean > 0 under both fail legs, and μ0 > 0 at 0.10 (§4 item 2) | owed (QP): `TODO-QP-CE1-SHRUNK-MEAN`, `TODO-QP-E1-SHRUNK-MEAN`, `TODO-QP-CE1-MU0-0.10`, `TODO-QP-E1-MU0-0.10` | DEC-036 §4 item 2, §6 item 3. Needs per-trade rows (no agent opens them), the overlap basis of row 13 and the latency haircut of row 17 |
| 17 | Believable fills and latency while the control book is SIM OFF: haircut for the live-minus-sim bias (§3 item 3) | owed (QP): `TODO-QP-LATENCY-HAIRCUT` | Board 2026-11-20T16:30:58Z Recon line: FAIL (S1 and S2 INSUFFICIENT n 8 < 10, S4 FAIL n 20), "SIM OFF: not decision-grade". Strict books price entry at 1.9 s (`osb_board.py` header: "latency 1p9"), and FLOOR's live landing p50 is about 3.1 s (osb-oq-v1 card §3 item 1). CE1 and E1 have no latency twin; only osb-a has one (A-lt-a, 3.010 s) |
| 18 | Calibration per book (§6 items 2-4): L, U, μ0 per reachable rung, δ, h_w, h_a, path and sha256 | owed (QP): drafts with TODO-QP tokens in this PR | `docs/rotation/books/rot-ce1-v1.calibration-100000000.draft.json`, `rot-e1-v1.calibration-100000000.draft.json`; schema `rotation-decay-calibration.v1` (`tools/rotation_decay_monitor.py` docstring); fewer than 10 forward sim days with trades on either book, so trades are resampled one by one (§6 item 4) unless QP rules otherwise |
| 19 | Belief record in the book's file (§4 item 3) with QP OK and REVIEWER APPROVE at the exact head (§4 item 4, §7) | owed | drafts in the cards' §10; T1 |
| 20 | Instance config written; its sha256 in the belief record (§4 item 3) | owed (manager) | Config keys: `tools/forge/rotation_lane_executor.py` `TOP_KEYS` / `LANE_KEYS`; outline in each card's §10 |
| 21 | Envelope room (§4 item 5) | owed: books live 0 of 3; month and lifetime room full; wallet unfunded (row 10) | DEC-036 §5 items 6, 7, 9 |
| 22 | SEALS-BRIEF FLOOR LANE READERS line for the rotation executor at its merged commit | owed (manager adds it; draft in this PR) | DEC-036 §9 item 8; QP m5 on #1466; draft: `ARTIFACTS/lab/rotation/seals-brief-reader-line-draft.md` |
| 23 | DEC-035 §7 seal overlap declaration per book before its first live send | owed (manager) | DEC-036 §2 item 6 |
| 24 | §9 test list passing at the installed sha before the first send | owed (manager job on mal-fast-0, in the rotation venv) | DEC-036 "Amends" §2 item 7; `tools/test_forge_rotation_lane_executor.py`, `tools/test_forge_rotation_lane_executor_r1.py`, `tools/test_rotation_decay_monitor.py` |
| 25 | Decay monitor job: `--since-ms` watermark, `--calibration-sha256`, act on the worst exit code | owed (manager, at load) | QP deploy condition C1 on PR #1468 (`tools/rotation_decay_monitor.py` docstring) |
| 26 | CLAUDE.md wording names the rotation wallet key under the root-only rule | owed (manager; workers do not edit CLAUDE.md) | DEC-036 line 13 ("on approval, a CLAUDE.md wording follow-up") |
| 27 | Not a kill-review week | check at load | CLAUDE.md "Never add forward books during a kill-review week" |
| 28 | Load records: state-file line, notebook (kind decision, config sha256), LAB_STATE row, `data/console.json` | owed at load | DEC-036 §7 |

## What blocks a first start beyond the records in this PR
- Row 12 (code): no live decision host or rotation port exists for a FLOOR-rule book with TP 1.3. The merged executor runs FORGE DSL books only.
- Row 13 (overlap): with the same-pool refusal ON, E1 can trade only pools FLOOR does not hold open, so its sim is not the live book. CE1 keeps only its vault (0.5, 1.0] SOL slice plus FLOOR's skips, and none if FLOOR moves to vault 1.0.
- Row 15 (CE1 pre-registration route) and rows 16-18 (QP numbers).
- Rows 7, 9 (Helm) and 10 (owner funding).

Paper and records only. No book has cleared the promotion gate.
