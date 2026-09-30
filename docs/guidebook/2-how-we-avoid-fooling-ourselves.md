# 2. How we avoid fooling ourselves

> **In short**
> Every strategy is tried many times before one version looks good — that's exploration, and it proves nothing by itself. Confirmation is a single, pre-registered test on data nobody has looked at yet, read exactly once. Most of the discipline in this document exists to stop a good-looking exploration number from being mistaken for a real edge.

## Exploration vs. confirmation

MAL keeps two kinds of testing strictly apart:

**Exploration** is where ideas get tried, tuned, and compared — many settings, many exits, many thresholds, against a shared pool of historical data everyone can reuse. This is necessary and cheap, and it is *never* evidence of an edge by itself. A cell that looks good on exploration data is, at most, a hypothesis worth pre-registering.

**Confirmation** is a single, fixed test: one pre-registered rule, scored exactly once, on a block of data that rule has never touched before. This is the only kind of read that can promote anything.

The reason for the split is simple: if you try 100 things against the same data, roughly 5 will look statistically significant purely by chance, with zero real edge. Exploration is where you pay that cost cheaply, in ideas you throw away. Confirmation is where you find out if the one idea you kept survives data it never got to peek at.

## The holdout ledger, and why a block can be read once

Every block of sealed historical hours has exactly one owner — either a single confirmation experiment (an `EXP-###` file) or the shared exploration pool — recorded in `docs/HOLDOUT_LEDGER.md` **before** any hour in it is sealed or read. This isn't a formality. If a confirmation test's holdout block had already been glanced at during exploration, its "out-of-sample" read wouldn't be out-of-sample at all — the model, even indirectly, would have been shaped by data it's supposed to be judged against.

The rules: a block enters the ledger with a named owner before its hours exist on disk; any non-owner read of an owned block must be disclosed, dated, in that owner's `EXP-###` file; a block never has two owners at once (`docs/HOLDOUT_LEDGER.md` rules 1–4, adopted by `DEC-014`).

EXP-011 is the current live example. Its holdout, `[2026-09-09T12, 2026-09-15T12)` (144 fast-box hours), was reserved for it on 2026-09-29T00:32Z — before a single hour of that range was sealed. The pre-registration file itself is explicit that **no holdout hour has been read**: even though 114 of the 144 hours were already sealed on disk by the time the pre-registration was finalized (2026-09-30T07:00:17Z), the freeze script asserts it never touches that block, and the scoring script has only run on synthetic test fixtures (`EXP/EXP-011-migrate-entry-model-prereg.md`). The scoring tool, `tools/exp011_score.py`, checks that *both* backfill walkers covering the block show every one of their hours sealed before it will run at all, and it writes a read-once lock so the block cannot be scored a second time. That lock is the entire point: a pre-registration that could be re-read after a disappointing first result would not be a confirmation test, it would just be exploration with extra paperwork.

## Winner's curse, and "variant N of M tried on this data"

The winner's curse is what happens when you pick the best of many tries and then treat its number as if it were the *only* thing you tried. The best of a big pile of random noise still looks impressive — that's what "best of" does, even with zero real signal underneath.

MAL's clearest example is the original frozen migrate-direct cell. It was chosen as the best of a **972-cell grid** — a full sweep of route, size, and priority settings on one selection window (`ARTIFACTS/lab/migrate-direct-prereg.md`; the grid itself is in `ARTIFACTS/lab/latency-curve-2026-09-27.md`, "No route, size, or priority in the grid is positive on both models (972 cells)"). Being the best of 972 does not mean it is good — it means it is the best of 972, which is a different claim. `LAB_STATE.md` says this in plain terms: "The first positive in-sample cell does **not** clear this gate... it was the best of 972 cells, the CI lower bound is below 0, and the out-of-sample book is still small." That cell went on to formally fail its one-shot out-of-sample read on both fail models (`LAB_STATE.md`).

This is why every result card is expected to show **"variant N of M tried on this data"** (`docs/console-plan.md` §2 rule 1) — not to shame an idea for having been compared to others, but because "best of 972" and "the only cell we tried" carry completely different amounts of evidence, even if the raw number looks identical.

## Why ex-top-3 catches moonshot-driven results

A pooled total can be positive purely because of a handful of extreme trades, while the *typical* trade in that same book is a loser. Ex-top-3 — the book's total with its 3 best trades removed — is the cheapest check for that pattern, and MAL has a clean failure to show for it.

