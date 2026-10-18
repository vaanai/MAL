# Arena records, 2026-10-18 (DEC-031 §1.2 and §1.4 pins; written before any book's clock)

Written by manager12 on 2026-10-18 at about 05:45Z (`date -u`). No book has a clock yet, and no arena outcome exists. Nothing here says any book makes money, and no book has cleared the promotion gate.

## 1. Code pins at main `c3dc98f369cc1e7a81b0d0f2302f1b0b49447f15`
sha256 of the file, then the git blob (first 12 hex), then the path.

| sha256 | blob | file |
|---|---|---|
| e91f3f84dbb9a162b6cdb456bac40f3902d73db775924f5df834cf536401abb0 | 23b9ccc2562f | tools/h5_shadow.py |
| b20d51ceb939822ce813ca48b14348e61873277fef54a3d71af660e37fe03328 | b481d1c4c666 | tools/arena_h5_cost.py |
| a3dd6b31c38081b535cb965223623ae5797341b7374b0d3b3196799e79e65c27 | d5a1b59364f4 | tools/arena_h5_score.py |
| b51b1d1057d8745b6891b540bc73c1105c270eb58eed651e1c13de5ca23b8a73 | 698df6dd9ef3 | scripts/research/arena-h5.sh |
| 7a9d424b6584b097ee61e4c7b48bef6e43b317bdd438ba588074057b5828826f | d6969658f9d2 | tools/arena_feed.py |
| 8af345618e51ca480ee7538717aa001b4ad7ed5ef38941faaec812bbe217f639 | 38e4c608115c | tools/arena_state.py |
| 4ddbf4484e8a5b5b3066c6946c529d2d5b06463fc60cea784d561aa7e108b8ce | a847d27ef128 | tools/arena_zst.py |
| 9a23f2aa8bb7f7db13b9d30117787bb319fe6fa9e320a6a845670a22c9cabbbd | df19396aaa2b | tools/arena_paper.py |
| a508ed79ed1cc425af0aa7dcd08e3ef5f8599dd6a45cb5e47cc8a779c6a0cd34 | e2807a908e58 | tools/arena_score.py |
| a39b2ba68dd46b7d3a555fb8e06580718300f354020c1f91280ab7a0e0856778 | 092bbce0854d | tools/arena_stack.py |
| 5d1124331b87cc1d3e7f5777534d8612bbbdabb4359a246fe0edf44c2e2ec88d | 2f111da89992 | tools/arena_feed_delay.py |

Any later change to these files is disclosed in every read it touches, and needs quant-proof's OK on the change (DEC-031 §1.4).

