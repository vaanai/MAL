Write a new experiment file under `EXP/` from this template. Do not invent a result. Do not promote.

```markdown
# EXP-xxx — Title

- **Status:** planned | running | done
- **Owner seat:** Scout | Graph | Proof | Helm
- **Started:** YYYY-MM-DD
- **Hypothesis:** …
- **Method:** …
- **As-of-T:** …
- **Regime labels:** …
- **Windows:** 1s, 5s, 15s, 30s, 60s — primary: …
- **Fail models:** flat 15% and pressure scale 1, if the claim is a return
- **Kill-attempt:** …
- **Promotion gate:** n ≥ 100 OOS, ≥ 5 UTC days with a majority positive, 90% CI lower bound of mean SOL/trade > 0, still positive after dropping the top 3, under both fail models
- **Result:** …
- **Conclusion:** …
```

Rules:

- One experiment, one file: `EXP/EXP-xxx-short-slug.md`.
- Knowable-at-T only. No lookahead.
- A kill-attempt is required before any promote wording.
- Copy the next free id from `EXP/README.md` and add a row to that index.
- Branch `claude/exp-xxx-<slug>`. Do not merge.
