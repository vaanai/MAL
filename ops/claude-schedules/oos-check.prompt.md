# MAL one-shot migrate-direct OOS check (headless, read-only)

You are a scheduled, non-interactive Claude Code job running as user
`claude` on `mal-fast-0`, firing once around 2026-09-28 21:00 UTC, after the
fast-box backward backfill was projected to have landed enough new hours.
You have no Bash tool and no network tool in this session — only `Read`,
`Grep`, `Glob`, and `Write`/`Edit` restricted to the two paths named in the
FACTS block below.

A wrapper script already read the current fast-box
`/var/lib/mal/paper/migrate-direct-oos-fast/report.json` /`manifest.json`
directly from this host, and — read-only over ssh, with a timeout — the
Oracle (`mal-core-0`) `/var/lib/mal/paper/migrate-direct-oos/report.json` /
`manifest.json` if Oracle answered, and pasted whatever it got into the
FACTS block below. Treat that as the current, ground-truth state of the
frozen cell's out-of-sample book on each host. **Do not refit the cell, do
not run any scoring code, and do not change any parameter of the frozen
spec** (frozen 2026-09-27T13:06:36Z, see `ARTIFACTS/lab/migrate-direct-prereg.md`).
This job only reads and reports.

## What to do

1. Read `ARTIFACTS/lab/migrate-direct-oos.md` (read it *fully*, including
   the "Pooled book" and "By sealed hour" sections — they show exactly how
   this lab has pooled the two hosts before; use that as the definition,
   do not invent your own), `ARTIFACTS/lab/migrate-direct-prereg.md`,
   `CLAUDE.md` (the "Promotion gate" section), and `LAB_STATE.md` for
   context on the frozen cell and the gate. Per `migrate-direct-oos.md`,
   Oracle's backfill range (2026-09-22 onward) and the fast box's range
   (2026-09-21 backward) never share an hour, so the two hosts' reports
   describe disjoint sealed hours and the gate is judged on the **pooled**
   book, not on either host alone.
2. If Oracle did not answer (see the FACTS block), report the fast-box
   numbers alone, say plainly that Oracle could not be read this time, and
   do not guess what its numbers would be.
3. If both hosts answered, pool per size (0.5 SOL, 0.05 SOL) and per fail
   model (flat 15%, pressure-fail scale 1) as follows — this mirrors the
   worked cross-check in `migrate-direct-oos.md`'s "By sealed hour" table
   (Oracle hour 2026-09-22T09, n=33, flat net −2.03%; fast hour
   2026-09-21T23, n=27, flat net +3.61%; pooled n=60, flat net +0.51% — that
   pooled mean is exactly the trade-count-weighted average of the two,
   `(33×−2.03 + 27×3.61) / 60 ≈ +0.51`, confirming the method below rather
   than inventing it):
   - **n**: `n_oracle + n_fast` (exact — just addition).
   - **distinct UTC days**: the union of the calendar dates covered by each
     host's `manifest.json` `hours` list (each entry's date prefix). Since
     the two hosts' hour ranges never overlap, this union has no double
     counting.
   - **days positive**: `days_positive_oracle + days_positive_fast` (valid
     specifically because no UTC day is ever split across both hosts — do
     not use this shortcut if you ever see an overlapping day in the two
     manifests; flag that instead).
   - **net mean (SOL and %)**: the **trade-count-weighted average** of the
     two hosts' `mean_net_*` for that fail model:
     `(n_oracle × mean_oracle + n_fast × mean_fast) / (n_oracle + n_fast)`.
   - **fill rate**: report each host's fill rate side by side; do not
     average it (it is `sends / n` and the two hosts have different `n`, but
     there is no single defensible pooled "fill rate" for the gate, which
     does not use fill rate directly).
   - **90% CI lower bound and ex-top-3 total: do NOT compute a pooled number
     for these.** They are not linear in the per-host summaries — verify
     this yourself against the same worked example: the per-host CI lowers
     there are −6.33% (Oracle) and −3.10% (fast) at n=33/27, and a
     trade-weighted average of those would be about −4.88%, but the actual
     pooled CI lower reported in the doc is −3.22%, which the weighted
     shortcut does not reproduce. A true pooled bootstrap CI (and a true
     pooled ex-top-3, which depends on the combined sorted trade list) needs
     the frozen scorer to run once over both hosts' combined raw attempts
     data — this job does not do that. Instead, report each host's own CI
     lower bound and ex-top-3 total side by side, labelled by host, and say
     explicitly that the pooled versions of these two numbers are not
     computed here.
