# Pre-registration: HV6 v6.1 blind entry-rate rehearsal (one run, report-only)

Written 2026-10-26T23:23Z (date -u). It is committed BEFORE the prompts are built and before any model call. PAPER. No API key is used: the manager runs the calls with a Claude-plan Haiku subagent, the same way as the 192-card set. Nothing here says HV6 makes money. No book has cleared the promotion gate.

Rationale for the text: `docs/research/hv6/V61-PROMPT.md`. It was written before this file, and the text was fixed before any v6.1 reply existed.

## What is run

- **Prompt.** The v6.1 seed `tools/hv6_playbooks_v61/GEN_v0.txt` (sha256 `598f7aaa22339f8e0e7a5e6d6b83eba1efe9be0c81f9a6721f3be33de380b738`) is the only system block. The entry user template is unchanged (sha256 `aa9224d3...`).
- **Arm.** F, seq 0, four cards per call.
- **Request parameters.** As the 192-card set: `claude-haiku-5-5`, thinking disabled, max_tokens 400, no temperature.
- **Cards.**
  - n = 192, FRESH.
  - Disjoint from `/data2/mal/hv6-rehearsal/cards-sample.jsonl` (48) and `/data2/mal/hv6-rate-rehearsal/cards-sample.jsonl` (192). The builder asserts 0 overlap.
  - Source: the same 1,560-card file the earlier builders used, `/data/mal/arena/haiku-v6/drill/real-5071e29a-r16g-20261026/real-a/v6/cards/2026-10-2{1,2,3}.jsonl`, with the drill-rule md5 `120dbe2aba1c4e66e44f5825b2df589a`.
  - Pre-clock days 2026-10-21..23. These are walk-2-hour information (DEC-030 section 2 R1). They are disclosed as such.
  - Sampling rule:
    - 16 cards per (day, setup) cell, as P58;
    - 16 equal-count time bins over the cards that are in neither prior sample;
    - 1 card per bin, the lowest `sha256("hv6-v61-rehearsal-seed1|" + card_sha)`.
  - Packing as P58: 48 four-card calls, no two cards of one pool in a call, 8 files `batch-A..H.jsonl` of 6 calls each.
- **Builder.** `tools/hv6_v61_rehearsal_build.py`, run once at a clean detached checkout `/data2/mal/wt/hv6-v61-build` of this branch's head. It is write-once: it refuses if prompts or a manifest already exist. Output goes to `/data2/mal/hv6-v61-rehearsal/`: `prompts/batch-A..H.jsonl`, `cards-sample.jsonl` and `manifest.json` (file sha256s, code sha, arm-equality check).
- **No outcome.** No price, exit, P&L, ledger or later card of any rehearsal pool is joined or opened.

## How it is scored (fixed now)

- Replies go to `/data2/mal/hv6-v61-rehearsal/replies/all.jsonl`, one line per call: `{chunk_id, card_ids, text, stop_reason, error}`. This is the 192-card format.
- **Validator.** `/data2/mal/hv6-rate-rehearsal/validate.py` (sha256 `a54701b5492606c4a3e1ee5e131b0b08d884cc78511091bd6095193ea67bf6f2`, pinned code 8cd8fe54) with `--cards /data2/mal/hv6-v61-rehearsal/cards-sample.jsonl --json`. `hv6_agents.validate_entry` does not read the playbook, and its code is the same at 8cd8fe54 and here. So v6.1 replies are scored by v6.0's registered validator. GEN v0 knobs (min_conviction 3) apply.
- **Reported, all unrounded:**
  - counts and shares of BUY, WATCH, SKIP, BELOW_MIN, PARSE_ERROR (with reasons), STALE and NO_OUTPUT over 192;
  - the same counts by setup;
  - the BUY count at conviction >= 3 (the `is_entry` count);
  - the Wilson 95% interval on the BUY share.
- **Reference figures.** These are quoted from the QP ruling, not re-run: v6.0 on the 192-card set gave BUY 0/192, WATCH 24/192 (12.5%), SKIP 166/192 (86.5%), PARSE_ERROR 2/192 (1.0%). The needed first-pass rate is 1.603% of about 520 cards/day (100 attempts in H = 12 days).

## Rules (no iteration)

1. ONE run. The rate is accepted whatever it is: 0, 1.6% or 50%.
2. The v6.1 text is not edited in response to this run. No second variant is written, rehearsed or compared. If v6.1 is later judged unfit, the remedy is a new bot_id with a new pre-registration and new fresh cards, never a re-run of these 192.
3. The run never decides v6.0. v6.0 keeps its sha `8cd8fe54` and its 10-30 clock.
4. The run does not on its own decide whether v6.1 registers. That decision rests on the registration needs in `docs/research/hv6/V61-PROMPT.md` section 6. A low rate is reported with the D-ER-style disclosure. A high rate is reported with its caveats:
   - stratified sample (BOARD underweighted);
   - clustered by pool and day;
   - Claude-plan subagent, not the bare API call;
   - WATCH re-ask rate b still unmeasured;
   - a BUY is not an edge.
5. A failed or partial call (error, refusal, truncation) is counted as it is (NO_OUTPUT / PARSE_ERROR). It is NOT re-sent, unless a call returned no reply at all because of a transport error. Any re-send is listed with its chunk_id.
6. Reading the `why` texts is allowed only after the counts are written and notebooked. It is for reporting compliance with "say it is a hypothesis", never for editing.

## Build record (appended after the build, 2026-10-26T23:24Z; no model call made)

- Built once at `4eba9ddb2648b498b8737b32dfad0adcf7cdcc78`, which is the commit that added this file and the builder. Checkout `/data2/mal/wt/hv6-v61-build`, detached and clean after the build.
- Sample: n 192. By day: 64 / 64 / 64. By setup: 48 each. Distinct pools 132. Overlap with the 240 prior cards: 0. Arm equality 192/192 for L, L-nr, P and F (system equal, user equal except the nonce line).
- `manifest.json` sha256 `e28bed9af9414944f29a83fc931bba130637e899811469f2b42b596486a5d5b1`.
- `batch-A..H.jsonl` sha256:
  - A `23db310e7d46d6ba54304acda60e8d70f1f20903d3589559b48ac22836514038`
  - B `eda81365a2bf50c6e0f59c50226ede03fb25ab7b95bc169001a22beafc1ef62f`
  - C `091965fca6d2fe10b388bf08f7852e99d1aaec9515eb831754974c6467d144c2`
  - D `9de8b7ed23fc684bc31cde98f9b075b847ab10f32ec4936e7bb15fa4a9f7e027`
  - E `72116582953df657e048be2e72f0bd3947b4c7599581af4d3cd43a263ad5c63d`
  - F `aaddd0374bb8b47fe9a8f486888c7e967a26340ab394dc9d10dc89dfa8deb964`
  - G `dd8594db3a5861c32c5fae335a5a3ea47352139dce70fc475544ae9c33b5990d`
  - H `3a6644a31685305d3c96a151861f9c08272b027e50090fd9258fc8f81e2fcfe7`
- `cards-sample.jsonl` sha256 `a6891f98590316ebe8e5f836f3e749ae484794f68bb7be4ec2191b0134e0b775`.
