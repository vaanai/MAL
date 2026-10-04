# Manager handoff 2026-10-04 (Manager 2-3 on mal-research-0 → next manager)

Replace this page at the next handoff; don't append to it. Read it first, then [LAB_STATE.md](../LAB_STATE.md) (last refreshed at #260; see the "Since LAB_STATE" section below), [CONSTITUTION.md](../CONSTITUTION.md), DEC-015, DEC-016 (Amendments 1–3), DEC-018, DEC-019, and the memory notes `profit-focus`, `pumpswap-virtual-reserve`, `execution-probe`, `exp012-forward-read`.

Paper only. No key exists yet.

## Owner direction (2026-10-04)

> "Get to profit in about a month. Be deliberate. Work from previous mistakes and take steps forward, not sideways. Build the edge, build a dataset around the bot, make good decisions, make them quick."

The critical path is **EXP-012 → forward read (~10-16) → DEC-016 Amendment 3 checks → owner yes → small live trial (DEC-018)**. In parallel, a **live execution probe** (DEC-019) measures real fills early.

## THE big finding of this session: PumpSwap virtual quote reserve

- **What it is.** Every PumpSwap pool has a per-pool virtual quote reserve V, constant per pool. In about 69% of migrated pools V is about 17.584505 SOL; the rest have V ≈ 0. The swap math prices on `vault + V`.
- **The gap.** Our tape's `quote_reserve`/`price_sol` and `tools/paper_curve_math.py` ignore V. V was present across all our data (2026-08-26..09-21). Proof: #280 `tools/pumpswap_virtual_history.py`, and a reconciliation over 54.9M prints.
- **Size of the error.** At EXP-012's entry (mig slot+1, 0.5 SOL), paper credits about 19–20% too many tokens; real buys there come in at −1,943 bps. Exits are mispriced the other way.
- **V-corrected re-score of EXP-012 (#281, r1)**, a correction analysis, not a new read:

| Book | Metric | Frozen | Corrected |
| --- | --- | --- | --- |
| Spent one-shot block (n=451) | flat mean SOL/trade | 0.03487 | **0.02527** |
| | flat CI lower bound | 3.62% | **1.79%** |
| | pressure mean | 4.108% | **3.068%** |
| | flat ex-top-3 SOL | 14.04 | **9.86** |
| | gate | PASS | **still PASS** |
| Exploration OOF (n=891) | flat mean | 7.432% | 6.040% |
| | days positive | 9/9 | 9/9 |
| Exploration, **unfiltered** baseline | flat | +0.133% | **−0.144%** |

  The unfiltered baseline flips negative, so the selection carries the edge.
- **Still open:**
  - **`quant-proof` review of #281.** It was running at handoff; check its result. If it is OK, merge #281.
  - **r2.** A rerun with the exit mix and the full V map for (a), running **outside MiScusi** as PID 3506243 (`python -m tools.exp012_virtual_rescore rescore --run-id r2`), writing to `/data/mal/exp012-virtual-rescore/r2/`. When it finishes, update the #281 body: exit mix, CI upper bounds, (a) numbers.
  - **DEC-016 Amendment 4**, still to write before the read. Live support also requires the **V-corrected** forward book (same entries, re-priced with `tools/pumpswap_virtual_adapter.py`) to clear the full gate under both fail models. The frozen forward read itself stays as pre-registered. quant-proof was asked to draft the text; use it.
- **Every older PumpSwap P&L number** in this lab, including migrate-direct, EXP-013/014 screens as built and DEC-017, is unverified until re-priced.

## State at 2026-10-04T05:45Z

### Running (MiScusi, session MALsession1, mal-research-0 unless noted)

| Job | What | Notes |
| --- | --- | --- |
| #71 `j_XRdI7CrrMEa_JQ` | DEC-016 forward walk → `/data/mal/blocks/forward-1002` | Through 10-04T02, 0 issues, 486k credits. **Resubmit by ~10-09T15Z** (same command, params `{"start":"2026-10-02T15"}`, resumable, 10080 min). |
| #94/#95/#96 | fresh-0808 walkers w1/w2/w3 (`[08-08T12, 08-14T12)`, reserved block, #261) | At 38/32/36 of 48 sealed. |
| #97/#98/#99 | Verify jobs for fresh-0808 | Each runs `after` its walker. Check 0 flagged / 0 duplicates. |
| PID 3506243 (not MiScusi) | #281 r2 re-score | See above. |

### EXP-013 (graduation classifier)

- Tooling is merged, with pre-run fixes #267 (sweep watermark) and #271 (screen.json before result.v1), recorded as Amendment 6 (#268, #272).
- **The single screen job #102 was CANCELLED before it started** (10-04T03:17Z), because of the V finding. No try was spent: no tries line, `/data/mal/exp013-grad/` is empty.
- **Before re-queueing:**
  1. Write an EXP-013 Amendment 7 that the PumpSwap sell prices use V, via `tools/pumpswap_virtual_adapter.py` inside `exp013_grad_trigger`'s worker (new code, plan amendment, before any run).
  2. Add tests.
  3. Then queue the run. The view cutoff stays 10-04T12Z; all w1–w7 are verified before it.
- The run script is `scripts/research/exp013-screen-run.sh <RUN_ID>`, one MiScusi job, 24 GB, 8 CPU.
- **Prior: about 20% or less.** It is a backup, not the main effort.

### EXP-014 (mig+15 PumpSwap selector)

- Plan and Amendments 1–3 are merged (#263, #265, #266, #270). The table builder is merged (#264, #273); its late-row guard reads 0 on real data (debug #101).
- **#269 (model and screen) is reviewed but NOT merged.** It is marked do-not-merge until the EXP-013 screen runs.
- EXP-014 is entirely PumpSwap, so it **must get V pricing (plan amendment plus table-builder change) before its screen.** Its cutoff is 10-05T12Z for views; all w1–w7 are verified.
- **Prior: about 10%.** It is cheap, but do not spend much more builder time on it.

### EXP-015 (rolling retrain): parked

#278 is a draft. quant-proof found the "model aging" motive unsupported by EXP-012's own data, and this is the second try of a family that failed once (DEC-017 (a)). Do not revive it unless something new supports it.

### Feed and runner (DEC-015)

- **The free two-socket public tape FAILED coverage** over 10-03T20–22: 93.869% of chain trades (#274). The cause is HTTP 413 rejections and 1006 closes on the public RPC.
- **The owner chose option A, a getBlock tip follower** (#275). It is merged as `tools/fast_tip_follower.py` + `mal-fast-tip-follower.service` (#276), and is not installed. It also writes observe-format creates, so the runner switches with **config only**.
- **Nothing is installed on fast-0 yet.** The installer refuses before 2026-10-05T05:00Z.
- **Heartbeat sampler and downtime tool** are merged (#259). After install, start the heartbeat service once by hand and check that `pid` is non-null **before** enabling its timer.
- Daily coverage checks run `tools.tape_coverage` against forward-1002, as jobs on fast-0 (≤1.9 GB). Copy job #103.

### Live track (DEC-018 proposed, DEC-019 approved in principle)

- **Trial terms** (owner, #262): 0.5 SOL, 3 concurrent, 500k lamports priority per side, no tip.
- **DEC-016 Amendment 3 tooling is merged:**
  - #257 latency export and runner compare rows 0–2;
  - #258 forward sensitivity re-score;
  - #259 heartbeat.
- **Keyless PumpSwap builder and simulator** are merged (#280): `tools/pumpswap_tx.py`, `tools/pumpswap_simulate.py`. They reproduce real buys and sells byte for byte and add `virtual_quote_reserves` parsing.
- **Execution probe (DEC-019, #279):**
  - 30 × 0.05 SOL on EXP-012 runner signals, PumpSwap only, at most 3 concurrent, 0.25 SOL loss cap, 4 days, 0.5 SOL deposit.
  - **Helm or the owner** generates the key as user `mal-live` (script still to write: `scripts/mal-fast/make-probe-wallet.sh`). The manager never sees the key.
  - Preconditions, in DEC-019 §6:
    1. the executor (signer/sender, every limit coded) is built and passes reviewer plus security review;
    2. a 6 h keyless dry run on live signals with 0 errors;
    3. the runner is up;
    4. the wallet is funded;
    5. a withdraw address is named.
  - The executor must price with V.

### Owner questions open in MiScusi (no close tool on the manager side; ask the owner to dismiss answered ones)

| Question | Status |
| --- | --- |
| `q_YBNB8Qi1lR_WjQ` DEC-018 five live decisions | Due ~10-14, open. |
| `q_8eiu9qbtVa7TcA` execution probe | Answered "approve" in chat (DEC-019); the owner should dismiss it. |
| `q_Wy3S6eK9bN74Ng` trial terms | Answered "defaults" (#262); dismiss. |
| `q_lVujDVpXz-K3cg` debug-dir deletes | Done by the owner; dismiss. |
| `q_Oj6H5DPXKSswZg` feed | Answered "A". |

## Next steps, in order

1. **#281.** Read quant-proof's verdict, fix anything it requires, and merge. Write **DEC-016 Amendment 4** (V-corrected forward book must also pass for live support) before 10-06T00Z, since forward P&L is sealed until then.
2. **2026-10-05T05:00Z kill review.**
   - Follow `docs/runbooks/kill-review-2026-10-05.md`: snapshot job on fast-0, copy `MANIFEST_SHA256` into the score job on research-0, then quant-proof, then LAB_STATE, then `review_windows: []`.
   - The 9 Oracle books are priced with the V-less model. Report V-blindness as a caveat on any book that trades PumpSwap, and do not promote on V-less numbers.
3. **Right after the kill review**, install on fast-0 with `--commit <main sha>`:
   1. observe + runner + heartbeat + tip follower;
   2. build the venv (pins verified for cp312 x86_64);
   3. start the tip follower;
   4. check its coverage against forward-1002 over 2 h;
   5. point the runner `tape_dir`/`creates_dir` at the tip dirs;
   6. start the runner;
   7. heartbeat check, then enable the timer;
   8. 2-day lag probation;
   9. record it all in LAB_STATE.
4. **Executor PR for the probe:** signing, sending and limits, priced with V. Reviewer plus security review, then the 6 h keyless dry run, then ask Helm to make the key and the owner to fund it.
5. **EXP-013 and EXP-014.** Add V-pricing amendments and code, then their single screens. Both are low priority.
6. **About 10-09T15Z:** resubmit forward walk #71.
7. **About 10-16T02Z:** EXP-012 FINAL read, then quant-proof, then the V-corrected book (Amendment 4), then the Amendment 3 (a) sensitivity at measured latency and the (b) runner comparison, then the owner.

## Gotchas found this session

- **No `rm -rf` under `/data/mal` or in the scratchpad from this session** (permission denied). Ask the owner, which can be done through Helm. A 22 GB analysis cache sits in the scratchpad `recon/cache/` and can be deleted.
- **Hour files are not time-ordered.** PumpSwap rows lag up to about 1,600 s within a file. Never resolve on a running max mid-file. EXP-012's frozen scorer is safe (final flush only).
- **Pool B (Oracle live)** has no `block_time` on trades (use `event_ts`). Its creates carry only receive time `t_ws`.
- **Tape buy `sol_lamports` is inconsistent:** about 50% of PumpSwap buys exclude the fee.
- **`book_stats` "promote"** includes n ≥ 100 and ≥ 5 days. Screens must not read it; they compute bars themselves.
- **MiScusi jobs run on the pushed commit at submit time.** A job queued early is pinned to that code.
- **Builders often stop at 40 turns.** Resume them with SendMessage and a tight scope.
