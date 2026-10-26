# P59 RESULT: FLOOR-SLOW overlays A40 and A41 (exploration, not gate evidence)

Priced 2026-10-26 (jobs finished 23:13Z, 23:32Z, 23:51Z) after PREREG.md (commit ba578051). Nothing here says FLOOR makes money. No book has cleared the gate. Winner's curse applies to the base (best of 51 cells) and to both overlays (picked from many exits).

Control: base reproduces L3 REPRICE `l1` to the digit (n 332, flat 6.420621465162983%, pressure 5.141693995033923%; `output/control.txt`).

Full table: `output/SUMMARY.md`; raw: `output/*/results.json`. Discovery = explore-0814 (Aug, 14 days); confirmation = the three Sep blocks.

| variant | group | n | flat mean % | pressure mean % | delta flat / press (pp) | ex-top3 flat / press (SOL) | days+ |
|---|---|---|---|---|---|---|---|
| base | Aug | 307 | 6.4857 | 5.1757 | 0 | 4.0532 / 3.1950 | 14/14 |
| base | Sep | 25 | 5.6212 | 4.7240 | 0 | 0.0004 / 0.0015 | 4/5 |
| +A40 | Aug | 651 | -0.1572 | -0.2094 | -6.6429 / -5.3851 | -0.9749 / -0.9095 | 5/14 |
| +A40 | Sep | 135 | -2.1287 | -1.7964 | -7.7500 / -6.5203 | -0.8497 / -0.7163 | 0/7 |
| +A41 (= base) | | | | | 0 by construction | | |
| guard OFF (diagnostic) | Aug | 307 | 6.3857 | 5.0914 | -0.1000 / -0.0843 | | |
| guard OFF (diagnostic) | Sep | 25 | 5.6212 | 4.7238 | 0.0000 / -0.0001 | | |

## Verdicts (prereg lines)
- **A40: DROP.** Line 1 (delta >= +1.0 pp both models on confirmation) FAIL by about 8 pp in the wrong direction; line 2 (ex-top-3 > 0) FAIL; line 3 (ex-best-day > 0) FAIL; line 5 (discovery delta > 0) FAIL; line 4 (attempts/day) nominally PASS only because the cap shortens the cooldown (332 -> 786 attempts pooled; tp hits 35 -> 10). Mechanism: FLOOR's profit is the +50% take-profit on a 24 h hold; a 300 s cap cuts most of them off and sells at about the entry price minus costs.
- **A41: DROP, REDUNDANT.** The FLOOR pricer already applies `GUARD = 1.15` (`tools/arena_stack.py:27`), the same rule as the library's source (h4-boostmid RULE.md:26). Nothing to add. Guard-off ablation: the existing guard is worth +0.1000 pp flat / +0.0843 pp pressure on Aug, and 0 on Sep (the pooled flat figure is +0.0925 pp).
- **A40+A41:** same as A40.

## Notes the manager needs
- Line 4 as pre-registered is ill-posed on the kit count: the BASE itself shows 2.27 attempts/day on the Sep group (25 / 11 calendar days; 96 block-edge exclusions mostly in the short Sep blocks) and 21.93/day on Aug. The arena-tape P0 rate (4.6/day, #981) is a different measure. This does not change any verdict (lines 1, 2, 3, 5 fail).
- Confirmation is tiny (base n 25, ex-top-3 +0.0004 SOL): even a passing delta would be weak evidence. The base rule was tuned on these blocks, so "confirmation" is not out-of-sample for the base.
- Library evidence for A40/A41 is on a different population (g_reachable BOOST window); the transfer to FLOOR was an assumption and it failed.
- Conclusion for #981: register FLOOR-SLOW without A40 or A41. No rule-file change, so the byte-identical pin (blob cf4cf1c4) stands.

## Not measured
Any FLOOR G1 / forward / arena output, any October hour, other cap lengths or guard ratios (not tuned by design), worst-in-slot legs for the overlays.

## Corrections and disclosures (added after QP and reviewer CHANGES on #994)
- **Guard-off label.** The August guard-off figures are -0.1000 pp (flat) and -0.0843 pp (pressure) versus base. The 0.0925 pp figure is the POOLED flat difference (6.4206% vs 6.3281%), not an August figure.
- **Prereg deviation.** PREREG section 3 listed +A41 and +both as priced variants. They were not priced separately: A41 is already active in the base (`GUARD = 1.15`, `tools/arena_stack.py:27`), so +A41 equals base and +both equals +A40 by construction. Only base, +A40 and guard-off were run.
- **Trade-set disclosure.** The kit marks the base KILLED / not decidable in-sample: excluded block-edge share 22.4% (> 5%), 96 block-edge trades excluded. A40 excludes 0.63%. Every delta in this note therefore mixes the overlay's effect with a change in which trades were kept (n 332 vs 786 pooled). The direction of the A40 result is large enough that this does not change the DROP, but the deltas are not a clean paired effect.
- **Seal disclosure.** The 09-25..09-28 block (`oracle-live-2026-09-25_27`) overlaps about 36 of its 65 hours with the forward-paper stale-fill void. It is an exploration-pool block (HOLDOUT_LEDGER line 59), not a confirmation holdout. "Confirmation" in P59 means the Aug/Sep split only.
