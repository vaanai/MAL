---
cursor:
  subagentId: "bc-eb57b4f6-05b1-5a36-9135-d64d6e6c973c"
---

# Migrate direct, out of sample

Snapshot **2026-09-27T13:48Z**. Frozen cell, unchanged, from `migrate-direct-prereg.md` (2026-09-27T13:06:36Z). Both fail models. Promotion has not cleared.

The fast-box scorer rewrites `/var/lib/mal/paper/migrate-direct-oos-fast/report.json` after each newly sealed hour. This file is the pooled book: Oracle hour `2026-09-22T09` plus fast hour `2026-09-21T23`.

## Ranges

| Host | Covers | Stop | Now |
| --- | --- | --- | --- |
| Oracle | 2026-09-22T00:00Z through 2026-09-25T07:00Z | after hour 2026-09-22T00 | inside partial 2026-09-22T08 |
| mal-fast-0 | 2026-09-21T23 backward | +2,000,000 credits, 240 hours, 40 GiB, or disk free under 30% | 2026-09-21T23 sealed; inside 2026-09-21T22 |

No hour is on both lists.

## Credits

Backfill ledger when this run opened: **957,741 used of 4,000,000**, **3,042,259 remaining**. The fast run has its own counter, hard-capped at +2,000,000 (about 16k used when 2026-09-21T23 sealed). Same Helius key as the listeners. The billing-cycle admin API needs a project id; none is stored on the host, so this is the backfill cap, not the invoice.

## Seal rate

Fast box, after the worker restart: about **11.7 slots/s**. A full hour is about 13,500 slots, so about **3.1 history hours per wall hour**. Four fast days (2026-09-21 through 2026-09-18) are about 31 wall hours from 13:26Z, near **2026-09-28 20:00Z**. Oracle’s leftover 22 Sep hours finish sooner at its 50% cap. Disk on the fast box was 98% free. Listeners `mal-fast-create`, `mal-fast-pre-create`, and `mal-fast-public-logs` stayed active and their row counts kept rising.

## Pooled book

| Size | n | Days | Days + | Fill | Flat net | Flat CI lo | Flat ex-top-3 | Pressure net | Pressure CI lo | Pressure ex-top-3 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.5 SOL | 60 | 2 | 1 | 16.7% | +0.51% | −3.22% | −0.606 SOL | +0.56% | −1.62% | −0.333 SOL |
| 0.05 SOL | 58 | 2 | 1 | 22.4% | −1.89% | −5.88% | −0.128 SOL | −1.68% | −4.11% | −0.097 SOL |

Days positive is 1 of 2 under both models. Promotion does not clear.

## By sealed hour

| Hour | Host | 0.5 n | 0.5 days + | 0.5 fill | 0.5 flat net | 0.5 flat CI lo | 0.5 flat ex-top-3 | 0.5 pressure net | 0.5 pressure CI lo | 0.5 pressure ex-top-3 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2026-09-22T09 | Oracle | 33 | 0 | 9.1% | −2.03% | −6.33% | −0.523 SOL | −0.73% | −2.56% | −0.218 SOL |
| 2026-09-21T23 | fast | 27 | 1 | 25.9% | +3.61% | −3.10% | −0.270 SOL | +2.13% | −2.13% | −0.212 SOL |

0.05 SOL on 2026-09-22T09: n=32, days +=0, fill 18.8%, flat −5.59% (CI lo −10.62%, ex-top-3 −0.107 SOL), pressure −4.03% (CI lo −6.73%, ex-top-3 −0.073 SOL).

0.05 SOL on 2026-09-21T23: n=26, days +=1, fill 26.9%, flat +2.67% (CI lo −4.06%, ex-top-3 −0.039 SOL), pressure +1.20% (CI lo −3.02%, ex-top-3 −0.032 SOL).
