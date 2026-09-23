# EXP-007c Oracle runner (`mal-core-vnic`)

Fee-at-T restamp on the **existing EXP-007b capped** enrich sample (no resample). Re-scores **K-fee-knowable-at-t** on enriched audit overlay.

**Hard caps:** reuse `_exp007b-oracle-*` parent signatures only; rate-limited public RPC; **no** sealed rewrite; **merge ≠ authorize** observe-wiring.

## Host layout

| Path | Role |
| --- | --- |
| `/var/lib/mal/sealed/jsonl/observe-2026-09-{20,21}.jsonl` | Sealed ingest (read-only) |
| `/var/lib/mal/paper/_exp007b-oracle-2026-09-{20,21}_regime_enrich.jsonl` | EXP-007b enrich input |
| `/var/lib/mal/paper/_exp007c-oracle-2026-09-{20,21}_regime_enrich.jsonl` | Fee-stamped enrich output |

## One-shot (from agent VM)

```bash
REPO=/var/lib/mal/src/MAL
OUT=/var/lib/mal/paper
B_IN=_exp007b-oracle
B_OUT=_exp007c-oracle

for DAY in 2026-09-20 2026-09-21; do
  python3 -m tools.exp007c_fee_at_t \
    "$OUT/${B_IN}-${DAY}_regime_enrich.jsonl" \
    "$OUT/../sealed/jsonl/observe-${DAY}.jsonl" \
    --output "$OUT/${B_OUT}-${DAY}_regime_enrich.jsonl"
done

python3 -m tools.exp007_regime_audit \
  /var/lib/mal/sealed/jsonl/observe-2026-09-20.jsonl \
  /var/lib/mal/sealed/jsonl/observe-2026-09-21.jsonl \
  --output-dir "$OUT" \
  --prefix "${B_OUT}" \
  --enrich-jsonl \
    "$OUT/${B_OUT}-2026-09-20_regime_enrich.jsonl" \
    "$OUT/${B_OUT}-2026-09-21_regime_enrich.jsonl"
```

Remote via SSH wrapper:

```bash
scripts/mal-core/agent-ssh.sh -- bash -lc 'cd /var/lib/mal/src/MAL && …'
```

## Stamp honesty

- Update [EXP-007c](../EXP/EXP-007c-fee-knowable-at-t-v0.md) result table from real `fee_counts` in restamp summary JSON only.
- Sealed full-book **INCOMPLETE** on fee remains expected.
