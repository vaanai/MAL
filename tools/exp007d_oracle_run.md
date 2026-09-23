# EXP-007d Oracle runner (`mal-core-vnic`)

Decode-only fee re-label on **existing EXP-007c** enrich (no resample, no new RPC). Applies **Proposed** `fee=global_95bps` when stored `rpc_meta.fee_resolve.global_fee_bps == 95`, then re-scores **K-fee-knowable-at-t**.

**Hard caps:** reuse `_exp007c-oracle-*` rows only; **merge ≠ authorize** observe-wiring or enum production lock.

## Host layout

| Path | Role |
| --- | --- |
| `/var/lib/mal/sealed/jsonl/observe-2026-09-{20,21}.jsonl` | Sealed ingest (read-only) |
| `/var/lib/mal/paper/_exp007c-oracle-2026-09-{20,21}_regime_enrich.jsonl` | EXP-007c fee-stamped input |
| `/var/lib/mal/paper/_exp007d-oracle-2026-09-{20,21}_regime_enrich.jsonl` | EXP-007d re-labeled output |

## One-shot (from agent VM)

```bash
REPO=/var/lib/mal/src/MAL
OUT=/var/lib/mal/paper
B_IN=_exp007c-oracle
B_OUT=_exp007d-oracle

for DAY in 2026-09-20 2026-09-21; do
  python3 -m tools.exp007d_fee_rescore \
    "$OUT/${B_IN}-${DAY}_regime_enrich.jsonl" \
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

- Update [EXP-007d](../EXP/EXP-007d-fee-global-95bps-enum-v0.md) result table from `fee_counts` + audit `K-fee-knowable-at-t` only.
- Sealed full-book fee **INCOMPLETE** remains expected (no enrich overlay on book).
- `global_95bps` is **Proposed** in [REGIME-ENUM-V0.md](../ARTIFACTS/REGIME-ENUM-V0.md) — stamp PASS on gate does **not** promote enum to production law.
