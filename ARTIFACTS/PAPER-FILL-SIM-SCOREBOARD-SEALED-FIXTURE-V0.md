# Paper fill-sim scoreboard on sealed-day fixtures — Proposed paper registration

| | |
| --- | --- |
| **ID** | `paper-fill-sim-scoreboard-sealed-fixture-v0` |
| **Status** | **Proposed** paper registration (2026-09-24). **Soft GATE PASS Formal-stamped** (Lyra; kill `bc-1ba845c2-c2a7-5846-8768-169b2c403c72`; implement `bc-24029f87-19f0-5297-9b6e-56f6074d7a6f`; [PR #58](https://github.com/vaanai/MAL/pull/58) tip `15cdd67`; squash-merge on `main` pending). Not a measure. Not run. |
| **Owner seat** | Proof (scoreboard + Soft GATE); Scout (fill-sim bind spine stays on embedded stamps); Helm (AUTH) |
| **Commission** | Consumer of merged paper-fill-sim evaluate ([PR #57](https://github.com/vaanai/MAL/pull/57) squash `6217773`) and paper scoreboard sealed-fixture v0 ([PR #51](https://github.com/vaanai/MAL/pull/51)). Parent #49–#57 CLIs are **not** rewritten. |
| **Schema** | [paper-fill-sim-scoreboard-sealed-fixture-v0.schema.json](paper-fill-sim-scoreboard-sealed-fixture-v0.schema.json) (JSON Schema 2020-12) |
| **Fixtures** | [fixtures/paper_fill_sim_scoreboard_sealed_fixture_v0/](../fixtures/paper_fill_sim_scoreboard_sealed_fixture_v0/) — synthetic, not host extracts |
| **CLI** | `python -m tools.paper_fill_sim_scoreboard_sealed_fixture_v0` — validate / example / score on local JSON only |
| **Soft GATE** | **PASS** — Formal stamp Lyra (kill `bc-1ba845c2-c2a7-5846-8768-169b2c403c72`; implement `bc-24029f87-19f0-5297-9b6e-56f6074d7a6f`; [PR #58](https://github.com/vaanai/MAL/pull/58) tip `15cdd67`). Watches in [Soft watches](#soft-watches-non-blocking) stay **non-blocking** (`blocking=false`). |

This file registers how a local set of validated [`paper_fill_sim_hot_packet_evaluate_v0`](PAPER-FILL-SIM-HOT-PACKET-EVALUATE-V0.md) stamps is counted against a **checked-in sealed-day expectation**. Same honesty class as [PAPER-SCOREBOARD-SEALED-FIXTURE-V0.md](PAPER-SCOREBOARD-SEALED-FIXTURE-V0.md), but the stamp input is the fill-sim bind from #57, not raw paper-evaluate.

Merge ≠ Oracle measure ≠ EXP-006 promote ≠ Discovery promote ≠ continuous observe-wiring ≠ production enum lock ≠ Graph revive ≠ live trading.

---

## What this scoreboard is

| Piece | v0 fact |
| --- | --- |
| Stamp input | One or more local JSON objects that validate as `paper_fill_sim_hot_packet_evaluate_v0`, or a directory of those files under `fixtures/paper_fill_sim_hot_packet_evaluate_v0/` |
| Fixture side | One checked-in sealed-day expectation. Synthetic only. `mint=UNK` is that reject row’s identity, not a wildcard |
| Read path | Local JSON only. `rpc=false`. `observe_jsonl_tail=false`. `host_extract_required=false`. `marks_joined=false`. No `/var/lib/mal` |
| Output | Label counts, fill-sim status counts, reason histogram, graph status (not scored), null horizons, null `Δ_exec`, join counts |
| Population | `n` is the local set. `local_set_is_not_the_sealed_book=true`. Day `2026-09-20` on these fixtures is the string on the embedded parent packets |

Rates are **stamp counts**. `share` is `{numerator, denominator}` of this local set. `share_kind=count_fraction_not_a_return`. A zero numerator is a count of zero stamps. It is not a 0% return.

---

## DEC-007 both arms

Every scoreboard sets `full_book.policy=dec007_both_arms_retained`, `reject_stamps_dropped=0`, and `label_rates.rows` with `runner` then `reject` always present.

`fill_sim_status_counts` lists `documented_model_only` then `reject_arm_no_pretend_buy`. The reject fill-sim row stays on the table even when `n=0`.

`score` counts every valid fill-sim stamp it was given. It has no switch that drops `evaluate_label=reject`. Join disagreement does not relabel or delete stamps.

---

## Fill-sim status and evaluate labels

| `evaluate_label` | Expected `fill_sim.status` on valid stamps |
| --- | --- |
| `runner` | `documented_model_only` |
| `reject` | `reject_arm_no_pretend_buy` |

The sealed-day expectation row carries `fill_sim_status` for join. `fill_sim_disagree_n` counts expectation mismatches on that field. Numeric fill outcomes on embedded stamps stay null.

---

## Horizons and `Δ_exec`

Null with explicit status. Null is not 0% and not zero cost.

| Object | Status | Value |
| --- | --- | --- |
| Each embedded fill-sim stamp | `fill_sim_registration_null_explicit` | horizon `values` all `null`; `delta_exec.value=null` |
| Scoreboard `horizons` | `fixture_joined_null_explicit` | `values` all `null`; `null_is_not_zero_return=true` |
| Scoreboard `delta_exec` | `fixture_joined_null_explicit` | `value=null`, `reason=fill_sim_sealed_day_fixture_has_no_scored_fill`, `null_is_not_zero_cost=true` |

---

## Sealed book stays incomplete

| Field | Value |
| --- | --- |
| `dual_read.sealed_book_rpc_slice` | `incomplete` |
| `dual_read.closed_book_claim` | `false` |
| `dual_read.incomplete_reason` | `fill_sim_fixture_join_does_not_close_the_sealed_book` |
| `fixture_join.closed_book` | `false` |
| `expectation.sealed_book_rpc_slice` | `incomplete` |

`graph_lift` is null on every board. Graph policy on embedded stamps stays **cold**.

---

## Honesty

| Claim | v0 fact |
| --- | --- |
| Merge of this registration | Docs + schema + synthetic fixtures + fixture CLI. **Does not** wire fill-sim into observe |
| Input | Validated `paper_fill_sim_hot_packet_evaluate_v0` stamps plus one synthetic sealed-day expectation |
| Parent CLIs | **Not** rewritten (#49–#57 evaluate / fill-sim bind / scoreboard / batch / host tools) |
| EXP-006 | **Not** promoted. `honesty.exp006_promoted=false`. Harness **not** rewritten |
| Marks | **Not joined** (`input.marks_joined=false`, `honesty.marks_joined_on_this_stamp=false`) |
| Encoder | **Not** promoted. `observe/client.py` is untouched |
| Enum production lock | **No.** `global_95bps` and `launchlab_init` stay **Proposed** on embedded packets |
| Discovery promote | **No.** Graph cold; no revive, no densify |
| EXP-002c | **Not** retuned |
| EV / alpha | **No.** `measure.kind=none` |
| Measure exit | **None.** `measure.pass_fail_no_lift=false`. Exit `0` when a scoreboard prints; exit `1` on invalid input. No `PASS` / `FAIL_NO_LIFT` process exit |
| Host | No `/var/lib/mal` read from `score`. No SSH |

**Soft GATE PASS** (Formal stamp Lyra). Soft GATE PASS ≠ Discovery promote ≠ continuous observe-wiring ≠ production enum lock ≠ densify ≠ EXP-002c retune ≠ Graph revive ≠ X keys on host ≠ live trading ≠ EXP-006 promote.

---

## Soft watches (non-blocking)

**Soft GATE PASS** (Formal stamp Lyra). `soft_watches.blocking=false`. Inherited watches from #49–#57 and fill-sim #57 remain listed and non-blocking.

Named on this registration:

| Watch id | What it is | Why it does not block |
| --- | --- | --- |
| `label_share_is_not_a_return` | Runner/reject shares are count fractions | Not EV, lift, or return |
| `fill_sim_status_share_is_not_a_return` | Fill-sim status shares are count fractions | Documented model ≠ scored fill |
| `synthetic_sealed_day_not_a_host_extract` | Day `2026-09-20` aligns with calendar usage elsewhere; files are synthetic | Not an Oracle extract |
| `local_subset_is_not_dropped_history` | Unused expectation rows are not a deleted arm | `reject_stamps_dropped` stays `0` |
| `fill_sim_vocabulary_not_exp006_promote` | Constants cite EXP-006 vocabulary | Merge ≠ Oracle re-run |
| `documented_constants_not_a_scored_fill` | `fill_model` on embedded stamps; outcomes null | Not a fill score |

---

## CLI

From the repo root:

```bash
python -m tools.paper_fill_sim_scoreboard_sealed_fixture_v0 example --which all-runner
python -m tools.paper_fill_sim_scoreboard_sealed_fixture_v0 example --which mixed
python -m tools.paper_fill_sim_scoreboard_sealed_fixture_v0 example --which identity-reject
python -m tools.paper_fill_sim_scoreboard_sealed_fixture_v0 example --which graph-cold
python -m tools.paper_fill_sim_scoreboard_sealed_fixture_v0 example --which slots-not-scored
python -m tools.paper_fill_sim_scoreboard_sealed_fixture_v0 validate \
  fixtures/paper_fill_sim_scoreboard_sealed_fixture_v0/mixed_runner_reject.json
python -m tools.paper_fill_sim_scoreboard_sealed_fixture_v0 score \
  --expectation fixtures/paper_fill_sim_scoreboard_sealed_fixture_v0/sealed_day_2026-09-20_expectation.json \
  fixtures/paper_fill_sim_hot_packet_evaluate_v0/sealed_cold_runner.json \
  fixtures/paper_fill_sim_hot_packet_evaluate_v0/reject_missing_identity.json
```

`score` stamp paths must live under `fixtures/paper_fill_sim_hot_packet_evaluate_v0/`. Paths under `/var/lib/mal` exit `1` without opening.

Proof:

```bash
python3 -m unittest tools.test_paper_fill_sim_scoreboard_sealed_fixture_v0
```

| Fixture | What it shows |
| --- | --- |
| `sealed_day_2026-09-20_expectation.json` | Synthetic checklist for the five fill-sim identities |
| `all_runner.json` | Four runners; reject row `n=0`; one expectation row absent |
| `mixed_runner_reject.json` | Both arms; `missing_mint` once; fill-sim status mix |
| `identity_reject.json` | Reject arm; `reject_arm_no_pretend_buy` |
| `graph_cold.json` | Graph cold aggregate |
| `slots_not_scored.json` | Allowlisted slot; `graph_lift` null |

---

## Non-goals

| Cap | Held |
| --- | --- |
| Rewrite #49–#57 parent CLIs or EXP-006 harness | Out |
| `observe/client.py` | Untouched |
| Host paths, SSH, Oracle re-run, marks join as scored measure | Out |
| `PASS` / `FAIL_NO_LIFT` measure exit | Out |
| Closing `sealed_book_rpc_slice` | Out. Stays `incomplete` |
| Soft GATE PASS at merge | **PASS** (Formal stamp Lyra; kill `bc-1ba845c2-c2a7-5846-8768-169b2c403c72`) — ≠ Discovery / wiring / enum / densify / EXP-002c / Graph / X / live / EXP-006 |

---

## Cross-links

- Stamps being counted: [PAPER-FILL-SIM-HOT-PACKET-EVALUATE-V0.md](PAPER-FILL-SIM-HOT-PACKET-EVALUATE-V0.md)
- Evaluate scoreboard pattern: [PAPER-SCOREBOARD-SEALED-FIXTURE-V0.md](PAPER-SCOREBOARD-SEALED-FIXTURE-V0.md)
- Pipeline: [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md), [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md)
