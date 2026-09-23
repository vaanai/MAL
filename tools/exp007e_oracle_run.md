# EXP-007e Oracle runner (`mal-core-vnic`)

Knowable-at-T **instr / quote** re-decode on reused **EXP-007d** enrich (same ≤500/day sample; no resample). Preserves **007d** fee labels. Re-scores **K-platform-rpc-resolved** + leak gate.

**Hard caps:** reuse `_exp007d-oracle-*` parent set only; **merge ≠ authorize** observe-wiring or enum production lock (`global_95bps` stays **Proposed**).

## Host layout

| Path | Role |
| --- | --- |
| `/var/lib/mal/sealed/jsonl/observe-2026-09-{20,21}.jsonl` | Sealed ingest (read-only) |
| `/var/lib/mal/paper/_exp007d-oracle-2026-09-{20,21}_regime_enrich.jsonl` | Fee-rescored input |
| `/var/lib/mal/paper/_exp007e-oracle-2026-09-{20,21}_*` | EXP-007e output + diagnose JSON |

## One-shot

Sync checkout to `/tmp/mal-exp007e` (or use repo on host), then:

```bash
bash /tmp/mal-exp007e/tools/exp007e_oracle_run.sh
```

Remote:

```bash
# from agent VM
cd /workspace && tar cf - tools observe | scripts/mal-core/agent-ssh.sh -- \
  'rm -rf /tmp/mal-exp007e && mkdir -p /tmp/mal-exp007e && cd /tmp/mal-exp007e && tar xf -'
scripts/mal-core/agent-ssh.sh -- bash /tmp/mal-exp007e/tools/exp007e_oracle_run.sh
```

## Stamp honesty

- Report **K-platform-rpc-resolved** from audit `enriched_sample` only — sealed full book stays **INCOMPLETE**.
- **PASS** only if instr ≤5% pending/unknown **and** quote_verified ≥90% on enriched overlay.
- Document `residual_taxonomy` before/after (`_residual_diagnose_{before,after}.json`).