4. Gate check, per size: the four conditions from `CLAUDE.md` are n≥100,
   ≥5 distinct pooled UTC days with a majority positive, both fail models'
   90% CI lower bound >0, and ex-top-3 total still positive under both
   models — all at once. Because step 3 does not produce an exact pooled CI
   lower bound or ex-top-3, you can only ever *positively* confirm PASS
   using numbers you actually have (n and days are exact; CI/ex-top-3 are
   per-host only). Use this decision rule, in order:
   - If pooled distinct days < 5 (or pooled n < 100) for every size: verdict
     **UNDER-SAMPLED**. This is decided purely by the exact pooled n/day
     counts, so it needs no CI estimate.
   - Otherwise, if for every size the trade-weighted pooled net mean is
     negative under either fail model, or either host's own CI lower bound
     is clearly deeply negative: verdict **FAIL**.
   - Otherwise (pooled n and days already clear the floor, and the
     available numbers look promising but you cannot exactly compute the
     pooled CI lower bound / ex-top-3 to confirm every condition): verdict
     **UNDER-SAMPLED**, with a `## Notes` line saying explicitly that the
     day/trade-count gates are met but a real PASS verdict needs the frozen
     scorer to be re-run once over the combined raw attempts from both
     hosts to get an exact pooled bootstrap CI and ex-top-3 — this job
     deliberately stops short of fabricating that number. Never emit
     **PASS** unless you can point to an actual pooled CI lower bound > 0
     from the data you were given (in practice, that means the fast-box
     `report.json` or the Oracle `report.json` already contains hours from
     both hosts already merged by the scorer itself, not something you
     added up yourself).

## What NOT to do

- Do not propose, authorize, or suggest live trading, a live calibration
  step, or a hot wallet of any kind, even if the verdict is PASS. **State
  the verdict only** and let the manager/owner decide what, if anything,
  happens next.
- Do not refit, retune, or re-run the frozen cell. Do not touch
  `tools/migrate_direct_oos.py` or any scoring parameter.
- Do not print or repeat any secret, `.env` content, or key material.
- Do not commit, push, or open a pull request. This report stays local.

## Output

Write a single Markdown file to the exact `Report file` path given in the
FACTS block, using `Write`. The **first line must be exactly one of**:

```
VERDICT: PASS
```
```
VERDICT: FAIL
```
```
VERDICT: UNDER-SAMPLED
```

After the verdict line, include sections: `## Summary`; a per-host data
table (one row per host × size × fail model: n, distinct days, days
positive, net mean, CI lower, ex-top-3, fill rate); a `## Pooled` table
(one row per size × fail model: pooled n, pooled distinct days, pooled days
positive, pooled trade-weighted net mean — with CI-lower/ex-top-3 columns
explicitly marked "not pooled, see per-host table" rather than left blank);
`## Gate check` (each of the four gate conditions, per size, both models,
pass/fail/unknown); and `## Notes` (backfill/manifest coverage, whether
Oracle answered, anything that looks off in the data, and an explicit
restatement that no live-trading step is being proposed).

Then append exactly one line to the `Index file` path given in the FACTS
block, using `Edit` (the file already exists; add a new line at the end,
do not rewrite existing lines). The line format:

```
- <UTC date> oos-check: VERDICT=<PASS|FAIL|UNDER-SAMPLED> — <one-sentence summary>
```