## 2. Parity references and sets
- **H5 books** (`tools/arena_h5_cost.py`). Reference `tools/r4b_book.py`, sha256 `9c20de4073ea22f6cf61cb9528302fa22edbc64fb16685490284c4e5fb6b7513` (DEV-TIME parity only; no arena process imports it, DEC-031 §3.1).
  - Parity set: `tools/fixtures/arena_h5/parity_cases.jsonl` sha256 `934bd08a1dbcb08248980fe77e53812d1d8ad9225344842f3c22fe6353c43614`.
  - Expected: `tools/fixtures/arena_h5/parity_expected.jsonl` sha256 `60f6f3ae2af8ac7412e9a031a34f8fb523094b58038390a4c1bd90a3e57ab72a`.
  - `PARITY.json` sha256 `023acd61db69fd87c7f5841a0850d44bb2346a9bd7c968bd7e41cd3b31a81f82`.
  - Result: 1,838 cases in total (1,824 from the replay and 14 edge cases), 0 mismatches under both strand rules (PR #707).
- **Arena engine** (`tools/arena_paper.py` and `tools/arena_stack.py`). References (PR #708):
  - PumpSwap: hunt8 `sc.py`, not in the repo, at host path `/data/mal/hunt-1008/push-1016/hunt8/second-book/h/sc.py` on mal-research-0, sha256 `161ee53576e6feb399f08ae11a599333e82f09dee5169a27a8f3bd0ab3b86b9b`;
  - bonding curve: `tools/latency_curve.py`, sha256 `129a8dddb8567bfa4cb3f35120b5da702439ff0df930a1bfb9d542ecd6a983db` at c3dc98f.
  - Parity set: `tools/fixtures/arena_paper/parity_cases.jsonl` sha256 `8b4d309b833da29200c7ebece3d4e0d4cc352f47def94560223b6c86026a4f58` (420 cases from exploration hour 2026-09-10T12).
  - Expected values:
    - legacy: `parity_expected.jsonl` sha256 `a114480424e2f512f0b5ef3ba3e466d072adc86aeedfd51ab7ef2566f4ee3c51`, md5 of the float lines `8427f106a0078f5c002345957d6a717d`;
    - binding: `parity_expected_binding.jsonl` sha256 `2d5936d71097f9de5eb7c9eea9e0b549676f1783a6a7730b804eb35d6ba98e2a`, md5 `af48053427bd2c49089f99a237a1e781`.
  - `PARITY.json` sha256 `753092b6d0eefad141a318ba8a36a9f1769f62699be961ef27f8c062aa253f98`.
  - Result: 2,520 legs per mode, 0 mismatches.

- **Source exploration-pool hours (quant-proof #719, comment 6246254252).** Every source file is pinned as path@sha256 in `ARTIFACTS/lab/arena-records-2026-10-18-parity-sources.txt`. That is 78 lines: the trades, creates and migrations parquet of each hour.
  - The engine set comes from `/data/mal/audit-1008/tape` hour `2026-09-10T12`, in ledger row "Fast `[2026-09-09T12, 2026-09-15T12)`". Its owner is the exploration pool: a spent confirmation block, prior owner EXP-011, moved 2026-10-06.
  - The H5 set comes from the same tape, hours `2026-09-20T00` through `2026-09-21T00`, in ledger row "Fast pre-cut" `2026-09-19T01 → 2026-09-22T00`. Its owner is the exploration pool.
- **Disclosure: both pinned parity sets are state-level.** They are files of extracted pool states (reserves and spot, entry and exit states), not rows in the arena tape's format, so they depart from DEC-031 §1.4's wording.
  - Engine: extraction from feed rows is tested separately by the `on_row` replay in `tools/arena_parity.py`, which feeds one global slot-ordered stream through the full engine. It showed 0 state mismatches against an independent l3-simlib extraction on all 300 PumpSwap cases.
  - H5: the case states come from `tools/h5_shadow.py`'s own replay outcome strips over the raw tape, the same decision path the book runs. That extraction is not separately parity-checked against an independent reader. The md5 proof covers its trigger decisions (pv 72/72), not the strip states.

- **Condition before the H5 books' first gate read (quant-proof, comment 6246275907).** Run a parity check of the H5 entry and exit state selection on the pinned H5 hours against an independent extraction (for example the l3-simlib reader used in #708). Use timing and states only, no outcomes, and record 0 mismatches. Any fix to `tools/arena_h5_score.py` is a change to a pinned file: disclose it in the read, with quant-proof OK. The clock may start before this.

## 3. Feed delay d (arena engine books only; the H5 books measure delay per trigger on the chain clock)
- **Input:**
  - the tape from MiScusi job #1147: mal-fast-0, `$HOME/data/arena/tape-delaymeas-20261018T0402`, 75 min of public RPC, 2 endpoints × 2 sockets;
  - tape files `tape-2026-10-18T04.jsonl.zst@a113012bae98abeec427d9bd776f932d1007c5cd85a31a14b200fd5ab2065d1f` and `tape-2026-10-18T05.jsonl@7bed794f79e40e1b28eb9136ffb63c4f3fc4c3f4ec1eee81eb99d5694dcc8d2e`.
- **Measurement:** job #1155, `tools/arena_feed_delay.py` at `7a319e3`. Output on mal-fast-0 `$HOME/data/arena/feed-delay-20261018.json`, sha256 `14c29643e9206e18d4c8910d3a61d6a1c4e0e4eef3248ed6d3eed0732e4d3a45` (38,312 bytes). Timing fields only.
- **Result:**
  - 20,767 distinct slots, 416 sampled (stride 50), 0 missing;
  - median delay 1.0445 s, p90 1.4815 s;
  - 0.21683 s per slot.
- **Deciding d = 7 slots,** that is ceil(p90 / seconds per slot) = ceil(1.4815 / 0.21683) = ceil(6.83).
  - The p90 rule is the manager's ruling, implemented in PR #716 and quant-proof OK.
  - The measurement tool version at `7a319e3` printed the median-based d = 5. The p90 figure and seconds-per-slot in the same JSON give 7. The arithmetic is shown here; the tool on main now prints both.
  - block_time has 1 s resolution, so each delay reads up to 1 s high (conservative).
- **How d is set:** the arena engine's config sets d = 7 explicitly through `StackConfig(feed_delay_slots=7)` (required, no default). Nothing loads the JSON's own `feed_delay_slots` field, which holds the median-based 5.
- **File hash, quoted from the log of MiScusi job #1156** (mal-fast-0): `14c29643e9206e18d4c8910d3a61d6a1c4e0e4eef3248ed6d3eed0732e4d3a45  /home/claude/data/arena/feed-delay-20261018.json`.
- **Re-measurement:** each read re-measures the delay on its own window and says so if it exceeds 7.
- **Gap census of the same tape** (job #1152): 105 PumpSwap gap rows, all `socket_reconnect` with `data_loss` false. There were 0 data-loss gaps in 75 min.

## 4. Deployment conditions (quant-proof, PR #716 comment 6245701738; manager decisions)
- **Frozen checkout.** Each runner runs from its own frozen checkout: a MiScusi job tree at the frozen sha, never `~/MAL`. Output is outside the checkout.
- **Haiku v4.** All 13 arms run in one process at one frozen sha. Prompts, cards and reviewer instructions load from inside that checkout.
- **H5 books.** `h5v1-boostfloor` and `h5v2-boostclock`:
  - run on mal-fast-0 under `$HOME/data/arena` (a different filesystem from walk 2, DEC-031 §3.8);
  - read their own public-RPC feed (`wss://api.mainnet-beta.solana.com`, `wss://public.rpc.solanavibestation.com`; 3 sockets), with the tip union off;
  - deciding cell 1.9 s at 0.30 SOL; the pressure intercept is refit at read;
  - counted window: pools with s0 at or after the clock and before 2026-11-06T00Z, the end-seal the code enforces;
  - any A3 HALT flag ends both counted windows.
- **Disk floor.** On research-0 the arena ledger refuses writes below 25% free (DEC-031 §3.8). The host is at about 19.9% free, so no research-0 book can start until more space is freed.

## 5. bots.json lines (H5 books; registered 2026-10-18T05:54:06Z, job #1157; copied here within 24 h, DEC-031 §1.2)
- **Registry:** mal-fast-0 `$HOME/data/arena/bots.json`, sha256 `d1bd6ad32842706e57d847862b239e34ad3a7fd45d5a23e6bd53b805843c22e8`. Notebook entry n_e-80oeFe6Ma0zw.
- **Shared by both books:**
  - frozen_sha `1f7a89cdf02ff46c3e33b9dcc88531c75d2b6cde`;
  - clock_start_ms `1792303200000` (2026-10-18T06:00:00Z); registered_at_ms `1792302846538`;
  - deciding_stake_sol 0.30, deciding_cell 1p9, stack DEC-031-rev2;
  - rule H5-BOOSTFLOOR v1;
  - sockets 3, endpoints `wss://api.mainnet-beta.solana.com` and `wss://public.rpc.solanavibestation.com`, host mal-fast-0;
  - scorer `tools/arena_h5_score.py`, cost module `tools/arena_h5_cost.py`;
  - window_end_ms null and stopped_ms null at registration.
- **`h5v1-boostfloor-a1`:** exit_rule v1 (330 s).
- **`h5v2-boostclock-a1`:** exit_rule v2 (bx10), freeze_sha256 `5368cc7b757a2f7a6cb064c71193b4e0cd740583f7348a60cb9a864401c190e0`.
- **Shadow:** MiScusi job #1158 at git_ref 1f7a89c. Start record start_ms `1792302863694` (05:54:23Z), with fresh_forward ARENA, dirty false, bx10 true and tip_union off (checked by job #1160). Output goes to `$HOME/data/arena/h5-2026-10-18` on mal-fast-0.
