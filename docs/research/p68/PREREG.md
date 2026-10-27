# P68 PREREG: LLM rule writer, offline (written BEFORE any held-out row was loaded)

Lane P68, manager17. Library row p67-llm-rule-writer-offline. Paper only. No book has cleared the promotion gate; nothing here says a book makes money.

- Family (parent): the H5 frozen binding trades (`/data/mal/hunt-1008/h5-lossfilter/frozen_binding_trades.parquet`, D=40, leg b, H=end, stake 0.1 SOL). Base = all trades of the block.
- Discovery: `explore-0814` (Aug 14-28, exploration pool, `frozen_disc`, n=1702). The writer saw only `discovery_aggregates.txt` (quintile tables of trig_t, rem, q_trig, sps, UTC hour vs mean net pp under the flat and pressure models). Script `p68_aggregates.py` filters to explore-0814 before computing anything.
- Held-out: `exp011-0909` (Sep 9-15, moved to the exploration pool 2026-10-06 per HOLDOUT_LEDGER). Not loaded until this file and rules.json were committed. The writer saw no held-out count or outcome.
- Rules: exactly N=5 in `rules.json`, written in one shot by Claude as writer. Each is an AND of interval bounds on at most 2 distinct causal features (so <= 3 conditions counting each feature once). No edits after commit; failing rules stay reported.
- Unit: pp of stake = `flat` or `press` field / 1e6 (stake 0.1 SOL). Two fail models: flat 15% (`flat`), pressure-fail slope 1 (`press`).
- KEEP a rule only if ALL hold: (a) held-out mean(rule) - mean(base) >= +1.0 pp under BOTH models; (b) same >= +1.0 pp after dropping the rule's top 3 trades (base left whole, conservative); (c) same after dropping the rule's best UTC day; (d) Holm over the 5 rules at alpha 0.10 on the worse of the two models' bootstrap p; (e) n(rule) >= 30 (added so a thin cell cannot pass on luck).
- Bootstrap: 10,000 paired row resamples of the held-out block, seed 1; p = share of draws with diff <= 0.
- Caveats in advance: rows cluster by day and pool, so p is optimistic; this is one exploration block, never a promotion; a KEEP is only a hypothesis for a later pre-registered test on an unowned block.
- Seals: no HV4, Q50, October, meta-capture, DBC, FLOOR, LaunchLab or D1 data touched.
