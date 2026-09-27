---
cursor:
  subagentId: "bc-bcf25b45-d448-5435-8300-f5f61e11fedf"
---

# mig+15 daily booster

Draft PR: https://github.com/vaanai/MAL/pull/96 (`cursor/mig15-daily-train-fedf`, base `main`). Not merged.

The forward book `mig15_top20_tp50_sl30` loads `/var/lib/mal/paper/graduated-swing/out/mig15_model.txt` (PR #95). Training is `tools.graduated_swing --deploy`: mig+15 decisions only, label is tp50/sl30 pnl > 0, 0.05 SOL, 4h cap, LightGBM text with `SWING_FEATURES`. The file is staged and renamed. A fit that does not train leaves an existing booster in place.

`scripts/mal-core/laya-v0.sh` runs `scripts/mal-core/graduated-swing-train.sh` after the 04:15 LAYA fit (nice 19, idle IO). On the host the live copy is `/var/lib/mal/paper/laya-v0/laya-v0.sh`. That copy does not pass `--latency-report` (the snapshot `laya_v0.py` has no such flag), so the hook was inserted into the existing script rather than replacing it with the repo file. The trainer is `/var/lib/mal/eng/graduated-swing-train.sh` and uses the LAYA venv because `/var/lib/mal/paper/graduated-swing/py` is a wheel tree, not an interpreter. Source is `/var/lib/mal/paper/graduated-swing/src`.

One-shot `mal-mig15-train-now` finished at 2026-09-25T19:08:02Z. 442 mig+15 decisions, 390 labeled, 76 positives, tape end 2026-09-25T19:06:06Z. Booster is 39,056 bytes, 37 features, names match `SWING_FEATURES`. Sidecar `mig15_model.json` is not read by the service.

Forward paper (PID 62072, not restarted for the model) scored three clocks after the freeze: 0.025, 0.029, 0.659, all `skip` / `topk_warmup`. Causal top 20% waits for 5 scores. No `no_model` on this book. `mal-trade-tape`, `mal-attention`, `mal-pump-backfill`, `mal-funding-graph`, and `mal-laya-v0.timer` stayed active.

Nothing promotes. One UTC day. Gate unchanged.