The trailing-stop exit (`trail_30_act20`: trail 30% off the running high, arming at +20%, 30-minute cap) looked like the best of a 41-cell exit sweep on the fast-box exploration pool: pooled pressure total **+7.217 SOL**, flat total **+23.245 SOL**, with per-day pressure means of +1.367% / +0.233% / −0.110% (`ARTIFACTS/lab/exploration-exits-2026-09-28.md`). But its top 3 trades alone were worth 16.58, 9.68, and 4.47 SOL (flat) — one single 0.5 SOL position returned +16.58 SOL. Remove those three and the pressure total flips to **−8.048 SOL**; the flat total flips to **−7.492 SOL**. Without its moonshots, the "winning" exit rule is actually *worse* than the plain take-profit/stop-loss rule it was being compared against (`tpsl_tp50_sl30` pressure total −3.164 SOL, ex-top-3 −4.426 SOL — a smaller loss). The manager's verdict on this: "the typical trade loses; exits only harvest rare tails" (`LAB_STATE.md`), and the candidate was never pre-registered — its ledger reservation was released, unread and unassigned. This is exactly the failure ex-top-3 is built to catch: a positive pooled number that quietly depends on 3 trades out of thousands.

## Review blindness: why paper P&L is hidden until the kill review

During a registered forward-paper review window, the Console shows *how* the runner is operating — lag, fill counts, errors, restarts — but not each book's actual profit-and-loss, until the review instant itself (`docs/console-plan.md` §2, "Paper P&L is blinded during review windows"). This is deliberate, not an oversight: DEC-014(c) requires the kill-review books to be "read once, at 2026-10-05T05:00:00Z... no interim read of those books before that instant may be used to make or influence a promote decision" (`DEC/DEC-014-holdout-ledger-and-multiplicity.md`).

The reason is the same one that motivates the holdout ledger, applied to your own eyes instead of a model: if you can watch a book's running P&L climb and dip all week, you will — consciously or not — start reading meaning into short streaks that are pure noise, and that reading will bleed into how the eventual formal read gets interpreted. Blinding the number until the single scheduled read keeps the review honest the same way a locked holdout block keeps a backtest honest. Monitoring reads (is the runner alive, is it lagging, did it crash) are unaffected by this — only the number that would tempt a promote/kill judgment is hidden.

## Multiplicity, in plain words

If you flip nine fair coins and ask "did any of them come up heads at least 9 times out of 10 tries," you'll very likely get a "yes" on at least one coin — not because any coin is rigged, but because you gave nine coins nine separate chances. Reading many candidate books at one sitting and promoting whichever one clears the bar has the same problem: each individual book is being tested at roughly a 5% false-positive rate, but the chance that *at least one* of several books clears the bar by luck alone is much higher than 5%.

MAL's kill review reads all 9 forward-paper books together at one sitting. With k = 9 books, each individually tested at a nominal 5% false-positive rate, the chance that at least one clears its bar with zero real edge in any of them is approximately 1 − 0.95⁹ ≈ 37% (`DEC-014`) — not a small-sample fluke, just what happens when you read 9 tests at once and take the best one.

**Holm–Bonferroni** is the fix: instead of comparing every book's p-value to the same 5% threshold, you sort the books by how extreme their result is and demand progressively stricter thresholds for anything hoping to promote. The smallest p-value has to beat α/k (for k=9, that's 0.05/9 ≈ 0.0056); the next-smallest has to beat α/(k−1); and so on — and a book only passes at its rank if every book ranked ahead of it also passed. This bounds the *family-wise* chance of a false positive at 5% across all 9 reads combined, instead of letting each book gamble at 5% independently. It can only make the gate stricter, never looser — a book that already fails the base gate (trade count, day count, CI, ex-top-3) still fails regardless of its Holm rank (`DEC-014`). Because the smallest measurable p-value with only 1,000 bootstrap draws (0.001) is too coarse near a 0.0056 threshold, any multiplicity-tested read uses 10,000 draws instead, same seed (`DEC-014`).

A single pre-registered primary cell, read alone, is k = 1 — no correction needed, because there's nothing to correct against. That's EXP-011's situation: its pre-registration declares exactly one cell, so its holdout read (once it happens) needs no Holm step-down (`EXP/EXP-011-migrate-entry-model-prereg.md` §5).

