# r4-b-T pricer, read wrapper, A3 entry and decode check (design note)

Code and records for the arena book `r4bt-a1` under the r4-b-T DEC-031 amendment (PR #1187; the amendment number is filled at merge) and QP-RULING-1102-r4bt (`/data/mal/hunt-1008/ops/manager18/QP-RULING-1102-r4bt.md`, sha256 `2d329e4bb2484cca59bfb02cbd232d1cd3f6347fbc55b53563eca7372750c0d4`). Paper only. Synthetic tests only: no r4-b decision log, declog, host `out/`, tip file, derived-copy tape, arena tape or real ledger was opened to write this. No book has cleared the promotion gate; nothing here says r4-b or r4-b-T makes money.

## W: a branch from bc65c088 that adds files only (Q8)

`claude/r4bt-w` is created at `bc65c088793753954c86b82d0e5eff74aa038e0b` and adds only new files, so every file the r4-b host imports stays byte-identical and dry-long-1101a's smoke, P0 and P-C2 carry over:

| File | Role |
| --- | --- |
| `tools/r4bt_attempts.py`, `tools/test_r4bt_attempts.py` | the outcome-blind counter, byte copy of main's #1195 versions (blobs fd7fccca08377f8e53e7050baf5cf690fba620c2 and 4796a1453ff970f5f7463708e27c4b1ae6816644) |
| `tools/r4bt_gate.py` | "EXP-027 read written" record (route a or b; route b also names the A2 C4 and C5 commits, both checked as ancestors of origin/main after a quiet fetch), closed roots, own-registry check, engine and scorer pins, closure walk and blob comparison |
| `tools/r4bt_price.py` | the pricer |
| `tools/r4bt_read.py` | the read wrapper |
| `tools/r4bt_a3.py` | the A3 entry (Q2) |
| `tools/r4bt_decode_check.py` | the r4-b-T decode check (Q2 item 4), a profile of `tools/a3_redeploy_check.py` |
| `tools/test_r4bt_*.py`, this note | tests (95 pass at the branch head) |

`git diff --name-status bc65c088 W` shows only `A` lines. The branch reaches main through a PR merged with **"Create a merge commit"** (`gh pr merge --merge`, never squash), so that W (the branch head) becomes an ancestor of origin/main: `register_bot` resolves `frozen_sha` with `merge-base --is-ancestor <W> origin/main` and the scorer refuses a registry line without `verified_sha`. Main's later changes (for example #1182's `arena_paper.py`) never enter W.

The registration record states: the `--name-status` list; QP's 88-file closure compared by `git rev-parse` at both shas; `models/r4b/` (7 files) and the wrappers `scripts/research/r4b-dry-long.sh` (8a0c7628), `r4b-dry-sidecar.sh` (3659842e), `r4b-dry-stop.sh` (1cfe12c4); and `r4bt_gate.static_closure` as a subset check (75 `tools/*.py` files at bc65c088; it sees no non-Python file, QP's list is the authority). The comparison is a registration-time record, not a test, because main may move closure files later. Also owed there: the test files green at a clean checkout of W; the decision host from a fresh clean install tree at W; the sha256 of the `$HOME/mal-venvs/r4b` `pip freeze` at the registration and at the host start; the configuration equal to dry-long-1101a's.

## Stack and registry

- `StackConfig(rent=0, e1_floor_per_005=384022, strand_rule="both", gap_slots=150, gap_silence_ms=60000, k_rounding="ceil", feed_delay_slots=d)`. `ceil` is the arena default (Q5); d is `ceil(p90 block_lag_ms_p90 / (slot_s x 1000))` from at least 24 h of the sampler, pinned once in the registration record. Neither has a default in the pricer or the read. No writer or transport lag is added: the pricer feeds every tape row received at or before the decision row's `ms` before applying it (a test pins the order).
- r4-b-T has its OWN registry file, outside `/data/mal/arena` (the pricer, the read and the classifier refuse that root). The `r4bt-a1` line also goes into `/data/mal/arena/bots.json` through `register_bot`; N for the multiplicity block is counted over the arena registry by the manager (`--n-books`). The A3 stop line is written to both (`r4bt_a3 apply --registry OWN --arena-registry /data/mal/arena/bots.json`; the shared file is accepted only there, only that exact path).

## What changed from the first version (QP V1 to V10, reviewer items)

V1 declog gap rows are counted by `gap_kind` and never fed to the engine; the read prints them beside the derived copy's data_loss rows. V2 every closed declog day file is checked against a pinned day-sha list before pricing. V3 an entry without a string `pool` is untrusted (exit 3). V4 a confirmation boundary file with `from_ms` selects [from_ms, boundary) into a fresh root. V5 the read runs the boundary and attempt-count refusals before any scoring call and records a config-pin NOT_DECIDABLE. V6 route b checks the A2 C4/C5 commits. V7 the A3 regime (halt instants, 24 h rule at the write, dead-man, the r4-b-T decode-check profile only) and the `a3_halt` span exclusion. V8 the V audit (counts, ids in a sidecar, rent-charged leg decides). V9 disclosures per the ruling. The pricer also refuses while any counted entry has no exit, and requires every declog `start` row to carry the registered frozen_sha.

## Left (not in this branch yet)

- `tools/r4bt_attempts.py count --from-ms` (the confirmation mode, QP's #1188 follow-up, "also in W") and the read's confirmation mode (p <= 0.05/c, 10,000 draws, seed 1, the larger p of the two fail models); both must merge with QP OK before the screen read runs.
- The decode check's second decode path (the `tools/r4b_feed_adapter.py` mapping parity, the `adapter_check` argument) and the host-side CLI that builds the RPC client. Until the adapter path exists the check cannot PASS, so every program-only redeploy is a hard halt at its write (the fail-closed fallback of Q2 item 4).
- The Am.23 item 5 hour-health tool, d, the envelope run, the sampler chain, and a real-layout count-only pass of the pricer on the derived copy (its memory and runtime on 8 to 12 days of tape are not measured).
