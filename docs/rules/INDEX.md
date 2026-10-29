NON-BINDING INDEX of the binding text. On any conflict, the cited DEC/EXP text on main controls. Built 2026-10-29 at main 5884cfd6. Any edit to this file is a T1 PR (DEC-013 Am.1).

# Rules library index

| Book | Rule file | Status | Full sources |
| --- | --- | --- | --- |
| `h5v1q50-boostfloor-a1` (Q50) | [q50-a1.md](q50-a1.md) | Paper. Clock 2026-10-25T00:00:00Z, end-seal 2026-11-06T00:00Z. Read not run at build. C0 path text (DEC-033) is a draft copy NOT IN FORCE; the in-band block binds C0 through the owner's condition and the xorder pre-ruling (see Rulings in the file). | DEC/DEC-031-forward-paper-arena.md (Am.4, 5, 6, 10, 11, 13, 14); EXP/EXP-024-h5-boostfloor-part1-prereg.md (Am.10, Note under Am.10); EXP/EXP-024-RULE-Q50.md; DEC/DEC-032-arena-canary-template.md; DEC/DEC-033-h5v1q50-canary.md |
| DBC m10, forward v1 (confirmation read; P18 B' on PASS) | [dbc-m10.md](dbc-m10.md) | Not an arena book; not a promotion-gate read. Window [2026-10-25T00Z, 2026-11-01T00Z), read refuses before 2026-11-01T00:10Z. ONE read via job #1804 (Option A), not run at build. On PASS only: P18 B' (DEC-031 Am.20), clock 2026-11-04T00Z, expiry 2026-11-06T00Z. 12 open questions for QP are listed in the file. | docs/research/dbc-m10-forward/ (REGISTRATION.md incl. addendum 2c, AMENDMENT-1.md, READ-HARDENING.md, PINS.txt); DEC/DEC-031-forward-paper-arena.md (Am.20); ARTIFACTS/lab/qp-ruling-1026-p33-p18.md; docs/HOLDOUT_LEDGER.md |

Not yet in the library (pointer only, no rule file): `h5v1q50-boostfloor-s1` (DEC-031 Am.17, Am.18; EXP/EXP-024-note-s1-successor.md; DEC/DEC-034-h5v1q50-s1-canary.md), `h5v1q60-boostfloor-a1` (DEC-031 Am.19; EXP/EXP-024-RULE-Q60.md).

**The checker proves verbatim text only, not that a clause is in force or complete.** Check every rule file: `python3 scripts/ops/rules-quote-check.py --rev origin/main` (exit 1 on any quote that is not verbatim at its cited line).
