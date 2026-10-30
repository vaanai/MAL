# s1 registration record, item 12 (N3): shadow segments and the 2026-11-19 handover (`h5v1q50-boostfloor-s1`)

Written 2026-10-30T21:28:33Z (`date -u`, mal-research-0). Records only. This is the line that QP-RULING (report `ops/reports/2026-10-30/qp-s1-scripts.md`, ruling R-f) makes REQUIRED. It goes in s1's registration record, which consists of:
- the pre-clock notebook entry (REF of `scripts/ops/q50s1-register.sh --preclock-notebook-ref REF`), as its item 12, copied word for word;
- the records PR.

It is fixed here, before the registration and the clock (2026-11-06T00:00:00Z), and it is outcome-blind: no s1 outcome exists. No DEC item is needed, because DEC-031 Amendment 6 item 4 covers a restart at the same frozen sha (QP R-f).

Cites: DEC-031 Amendment 6 item 4 and Amendment 18 item 8; Amendment 22 items 8.1, 9, 10 and 11 (#1107); `EXP/EXP-024-note-s1-successor.md` (blob 7015abc9); PR #1111 (merged as main `f22f306eabce38c00a463b65a59d9a625103216e`); PR #1121 (second provider as a parameter, open). No book has cleared the promotion gate, and nothing here is a profit claim.

## The line (item 12)

> 12 shadow segments (DEC-031 Amendment 6 item 4; QP R-f). A MiScusi job lives at most 14 d, so the s1 shadow runs as segments.
> - **Segment 1** is `sh scripts/ops/q50s1-shadow-start.sh`, started in [2026-11-05T20:00Z, 22:30Z]. It stops the shadow itself at the **handover instant 2026-11-19T04:30:00Z** (SIGTERM through the scope, then exit 0).
>   - The instant is the constant `SEG1_END=1795062600` in `scripts/ops/q50s1-shadow-start.sh`. It was introduced at main `f22f306eabce38c00a463b65a59d9a625103216e` (#1111's merge).
>   - Every later main that a segment job runs at must carry the same value. The job's git_ref sha is recorded in the notebook at submit.
> - **Segment 2** is `sh scripts/ops/q50s1-shadow-start.sh --successor`, given the same `--ws2` as segment 1. It is submitted `after` segment 1, so it starts when segment 1 ends with exit 0.
>   - It writes `$ROOT/h5-q50s1-<UTC date>-succ`.
>   - It ends itself at min(2026-12-01T01:00:00Z, its start + 13 d 16 h), with exit 0.
> - Same `frozen_sha` 5c2ab7c6, same `bot_id`, no new clock, no new N.
> - **Restart gap.** The gap (last row of segment 1 to first row of segment 2) is excluded in whole UTC hours, rounded outward, as **decision-input** hours (Amendment 6 items 2.4 and 4).
>   - The handover makes at least one such hour: 2026-11-19T04Z.
>   - Attempts decided in those hours count toward Amendment 6's **10% cap** only.
> - **Positions open at the stop** are cut, so they are **incomplete**. They are section 1.4 exclusions (`process_restart`) and count in the **5% cap**.
> - The handover instant is fixed here before the clock and is **never moved after the clock**.
> - An early death of either segment is an ordinary Amendment 6 item 4 crash restart: the manager starts `--successor` by hand with the same `--ws2`.
> - The actual gap instants are notebooked after the handover.
> - **Feed.** The providers are this line's `endpoints`: mainnet-beta, plus the second provider given as `--ws2` (#1121; no default, key-free). The start refuses a registered line whose endpoints differ from its own feed (exit 8). A keyed provider's key is added inside the process only (`scripts/ops/q50s1-ws-launch.py`, QP on #1107).
> - **Root.** The s1 root is `/home/claude/s1home/data/arena`. Every s1 python that checks a root runs with `HOME=/home/claude/s1home` (QP R-a).
> - **Disclosed.**
>   - Loop 1 (Q50, `a3-guard-pin-1024` 0ec7fae8) and loop 2 (s1, tag `a3-guard-pin-s1`, commit 44b4c80b) call public RPC at the **same 07:10Z and 19:10Z minutes**.
>   - Loop 2's bounded upgrade-write enumeration spends Helius credits, reported daily.
>   - The read pack, owed before 2026-11-11T00Z, must rule how the pinned instant tool (#1109, 81cccea0), which counts trades decided in Amendment 6-excluded hours, applies those exclusions. The handover creates at least one such hour.
