---
cursor:
  subagentId: "bc-06d9d0a4-9355-5b34-8299-1200d3d54ef8"
---

# Honest rescore — median / p10 / p90 / SOL after fees

**As-of:** 2026-09-25T07:00Z. Oracle `mal-core-vnic` via DEC-011 (fingerprint `SHA256:HH+tRTOIpyvYorf+FhZ7INifqOm2L7NTcvPLdqMPVTo` matched). Paper-only. No rule retune. No Tunnel/Access changes. Postgres not opened.

**User-facing EXP file:** `EXP/EXP-002c-addendum-honest-return-distributions.md` (local commit `85e2dac` on `cursor/honest-rescore-addendum-4ef8`). **Host:** `/var/lib/mal/paper/_honest-rescore-2026-09-{20,21,24}_*` plus `marks-2026-09-24.jsonl`.

**PR:** https://github.com/vaanai/MAL/pull/74 (draft). Push retried 07:18Z after token refresh; branch is on origin. `LAB_STATE.md` not edited.

Fee stacks on a **0.1 SOL** entry:

| Label | bps | What |
| --- | ---: | --- |
| 125 | 125 | EXP-006 `pump_assumed_bps_v0` (lab constant) |
| 350 | 350 | Planning round-trip: curve 1.25% + PumpPortal Local 0.5% **per side**, before impact / priority / ATA rent (`../../docs/research/execution-stack-options.md`) |

**Keep/kill uses 350 bps**, median **and** mean SOL vs **spine priced** @60s, **both** courier days.

## SSH

Smoke: hostname `mal-core-vnic`, user `ubuntu`, `aarch64`. Read existing 09-20/21 observe+marks; wrote new paper artifacts only.

## Survivorship

RPC marks files: **98** unique parents (09-20), **116** (09-21). Full-book 60s priced_n **338 / 646** because later `ingest_hot` ticks on the same mint (migrate/duplicates) join as marks, and this spine includes bonk (EXP-004 graph pop 13029/15487 after parking 1766/2614). Combined v2 runner priced_n **96** matches parent EXP-002c.

Unpriced on the full book ≈ never sampled. Unpriced inside attempted mints = likely dead; host field `attempted_unpriced_as_dead` uses **−100%**.

## 60s priced-only (primary)

See EXP addendum for the full markdown tables (1s–60s on host). Headline @60s:

**09-20** — spine median ~0, mean 2888%, mean SOL@350bps **2.88** (p10 −99%, p90 16941%). v2_runner median **0.24%**, SOL@350 **−0.0023**. v1_runner median **3.36%**, SOL@350 **0.0018**. L3 median **0.93%**, SOL **1.95**. L3_minus_v2 median **6.91%**, SOL **3.06** (p90 15089% — one name).

**09-21** — spine median ~0, mean SOL@350 **2.89**. v2_runner median ~0, SOL **−0.0038**. v1_runner median **1.16%**, SOL **−0.0025**. L3 median **1.91%**, SOL **1.19**. L3_minus_v2 median **6.94%**, SOL **1.95**.

## Verdict

| filter | 20 | 21 | Cross-day |
| --- | --- | --- | --- |
| v1_runner | KILL | KILL | **KILL** |
| v2_runner | KILL | KILL | **KILL** |
| v2_reject | KILL | KILL | **KILL** |
| L3 | KILL | KILL | **KILL** |
| L3_minus_v2 | letter KEEP (outlier SOL) | KILL | **KILL** |

Attempted-unpriced-as-dead @350bps: L3 SOL **−0.008** both days; L3_minus_v2 **−0.005 / −0.009**. Filters that look “positive median” still lose SOL on the marked subsample once dead names count.

Arithmetic-mean FAIL in EXP-002c stands; magnitude was never an SOL expectancy.

## 2026-09-24 public-RPC backfill

`observe-2026-09-24.jsonl` — **32993** bonding creates. Existing `tools.exp003_rpc_backfill` seed 1 sample 300 window 60s → `/var/lib/mal/paper/marks-2026-09-24.jsonl`. Then `tools.exp003_marks` + the same honest table.

| Probe | Rate |
| --- | --- |
| min_interval 0.40 s | **Sustained 429.** 154 429s / ~6 min on create 1; **0 creates/min**. |
| min_interval 2.5 s, skip marked parents | **0 429s.** 14/300 walked in 913 s (**0.92 creates/min**). **361** ticks, **5** unique parents. |

EXP-003 coverage (RPC marks only): 60s **ok=5** → **INCOMPLETE**. Filter priced_n on that subsample is 1–2. Host ingest-join still prices 352 creates @60s from later observe ticks — not a third marks day. Loop proved; no paid RPC.

## Cleanup

Agent tempfile key and cloudflared Access TCP torn down after the session. Host `cloudflared.service` / Access policy not touched.
