#!/usr/bin/env bash
# EXP-007e Oracle one-shot on mal-core-vnic (paper-only).
set -euo pipefail
REPO_ROOT="${REPO_ROOT:-/tmp/mal-exp007e}"
export PYTHONPATH="${REPO_ROOT}"
OUT=/var/lib/mal/paper
B_IN=_exp007d-oracle
B_OUT=_exp007e-oracle

for DAY in 2026-09-20 2026-09-21; do
  echo "=== EXP-007e restamp ${DAY}"
  python3 -m tools.exp007e_instr_quote_rescore \
    "${OUT}/${B_IN}-${DAY}_regime_enrich.jsonl" \
    "/var/lib/mal/sealed/jsonl/observe-${DAY}.jsonl" \
    --output "${OUT}/${B_OUT}-${DAY}_regime_enrich.jsonl"
  python3 -m tools.exp007e_residual_diagnose \
    "${OUT}/${B_IN}-${DAY}_regime_enrich.jsonl" \
    --output "${OUT}/${B_OUT}-${DAY}_residual_diagnose_before.json"
  python3 -m tools.exp007e_residual_diagnose \
    "${OUT}/${B_OUT}-${DAY}_regime_enrich.jsonl" \
    --output "${OUT}/${B_OUT}-${DAY}_residual_diagnose_after.json"
done

python3 -m tools.exp007_regime_audit \
  /var/lib/mal/sealed/jsonl/observe-2026-09-20.jsonl \
  /var/lib/mal/sealed/jsonl/observe-2026-09-21.jsonl \
  --output-dir "${OUT}" \
  --prefix "${B_OUT}" \
  --enrich-jsonl \
    "${OUT}/${B_OUT}-2026-09-20_regime_enrich.jsonl" \
    "${OUT}/${B_OUT}-2026-09-21_regime_enrich.jsonl" \
  > "${OUT}/${B_OUT}-audit_summary.json" || true

echo "EXP-007e oracle stamp complete"
