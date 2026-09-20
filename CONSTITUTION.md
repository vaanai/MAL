# MAL Constitution (non-negotiables)

Violations require an explicit DEC override with kill-attempt evidence.

## Observation and discovery

1. **Continuous observation before X** — Market (and defined on-chain) observation runs before treating social/X as an input.
2. **X is a timing accelerator, not discovery** — X may trigger **reassess** only; not primary token discovery.
3. **Knowable-at-T** — Features and decisions use only information available at decision time T; document as-of-T in every EXP.

## Data and packets

4. **Regime ID at ingest** — Every ingest path assigns a regime label; no untagged hot path.
5. **Capped hot packet** — Precompute → JEV-like packet with hard caps (size, fields, graph depth).
6. **Immutable decision packets** — Published decisions are append-only artifacts; fix forward with new DEC/packet.

## Risk and promotion

7. **Deterministic risk gate** — Same inputs → same gate outcome; no discretionary bypass in automation.
8. **Kill-attempt before promote** — Hypotheses and strategies must survive a documented kill test before promotion.

## Infra and tooling

9. **Cheap infra / free RPC until measured need** — Do not buy dedicated nodes, colocation, or premium social API until metrics justify (see API brief).
10. **Composer 2.5 / Cursor Grok preferred** for lab agents; **Fast mode OFF** unless imperative and logged.
11. **Soft ~$60/mo team LLM API budget** — Separate from RPC/indexing/social; Helm tracks burn.

## Agents

12. **Managers (Grok) are the only persistent agents** — Workers produce PRs/artifacts and leave.
13. **Memory first** — Reload `LAB_STATE.md` + latest `ARTIFACTS/SUMMARY.md` + active DECs each session.

## Phase 0 scope fence

- Observe-only and research unless a future DEC explicitly enables execution.
- **No database** in phase 0 (see DEC-002).
