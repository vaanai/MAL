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
9. **Full detect book** — Outcomes on rejects and runners alike; evaluate labels never delete history ([DEC-007](DEC/DEC-007-full-detect-book-anti-selection-bias.md)).

## Infra and tooling

10. **Cheap infra / free RPC until measured need** — Do not buy dedicated nodes, colocation, or premium social API until metrics justify (see API brief).
11. **Composer 2.5 / Cursor Grok preferred** for lab agents; **Fast mode OFF** unless imperative and logged.
12. **Soft ~$60/mo team LLM API budget** — Separate from RPC/indexing/social; Helm tracks burn.

## Agents

13. **Managers (Grok) are the only persistent agents** — Workers produce PRs/artifacts and leave.
14. **Memory first** — Reload `LAB_STATE.md` + latest `ARTIFACTS/SUMMARY.md` + active DECs each session.

## Phase 0 scope fence

- Observe-only and research unless a future DEC explicitly enables execution.
- GitHub remains SoT for DEC/EXP/LAB_STATE. Sealed JSONL remains the provenance/EXP spine. On-box Postgres is the ops/state layer, not a provenance replacement ([DEC-009](DEC/DEC-009-oracle-always-free-phase0-host.md), [DEC-010](DEC/DEC-010-oracle-phase0-handoff-autonomy.md)); no managed/Autonomous DB in phase 0.
- Escalate to the owner before spend, Always Free exit, security-boundary weakening, public Postgres, trading/X credentials on host, or capital access. Human PC is not a permanent Cursor→Oracle hop. Access design is [DEC-011](DEC/DEC-011-cursor-oracle-access-cf-tunnel.md) (**pending owner implement**).
