# DEC-035: Live-learning lane (real SOL at micro size, before the gate)

| Field | Value |
| --- | --- |
| **Status** | IN FORCE from merge. Written 2026-11-02 (`date -u` 20:5xZ), manager19. |
| **Decider** | Vaan (owner). Approval in chat, 2026-11-02 ~20:45Z: "You have the yes on it, and you can communicate with helm, my grokbot agent, for whatever you need." MiScusi notebook n_H7IIZL1Qke4dVA. |
| **Amends** | CLAUDE.md "Hard rules" (paper only) and "Promotion gate", for this lane only. DEC-032's owner-ask step is not needed for this lane: the owner's approval above covers every book run under this DEC, within the limits in §2. |
| **Does not amend** | The promotion gate. Any registered read, seal or ledger entry of a running book. Hosts, firewall, SSH, Cloudflare. |

## 1. Why
For six weeks every candidate stayed on paper, and most reads ended NOT_DECIDABLE for operational reasons (feed gaps, A3 halts, lost crons, failed smokes; lane L4 audit, `ops/reports/2026-11-02/L4-nd-audit.md`: 4 market verdicts in 30 events). Simulator fidelity has been the open question on every line (PumpSwap virtual reserve, exit lag, latency). Small real trades measure fills, latency, fees and P&L directly. This lane is a **measurement budget**, not a promotion.

## 2. Limits (hard; in code or unit config, not in prompts)
1. **Total budget:** 0.30 SOL of realized + marked loss across all books in this lane. At -0.30 SOL every executor in the lane stops and stays stopped until the owner adds budget.
2. **Stake:** 0.02–0.03 SOL per position.
3. **Open positions:** at most 3 across the lane.
4. **Daily loss stop:** 0.10 SOL realized + marked (a stale mark counts at zero) per UTC day across the lane; trading resumes at the next 00:00Z.
5. **Keys:** the wallet key is placed by Helm, root-only, at the path the executor's unit binds. No Claude session reads, prints or copies it. One wallet for the lane, funded by the owner (up to 1 SOL in total; budget above is the loss limit, not the balance).
6. **Kill switch:** a STOP file in the executor's state dir stops new entries at once; any session may write it.

## 3. Which books
Any frozen rule with a live executor path may run in this lane, at the manager's choice, without a gate read: H5-family (h5_executor / h5_arena_executor), arena books via arena_live_bridge, and the slow-bot line once built. Each book gets its own `bot_id`, frozen sha and log of every send. A code or prompt change is a new `bot_id` (logged, not re-approved).

## 4. Program redeploys
A pump / PumpSwap redeploy (A3 `pins_changed`) does **not** end this lane. On a flag the executor pauses new entries, the manager (or a scheduled job) re-checks the decode on post-redeploy transactions, re-pins, and resumes the same day. Other A3 flags (synthetic share, feed health) pause entries until the check is clean.

## 5. Reporting
Each UTC day: trades, realized SOL after all fees, live-vs-sim gap (landing delay, fill slippage, exit timing) per book, to the MiScusi notebook and `data/console.json`. Figures are real money and are reported as they are, including losses.

## 6. Scaling
Above 0.03 SOL per position or above the 0.30 SOL loss budget: only through the promotion gate (CLAUDE.md), on real-fill trades or a registered read, plus the owner's word. Live-learning trades may count toward the gate's trade and day counts only if the book's rule was frozen before the first counted trade and every trade is reported.

## 7. Interaction with seals
Live outcomes of a book in this lane are the lane's own data and are not sealed. Where a book's pools overlap a running registered read (e.g. EXP-024 Look-2 to 2026-11-14), that read records the live lane as a declared observation; the live lane is not stopped for it.
