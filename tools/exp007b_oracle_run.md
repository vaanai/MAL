# EXP-007b Oracle runner (`mal-core-vnic`)

Paper-only RPC platform enrich + sealed regime audit re-score on courier **2026-09-20** and **2026-09-21**. Access via [DEC-011](../DEC/DEC-011-cursor-oracle-access-cf-tunnel.md) — [`scripts/mal-core/agent-ssh.sh`](../scripts/mal-core/agent-ssh.sh).

**Hard caps (enforced in CLI):**

- ≤**500** bonding creates enriched per courier day (`tools/exp007_rpc_enrich.cap_sample_n`)
- Cheap-first public RPC (`SOLANA_RPC_URL` on host; throttle in `SolanaRpcClient`)
- Append-only `regime_enrich` under `/var/lib/mal/paper/` — **no** sealed JSONL rewrite
- **Merge ≠ authorize** continuous observe-wiring or encoder production promote

## Host layout

| Path | Role |
| --- | --- |
| `/var/lib/mal/sealed/jsonl/observe-2026-09-{20,21}.jsonl` | Sealed ingest (read-only) |
| `/var/lib/mal/paper/_exp007b-oracle-2026-09-{20,21}_*` | Gitignored enrich + audit artifacts |

## One-shot (from agent VM)

```bash
REPO=/var/lib/mal/src/MAL   # or synced checkout path on host
OUT=/var/lib/mal/paper
PREFIX=_exp007b-oracle

for DAY in 2026-09-20 2026-09-21; do
  python3 -m tools.exp007_rpc_enrich \
    "$OUT/../sealed/jsonl/observe-${DAY}.jsonl" \
    --output-dir "$OUT" \
    --prefix "${PREFIX}" \
    --sample 500 \
    --seed 1
done

python3 -m tools.exp007_regime_audit \
  /var/lib/mal/sealed/jsonl/observe-2026-09-20.jsonl \
  /var/lib/mal/sealed/jsonl/observe-2026-09-21.jsonl \
  --output-dir "$OUT" \
  --prefix "${PREFIX}" \
  --enrich-jsonl \
    "$OUT/${PREFIX}-2026-09-20_regime_enrich.jsonl" \
    "$OUT/${PREFIX}-2026-09-21_regime_enrich.jsonl"
```

Remote via SSH wrapper:

```bash
scripts/mal-core/agent-ssh.sh -- bash -lc 'cd /var/lib/mal/src/MAL && …'
```

## Stamp honesty

- Report **PENDING_ORACLE_RUN** in the EXP-007b doc until host artifacts exist.
- Never fabricate coverage — stamp from real `regime_enrich` + audit summary JSON only.
