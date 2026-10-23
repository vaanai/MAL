# QP ruling: DEC-019 Amendment 2 fee-arm probe, Test A / Test B (2026-10-23, quant-proof for manager14)

Data: /data/mal/hunt-1008/push-1016/hunt26/landing/feearm-landing-1022.csv, sha256 9a5f93fe436c1bb1fc30a25be4917c25d786d961c0ebc0022b5717cb6d30ab88 (re-hashed by QP, matches).
300 send rows, 2026-10-16T19:51:57Z .. 2026-10-19T21:57:58Z. Pre-registration: DEC-019 Am.2 lines 231-239 on origin/main.
QP reran the manager's script feearm_verdict.py (identical output) and ran an independent check (qp/qp_check.py in the manager's scratchpad).

## Verdict

PASS on both pre-registered tests. The floor move to 160,000 lamports is RECORDED ONLY after the two confirmations C1 and C2 below.
Neither confirmation can rescue a failure. Either one can void the PASS.

## Numbers (QP's independent computation)

- Test A (every send, buys and sells, failed in the denominator): test 150/152 = 98.684%, control 146/148 = 98.649%.
  Diff +0.036 pp. Newcombe-Wilson one-sided 95% lower bound -2.700 pp (z 1.645). Margin -5. HOLDS. The point is not <= -5, so this is not a FAIL.
