# DRAIN-VETO step 1: DECLARE (DRAFT, not filed)

Status: draft. Nothing here is measured. Step 0 (count-only, `tools/drainveto_reader.py`) has not run. The final text, with its sha256 in a MiScusi notebook entry, is filed before any price is read (hunt23 RULES). Basis: QP-RULING-1120b Q1; idea n1120-floor-drain-veto in `new-strategy-ideas.md`.

## Label

IN-SAMPLE EXPLORATION on the four OUTCOMES blocks; not a gate read. No book has cleared the promotion gate.

## What step 0 hands over

Per trigger (ordinal only, no pool, mint or trader id): measured or censored with a reason, top-1 and top-3 token share of the positive same-block net balances, and the flags top1 >= Dr and top3 >= Dr, where Dr = k/V - B = Q*B/V (token_raw) is the drain quantity from the trigger print's own post-trade state (Q the rule's qpost, real quote in lamports, V excluded; B the base after the sell; V the VMap value; not the kit's entry state, no latency index) (named Dr so that it is not confused with the split D). The balance cut is (slot, tx_index, event_index) up to and including the trigger print (per block the report gives trigger_print_seen, the triggers whose sell print was read). Counts per block and per split (D, C1, C2). Censoring, each counted per block: no create row in the block (merges "created before the block start" with "no create row found"), oracle-live proxy, block not measurable (under 99% of pumpfun or of pumpswap rows carry all of mint, trader, token_raw, slot, tx_index, event_index), create after decision, no usable rows before the cut, locator incomplete, dstate invalid (V outside [17.5e9, 17.7e9], Q outside [0, 0.5e9], B <= 0; never clipped), bad trigger row. Flagged share is computed on the measured (uncensored) triggers only.

Disclosed limits (every report): keep or decode gaps between create and decision are not detected, so balances there are incomplete (bias of unknown direction); transfers in are missed (understates concentration), transfers out leave tokens on the sender (overstates it), direction unknown; crc32 ids can collide (biases toward flagging); pool, burn and known-program holders cannot be recognised from a crc32 id and are not excluded; the live getTokenLargestAccounts read counts token accounts, not wallets, so it is a different measurement.

## Step 0 gate to step 1

The flagged share on the uncensored explore-0814 set must lie in 5-40% at both top-1 and top-3. Outside it, the veto either hits almost nothing or almost everything, and step 1 does not run. The September blocks are reported and do not decide (about 23 attempts in total).

## How the 332 triggers are selected

The 332 are the FLOOR triggers of the h24_tp1.5 cell of the exploration grid. That cell was itself chosen from a 12-cell grid, so the base carries selection. A veto that only removes trades raises the mean by chance about half the time per variant. Step 1 therefore also reports a matched random-veto placebo beside every difference: the same flagged count, 1,000 draws, seed 1, on the same uncensored subset. The locator (mint, slot, tx_index, event_index) of each of the 332 comes from the pinned builder `tools/drainveto_triggers.py` (QP-RULING-1120c): a count-only re-run of the exit study's L1 trigger pass joined to grid.pkl h24_tp1.5 keys. Every report quoting step 0 or step 1 carries disclosures D1-D6 of that ruling and D7 (Dr uses the trigger print's post-trade state, the rule's own q_post approximation with fee 0.0125, not the kit's entry state at sd + D + k; the entry can land on a different state), all printed by the builder, including the `locator_incomplete` count per block and the count of triggers with more than one qualifying print in the slot (the first in kit order is the locator). The 332 are the study's priced set: its membership depends on exits and on the 12-cell selection, and that selection is carried, not removed.

## What step 1 computes (only after the gate above, new QP OK, reader blob unchanged or the change named)

On the SAME uncensored subset (not the 332), through the kit pricer, for each variant (top-1, top-3): flagged vs unflagged mean SOL per trade, TP rate, strand count, and total with the top 3 trades removed. Both fail models (flat 15%, pressure-fail at slope scale 1) and both strand rules (total, sc) (RULES Am.1 item 1). Deciding legs: the kit send fee and the live send fee (QP-RULING-1120 Q2 D). Decision statistic: the worse of the two variants.

## What kills it

- Unflagged minus base mean SOL per trade <= 0 in split D or C1, in either variant, under any deciding sub-cell (flat and pressure-1 fail models x total and sc strand rules) or either send fee: dead.
- Unflagged minus base <= 0 after removing the best UTC day, in split D or C1: dead.
- Flagged share outside 5-40% at step 0: dead before pricing.
- Unflagged total not positive after removing its top 3 trades: dead. ("One UTC day" means the single UTC date with the largest contribution to the difference; see the line above.)
- The difference is not above the 95th percentile of the matched random-veto placebo: dead.
- The three c8 strands (0.91%) shown to be a kit tolerance artefact so that no strand is avoided: dead (the veto has nothing to remove).
- Uncensored explore-0814 subset too small to separate unflagged from base (fewer than 100 unflagged trades is reported "not decidable", not positive).

## What a pass would and would not mean

A pass is an in-sample exploration result on the uncensored subset of the 332 FLOOR triggers; it is not out-of-sample and not a gate read. It licenses only the forward check: the RPC logger (getTokenLargestAccounts then getMultipleAccounts, pool vault, curve account, burn and known programs dropped) on open-sim triggers, and a 5-day declared read. The tape rebuild and the live read are different measurements; only the forward check links them.

## Multiplicity

Two variants, two splits read, counted in m (throughput ruling item 6(g)).
