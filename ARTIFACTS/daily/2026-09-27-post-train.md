---
cursor:
  subagentId: "bc-82b39439-3c23-5422-9768-f9577dc0ae91"
---

# Post-train check — 2026-09-27 ~05:52 UTC

**Host:** `mal-core-vnic`. Read-only + conditional backfill start (DEC-011).

## mal-laya-v0

**Still running** (`activating` / `start` since 04:33 UTC). Journal progress ~36M tape lines scanned (05:50 UTC). **No finish result yet** for tonight’s train; prior unit metadata showed `Result=success` from an earlier cycle only.

## mal-forward-paper

`runner-status.json` **`lag_ms` 246** (`stale_cap_ms` 5000) — **under cap** (was ~5656 at 05:05).

## mal-attention-daily

**Still in `start-pre`** since 04:45 UTC (waiting on `ExecStartPre` for `mal-laya-v0` to leave active/activating). **`MemorySwapMax=0`** on the loaded unit (drop-in). **Main job has not started** — not a clean start yet.

## Swap

**~4.0 GiB / 4.0 GiB used** (`/swapfile`).

## mal-pump-backfill

**Inactive.** LAYA has **not** exited → **did not start** backfill (per policy).