- Test B (landed buys, slots_between): test mean 2.2000 (n 75) vs control 2.4384 (n 73). Diff -0.2384 slot.
  95th percentile of 1,000 bootstrap draws, seed 1: -0.0073 with numpy default_rng(1) (the lab's convention, arena_stack.boot_lo). Margin +0.5. HOLDS. The point is not >= +0.5.
- Secondary (does not change the verdict): slot+1 share test 6/75 vs control 3/73, two-sided Fisher p 0.494. Not landed: test 2/152 vs control 2/148, Fisher p 1.0. All 4 failures are slippage_exceeded.

## (1) The unlogged-expired caveat

Helm's statement "non-landing / expired sends are not recorded, dropped = 0 by construction" is WRONG about the installed code. dropped = 0 is a measurement, not a construction.

- probe_live.py is byte-identical at db4af222 and d29ae534 (git diff is empty).
- On expiry, `_resolve_expired` writes a buy or sell row with landed=False and fail_class="expired".
- `Executor._log` tags that row as outcome "dropped" (probe_executor.py line 1254 at db4af22).
- tools/feearm_watch.py counts "dropped", and watch #1083 reported "0 expired".
- So the absence of "dropped" rows means no send expired, as long as Helm's extract kept every buy and sell row.

How much this matters: Test A is fragile. 3 hypothetical unlogged, unlanded test-arm sends would take the lower bound to -5.250 pp, which fails. So the logging question decides the verdict, and the coverage argument is needed.

The coverage argument holds for buys. QP verified each point below.
- All 150 indices 0..149 are present, and each has exactly 1 buy row.
- The executor never builds a second buy for one attempt (probe_live docstring step 3).
- An unlogged buy is therefore impossible unless a whole index is missing, and none is.
- Arms match feearm_pick(20261016, i) for all 150 indices (0 mismatches). Buy index order is monotone in time.
- No index has a sell after a failed buy. Every landed buy has exactly 1 landed sell, and that sell is its last row.

For sells the argument is only partial. Every position closed, but an expired sell attempt before the landed one would leave no trace in this extract if the extract had dropped it.

Coverage confirmation C2 (Helm, read-only, 2 numbers) closes the gap:
- (a) the count of raw ledger buy/sell rows with fail_class "expired" or outcome "dropped" (expect 0);
- (b) the `--status` feearm `sends=` counter (expect 300). The counter is incremented once per signed tx at its first send, whatever the outcome.

If (b) is 300 and (a) is 0, the ledger is complete and the PASS stands. Otherwise Test A is rerun with every extra send counted as not landed in its arm.

## (2) Other issues

- Rebroadcast rule. OK. sends_attempts = 1 on all 300 rows, so there were no rebroadcasts. A sell retry is a new signed tx, so it is a new unit (test sells 77 = 75 + 2 failed retries, indices 26 and 53). That is what the DEC says, and it is the conservative count. Collapsing retries would give test 152 -> 150 sends, all landed.
- RNG/seed ambiguity. Not material. The canonical value is numpy default_rng(1) = -0.0073. RandomState(1) gives +0.0064, stdlib Random(1) +0.0035 / +0.0060. Over seeds 1..200 (default_rng) the range is -0.022 .. +0.032. The margin is +0.5. The lab note names default_rng(1) and draws test before control within each draw.
- Executor build sha. NOT RECORDED in the repo; this is confirmation C1. The 0-buy install was db4af222. Buys started only after the d29ae53 reinstall (PR #669, owner question q_RA-Txtw4ncevOA), and no install record exists on main.
  - Helm states the installed sha(s) and install instants for 2026-10-16T19:44Z .. 10-19T22:00Z.
  - Expected: d29ae534905a3179d575d994f437bf54079d35f8 and no switch inside the window.
  - If any other sha ran, the rows are grouped by sha and the tests rerun on the deciding group, as the DEC requires.
  - db4af22 vs d29ae53 differ only in the H5 precheck path and the entry-k config, so neither can change a landing.
- Hour-of-day (reported, not a gate). Buy attempts per UTC hour (test/control):
  00 5/6, 01 5/2, 02 4/7, 03 6/3, 04 3/7, 05 7/3, 06 5/6, 07 3/3, 08 2/3, 09 2/2, 10 0/1, 11 1/0, 12 1/1, 13 2/2, 14 3/2, 15 2/2, 16 2/3, 17 2/1, 18 0/1, 19 4/2, 20 6/6, 21 6/8, 22 2/2, 23 2/2.
  - By date: 10-16 14, 10-17 42, 10-18 26, 10-19 68.
  - 10Z-18Z (US/EU overlap) holds only 24 of 150 buys (16%). The measurement is thin at peak congestion, and the lab note must say so.
- Sell-side split (descriptive, not a test). Test sells landed 75/77 vs control 73/73. That split's lower bound is -7.55 pp, and Fisher p is 0.497.
  - Both failures landed on chain with slippage_exceeded. They did not fail to land.
  - It is a watch item for any live 155k book: track its sell slippage-fail share against the 505k history.
- Lag shift. The DEC wording says "the measured lag shift is added". The measured shift is NEGATIVE (-0.238). It is driven by 3 control outliers (lags 6, 8, 8); without them the diff is -0.029. The test was non-inferiority, not superiority. QP ruling: the shift added to the simulator is max(0, measured) = 0 slots. Crediting a latency gain needs its own pre-registered test.

## (3) What to record, and what it changes

Lab note, pinned wording, with the values filled in:
"At 155,000 priority, 150/152 sends landed vs 146/148 at 500,000 (lower bound -2.70 pp, margin -5); buy lag 2.20 vs 2.44 slots (upper bound -0.007, margin +0.5). From DEC-019 Amendment 2, the simulator send cost is 160,000 lamports, and the measured lag shift is added. This is a cost input, not a P&L result. No book changes status, and no recomputed history counts as out-of-sample."

Add, in the same note:
- the lag shift added is 0 (measured -0.24, not credited, see above);
- the build sha (C1) and the C2 numbers;
- the hour-of-day table and the 10-18Z thinness;
- that dropped = 0 was measured (expiry rows are written by the code);
- the bootstrap RNG (numpy default_rng(1));
- no probe P&L.

What it does NOT change:
- No running book's pinned stack. Every registered arena book (HV4, H5 v1/v2/Q50 and the rest) keeps 505,000 for its whole counted window. A mid-window repricing is a changed stack, and a book that reprices is a new book.
- No past result. Rerunning old screens at 160,000 is exploration only: in-sample, never out-of-sample, never a gate input. A cell that turns positive only because the fee fell is a new hypothesis, and it is subject to winner's curse across every cell rerun.
- No promotion gate threshold. No live executor's priority: the live code still pays 500,000. A sim floor of 160,000 is honest only for a book whose live path actually sends at 155,000. That needs its own reviewed executor change before any live trial.

DEC-031 for new arena books: YES, an amendment is required.
- DEC-031 §1.4 pins "505,000 lamports per send" explicitly. Amendment 2 item 1 restates it as unchanged.
- The tools/arena_paper.py sha256 is a §1.4 pin. SEND_LAMPORTS = arena_stack.SEND_FEE_DECIDING = 505,000, and arena_screen.py, arena_rules.py and arena_h5_cost.py all hard-code 505_000.
- A DEC-019 cost input cannot override merged DEC-031 text. Amendment 2 already set this precedent for rent: "a config ruling alone is not enough".
- The amendment names the covered books (registered after its merge only) and the new send value (160,000) and its source (this ruling plus C1/C2). It carries a new parity proof at the new sha, a QP OK on the exact head and a reviewer verdict, and it merges before the first clock it touches.
- QP recommends that covered books also print a 505,000 leg in every read, for comparison only, and that the amendment state the 10-18Z thinness as a residual risk.