## Why a strong exploration result can still fail its confirmation

Lane B3's screen found that an entry-selection model (`s2_clf`, taking the top 10% of migrate entries by a classifier score, exiting on a fixed take-profit/stop-loss) was the best of 6 pre-stated screening cells over a 9-day leave-one-day-out read: flat **+6.73%** (CI lo +4.35%), pressure **+3.84%** (CI lo +2.35%), **9/9 days** positive under both fail models (`ARTIFACTS/lab/exploration-entry-model-b3-2026-09-28.md`, quoted in `EXP/EXP-011-migrate-entry-model-prereg.md` §1). 9 out of 9 days positive is about as clean a screen result as this lab has produced.

And yet EXP-011's own pre-registration lists several concrete reasons that number could still evaporate on confirmation, before any holdout hour was read:

- **It's still the best of 6.** Winner's curse applies exactly as above, just with a smaller pile (6 cells instead of 972).
- **A lookahead bug was found and fixed.** Two of the original 20 features were computed from data that, on closer audit, existed only *after* the decision time they were supposed to predict at — a leak the no-lookahead test didn't cover. The number quoted above is already the *post-fix*, ablated result; the pre-fix version was slightly better and would have been wrong to trust.
- **The 9 days were reused across three separate PRs** (#146, #152, #156), each building on what the last one saw — so some fit to that specific week, beyond what a clean 9-fold LODO can detect, is likely on top of the winner's-curse effect.
- **The holdout itself is drawn from the weakest of the three source pools.** In the exploration split by source, the fast-box slice was consistently the *worst* performer (pressure +1.24% after ablation, versus +4.52% Oracle in-sample and +5.31% Oracle live) — and EXP-011's entire 144-hour holdout is fast-box data. A holdout result weaker than the pooled 9-day headline is the expected outcome here, not a sign of a broken model (`EXP-011` §1).

None of this means the model is bad — it means a 9/9-day exploration screen, however clean-looking, is not yet evidence. That's precisely why EXP-011 exists as a pre-registration rather than a promotion: the frozen model, threshold, and feature set are locked, the holdout is reserved and unread, and the single scored read — whichever way it comes out — is the only thing that will actually answer the question (`EXP/EXP-011-migrate-entry-model-prereg.md`).

## Glossary

- **Exploration** — trying and comparing many settings against a shared, reusable pool of historical data; never evidence of an edge by itself.
- **Confirmation** — a single, pre-registered test scored exactly once on a fresh block the rule has never touched.
- **Pre-registration** — a file written and frozen *before* a holdout is read, locking the model, threshold, and feature set so nothing can be tuned in response to the result.
- **Holdout ledger** — the table (`docs/HOLDOUT_LEDGER.md`) recording which experiment owns which block of sealed hours, so no block is read twice or by the wrong owner.
- **Read-once lock** — a mechanical guard (e.g. in `tools/exp011_score.py`) that refuses to score a holdout block a second time.
- **Winner's curse** — mistaking "the best of many tries" for "a good result," when being the best of a large pile is itself mostly what you'd expect from chance.
- **Variant N of M tried on this data** — the disclosure that tells you how many other settings were compared before this one was picked, so you can judge how much winner's-curse risk applies.
- **Ex-top-3** — a book's total with its 3 best trades removed; catches results propped up by a few moonshot trades rather than a repeatable edge.
- **Review blindness** — hiding a forward-paper book's live P&L until its scheduled, single review read, so ongoing noise doesn't bias the eventual judgment.
- **Kill review** — the single scheduled instant (2026-10-05T05:00:00Z) at which the 9 forward-paper books are read together, once.
- **Multiplicity** — the problem that reading several candidates together inflates the chance that at least one looks like a winner purely by chance.
- **Holm–Bonferroni** — a step-down procedure that raises the bar for each of several simultaneous tests so the *combined* false-positive rate across all of them stays at the target level (5% here).
- **Family-wise error rate** — the chance of at least one false positive across an entire group of simultaneous tests, as opposed to just one test.
- **Bootstrap draw** — one resample of the observed trades, used to build a distribution of plausible means; MAL uses 1,000 draws (seed 1) for a normal read and 10,000 (seed 1) for any multiplicity-tested read.
