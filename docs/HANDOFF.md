# Manager handoff 2026-10-04 late (manager4 on mal-research-0)

Replace this page at the next handoff; don't append to it. Read it first, then [LAB_STATE.md](../LAB_STATE.md) (last refreshed at #260; the kill review refreshes it next), [CONSTITUTION.md](../CONSTITUTION.md), DEC-016 (Amendments 1–4), DEC-018, DEC-019, and the memory notes `profit-focus`, `exp012-latency-binding`, `pumpswap-virtual-reserve`, `execution-probe`, `exp012-forward-read`.

Paper only. No key exists yet.

## Owner direction (2026-10-04)

> "Work like our lives depend on this working… test ideas we're genuinely curious about and think will work… learn from past mistakes."

- The owner approved the **0.5 SOL live execution probe** (DEC-019) to run alongside paper.
- The critical path is still EXP-012 → forward read (~10-16) → Am.3/Am.4 checks → owner → trial.
- The probe measures real execution first.

## Most important finding this session: entry latency under V

Lab note `ARTIFACTS/lab/exp012-latency-virtual-2026-10-04.md` (#292, quant-proof edited). This is exploration, not evidence.

EXP-012 selected book under V pricing, pressure mean / CI90 lower bound, SOL per 0.5 SOL entry:

| k | Pressure mean | CI90 lower bound |
| ---: | ---: | ---: |
| 1 | 0.01866 | 0.01114 |
| 4 | 0.01332 | 0.00653 |
| 6 | 0.01246 | 0.00541 |
| 8 | 0.00769 | 0.00117 |
| ≥ 12 | — | < 0 (every bound) |

- **What it means:** a fast-0 k(p50) of about 8 or more slots makes live support unlikely even if the FINAL passes. Am.3 also delays exits and applies the trial terms.
- **First thing after the runner is up:** measure k with the runner latency export and the probe's stage timestamps (`python -m tools.probe_executor --latency-report`).
- **If k(p50) > ~6:** latency work comes first.
  - The candidates are the tip follower's confirmed-getBlock lag and the runner's 300 ms holdback.
  - The executor's own tail interval was already fixed in #293 (50 ms signal loop).

## Merged this session

| PR | What |
| --- | --- |
| #284 | V map: every pool per mint (Am.4 §3). |
| #285 | r2 re-score lab note. Pressure-leg CI90 lower bound **0.00604** (spent block, V). Am.4 §1 adapter commit recorded. |
| #287, #292 | Latency × V wrapper and lab note. |
| #288 | **Runner + tip follower V pricing** (`pumpswap_virtual: require` in the fast config). |
| #286, #290, #293 | **Probe executor**: dry run + live (two switches), DEC-019 limits clamped, pre-sign whitelist, canonical pool, 50 ms signal loop, stage timestamps. |
| #289, #291 | **Wallet custody**. Pinned sha for Helm: `d5085b48aa6ffa2eaf781b5ddae6481a7eca2cb3`. |

**#288 details:**
- md5 equivalence with the flag off was EQUIVALENT (job #111: 95,948 decision rows and 18,030 position rows).
- EXP-012 features are unchanged; this is proved by test.

**#286, #290, #293 details:**
- Live requires both config `mode: live` and `--live`.
- The pre-sign whitelist means RPC data can't pick programs or destinations.
- **Bug found:** `pool_v2(mint)` is NOT the pool. `tx.canonical_pool(mint)` matched the tape for 95/100 sampled mints.

**#289, #291 custody (final, owner + Helm):**
- The key is at `/etc/mal-probe/probe-wallet.json`, root:root 0400.
- The executor gets it only via systemd `LoadCredential` in the live drop-in.
- Withdraw is root-only, from a pinned root install with hashed wheels.
- Repo deny rules cover `//etc/mal-probe/**` and `/run/credentials`.

## In flight

| Item | State |
| --- | --- |
| Job #108 (fast-0) | Kill-review SNAPSHOT. Sleeps until 05:00:30Z, then copies Oracle read-only. Then submit the score job on research-0 with `KR_EXPECT_MANIFEST_SHA256` (runbook `docs/runbooks/kill-review-2026-10-05.md`). The 9 Oracle books are V-less: report V-blindness as a caveat and never promote on V-less numbers. |
| Job #71 | Forward walk forward-1002. **Resubmit by ~10-09T15Z** (same command, params `{"start":"2026-10-02T15"}`, resumable, 10080 min). |
| Helm | Moving the Console off fast-0, fast-0 deny lines and auditd watch, then the wallet script at the pinned sha with the manifest. Sends **only the public key**. The owner relays. Also add `Read(//run/credentials/**)` and `Bash(*run/credentials*)` on fast-0. |
| Owner | Withdraw address (after Phantom setup). Funding 0.5 SOL only after a clean 6 h keyless dry run. DEC-018 five decisions q_YBNB8Qi1lR_WjQ due ~10-14. |

## Next steps, in order

1. **05:00Z kill review.** Snapshot #108, then the score job, then quant-proof, then LAB_STATE, notebook and console. Set `review_windows: []`.
2. **Right after it, install on fast-0** (paper only; the owner confirmed it is not blocked by Helm's lockdown). `scripts/mal-fast/install-fast-forward-paper.sh --commit <main sha>`:
   1. Start the tip follower.
   2. Check 2 h coverage against forward-1002.
   3. Point the runner `tape_dir`/`creates_dir` at the tip dirs. This is required: `pumpswap_virtual: require` skips `no_v` on old-tape rows.
   4. Start the runner.
   5. Check the heartbeat, then enable the timers.
   6. Record the restart in LAB_STATE.
3. **Install the probe executor in dry-run mode** (no key; the unit has no LoadCredential).
   - Before starting it, create `mal-live` and `/var/lib/mal-live` (0700), or ask Helm whether their wallet script run should do it first.
   - Verify `mal-live` can read `decisions.jsonl` and that the bind survives the runner's daily restart.
   - Run 6 h. Need 0 build errors and `live_validate_err` empty on real pools.
   - Then run `--latency-report`.
4. **Measure k(p50)/k(p90).** This decides the latency work and frames the owner conversation.
5. **When Helm sends the pubkey and the dry run is clean,** ask the owner to fund 0.5 SOL. Helm enables the live drop-in; post sha256 of `scripts/mal-fast/probe-executor-live.json` and `mal-probe-executor-live.conf` at the deployed commit for Helm to check.
6. **EXP-013 and EXP-014** still need V-pricing amendments before their single screens. Low priority (priors ≤ 20% and ≈ 10%).
7. **~10-16T02Z:** EXP-012 FINAL, then the V book (Am.4), then Am.3 at the measured k.

## Gotchas

- MiScusi job commands need `/data/mal/venv/bin/python` (`python` is not on PATH).
- There is no direct SSH from research-0 (`agent-ssh.sh` env unset). Use MiScusi jobs on fast-0.
- Quant-proof enforces "never round in the favorable direction". Build tables from raw JSON with floor rounding.
- Hour files are not time-ordered. PumpSwap rows lag up to ~1,600 s.
