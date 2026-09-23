# Oracle sealed-day paper batch, incomplete RPC — Proposed paper registration

| | |
| --- | --- |
| **ID** | `paper-batch-oracle-sealed-day-incomplete-rpc-v0` |
| **Status** | **Proposed** paper registration (2026-09-23). Not a measure. Not a host run. |
| **Owner seat** | Proof (batch + Soft GATE); Scout (packet spine stays on the projection); Helm (RE-AUTH) |
| **Commission** | Helm RE-AUTH 2026-09-23. Soft GATE required before merge. Consumer of merged paper-scoreboard (`aa31768`, PR #51), paper-evaluate (`224166c`, PR #50), and hot-packet v0 (`274faa2`, PR #49). |
| **Schema** | [paper-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json](paper-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json) (JSON Schema 2020-12) |
| **Fixtures** | [fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/](../fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/) — synthetic, not host extracts |
| **CLI** | `python -m tools.paper_batch_oracle_sealed_day_incomplete_rpc_v0` — example / validate / batch on local JSON only |
| **Oracle shape** | [paper_batch_oracle_run.md](../tools/paper_batch_oracle_run.md) — paths and flags only. No executed host result |
| **Soft GATE** | **Required** before merge. Watches in [Soft watches](#soft-watches-non-blocking) stay **non-blocking** (`blocking=false`). |

This file is the object **LAYA / DEC-006** cite for a day-aligned paper batch: sealed observe JSONL → [`hot_packet_v0`](HOT-PACKET-V0.md) → [`paper_evaluate_hot_packet_v0`](PAPER-EVALUATE-HOT-PACKET-V0.md) → [`paper_scoreboard_sealed_fixture_v0`](PAPER-SCOREBOARD-SEALED-FIXTURE-V0.md). It is an artifact registration, same class as those three.

It is **not** EXP-009. It is **not** a sealed Oracle measure. It is **not** an EXP-002c retune. An EXP needs a hypothesis, windows, and a kill-attempt ([EXP/README.md](../EXP/README.md)). This batch has none of those. Merge does not score a sealed book and does not claim alpha.

Cloud agents cannot read host Oracle JSONL. The checked-in files are synthetic. Day strings `2026-09-20` and `2026-09-21` are calendar labels already used by sealed observe. They are not a read of `observe-2026-09-20.jsonl` or `observe-2026-09-21.jsonl` on `mal-core-vnic`.

---

## What this batch is

[DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md) is detect → decode → evaluate → runners. [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md) keeps both arms. v0 names the composed local run `type=paper_batch` / `schema_version=paper_batch_oracle_sealed_day_incomplete_rpc_v0` / `id=paper-batch-oracle-sealed-day-incomplete-rpc-v0`.

| Stage | This registration |
| --- | --- |
| Detect | Local `observe-YYYY-MM-DD.jsonl` lines. Checked-in lines are synthetic `ingest_hot` creates plus one skipped non-create per day |
| Decode | `tools.hot_packet_v0.validate_packet` on a sealed-overlay projection. Graph slots stay null |
| Evaluate | `tools.paper_evaluate_hot_packet_v0.stamp_from_packet`. Rules are not reimplemented here |
| Runners / book | `tools.paper_scoreboard_sealed_fixture_v0.score_stamps`, one scoreboard per day |

| Piece | v0 fact |
| --- | --- |
| Read path | Local files only. `rpc=false`. `observe_jsonl_tail=false`. `host_extract_required=false` |
| Overlay | `sealed`. `enrich_type=null`. A row whose `regime_id` is not the sealed observe default is refused. This batch does not open a `regime_enrich` file and does not write `global_95bps` or `launchlab_init` |
| RPC slice | `dual_read.sealed_book_rpc_slice=incomplete` on the batch, on every embedded scoreboard, and on every embedded packet |
| Closed book | `dual_read.closed_book_claim=false` on the batch and on every scoreboard. `fixture_join.closed_book=false` |
| Population | `local_set_is_not_the_sealed_book=true`. `n` is a stamp count |

The checked-in example is the pair `2026-09-20` / `2026-09-21`. `batch` accepts any `observe-YYYY-MM-DD.jsonl` whose `t_ws` UTC day matches the basename. A row from another UTC day fails the batch. There is no cross-day mark join.

---

## Incomplete RPC

EXP-007 through EXP-007e left the sealed full book **INCOMPLETE**. Enriched PASS stays a sample overlay on a child `regime_enrich` row. This stamp does not promote that overlay onto the sealed create.

| Field | Value |
| --- | --- |
| `dual_read.sealed_book_rpc_slice` | `incomplete` |
| `dual_read.closed_book_claim` | `false` |
| `dual_read.incomplete_reason` | `batch_projection_does_not_close_the_sealed_book` |
| Each scoreboard `dual_read.sealed_book_rpc_slice` | `incomplete` |
| Each scoreboard `dual_read.closed_book_claim` | `false` |
| Each scoreboard `fixture_join.closed_book` | `false` |
| Each packet `dual_read.overlay` | `sealed` |
| Each packet `dual_read.sealed_book_rpc_slice` | `incomplete` |
| `honesty.executed_host_sealed_book` | `false` |
| `honesty.scored_oracle_measure` | `false` |

`--fixture-origin sealed_row_projection` is the operator-local flag documented in [paper_batch_oracle_run.md](../tools/paper_batch_oracle_run.md). It sets `input.host_jsonl_read=true` for that process. It still cannot set `closed_book_claim`, `executed_host_sealed_book`, or `measure.kind`. The checked-in example uses `fixture_origin=synthetic` and `host_jsonl_read=false`. No host stdout is in git.

---

## DEC-007 both arms

Every batch sets:

| Field | Value |
| --- | --- |
| `full_book.policy` | `dec007_both_arms_retained` |
| `full_book.arm_retained` | `true` |
| `full_book.deletes_detect_history` | `false` |
| `full_book.reject_stamps_dropped` | `0` |
| Each scoreboard `label_rates.rows` | `runner` then `reject`, both rows always present |

`score_stamps` counts every stamp it was given. This CLI has no switch that drops `evaluate_label=reject`.

Non-create lines are a census, not a deleted arm. `row_census.skip_is_not_a_dropped_evaluate_arm=true`. Reason `not_create_row` covers `outcome_mark` and non-create `ingest_hot` (the day-21 migration line). Those lines stay in `input.days[].source_rows` and do not become horizons. A bonding create that cannot project (wrong UTC day, non-default regime, invalid packet) fails the batch. The CLI does not emit a scoreboard that omitted that create.

Checked-in days, all synthetic:

| Day | Projected | Runner | Reject | Skip | Reason with `n>0` |
| --- | --- | --- | --- | --- | --- |
| `2026-09-20` | 2 | 1 (`SigSynth20Runner`) | 1 (`mint=UNK`, `missing_mint`) | `outcome_mark` | `missing_mint` |
| `2026-09-21` | 2 | 1 (`SigSynth21Runner`) | 1 (`signature=UNK`, `missing_signature`) | migration `ingest_hot` | `missing_signature` |

Rollup `n=4`, runner `2`, reject `2`, share `2/4` and `2/4`. `rollup.share_kind=count_fraction_not_a_return`. `rollup.counts_are_not_returns=true`. The `2/4` is a stamp count. It is not a return, not EV, and not lift.

`mint=UNK` and `signature=UNK` are identities, same rule as paper-evaluate. They are not wildcards.

A fixture disagreement does not relabel a stamp and does not delete it. `label_disagree_n` stays a join count. The process still exits `0`. It does not become `FAIL_NO_LIFT`.

---

## Graph stays cold

This stamp does not attach graph slots. `graph.slots=null`, `graph.cold=true`, `graph_policy=cold`, `graph_lift=null`. Each scoreboard aggregate is `not_used_graph_cold`.

H-G2, ordinal buckets, NH-Index, and NH-G3a stay off the packet. The integer on the paper-scoreboard slot fixture is not an input here.

---

## Horizons and `Δ_exec`

Null, with the scoreboard's explicit status. The skipped day-20 mark carries `price_proxy`. That number stays on the source row. It is not copied onto a horizon, a packet, or `delta_exec`.

| Object | Status | Value |
| --- | --- | --- |
| Each embedded stamp | `horizon_status=null_ok_no_marks_on_this_stamp` | every horizon key `null` |
| Each scoreboard `horizons` | `fixture_joined_null_explicit` | `values` all `null`. `null_is_not_zero_return=true` |
| Each scoreboard `delta_exec` | `fixture_joined_null_explicit` | `value=null`, `reason=sealed_day_fixture_has_no_fill` |

`measure.kind=none`. `measure.pass_fail_no_lift=false`. `measure.invented_ev=false`. `measure.invented_lift=false`. `measure.claims_alpha=false`.

---

## Honesty

| Claim | v0 fact |
| --- | --- |
| Merge of this registration | Docs + schema + synthetic fixtures + fixture CLI. **Does not** wire a batch into observe |
| Input | Synthetic day-aligned JSONL plus one synthetic sealed-day expectation per day |
| Encoder | **Not** promoted. `observe/client.py` is untouched |
| Enum production lock | **No.** `global_95bps` and `launchlab_init` stay **Proposed**. This projection refuses them on a sealed row |
| Discovery promote | **No.** No Graph revive, no X ingest, no mark densify |
| EXP-002c | **Not** retuned. No new evaluate threshold. No invented lift |
| EV / alpha | **No.** `honesty.invented_ev=false`, `honesty.claims_alpha=false`, `measure.kind=none` |
| Measure exit | **None.** Exit `0` when a batch is printed, including when `label_disagree_n` is non-zero. Exit `1` when input is not valid. No `PASS` / `FAIL_NO_LIFT` |
| Oracle measure | **No.** `honesty.scored_oracle_measure=false`. `honesty.executed_host_sealed_book=false` |
| Trading | Paper only. No live capital, no trading keys, no PumpPortal trade API |
| `Δ_exec` | Null on every embedded scoreboard |

`honesty` is const-false on scored measure, executed host sealed book, wiring, encoder promote, enum lock, Discovery promote, graph-lane revive, invented lift, invented EV, alpha claim, EXP-002c retune, live capital, trading keys, and PumpPortal trade API. A batch that flips those bits does not validate.

---

## Soft watches (non-blocking)

**Soft GATE is required** before merge. `soft_watches.blocking=false`. `soft_watches.source=hot_packet_v0_soft_gate_pr49_paper_evaluate_pr50_scoreboard_pr51`. The watches do not fail merge of this registration.

Inherited from hot-packet v0 (PR #49), paper-evaluate (PR #50), and paper-scoreboard (PR #51):

| Watch id | What it is | Why it does not block |
| --- | --- | --- |
| `schema_looser_than_cli` | JSON Schema is the citeable shape. The CLI recomputes the batch from the embedded JSONL rows and refuses a closed book, a numeric horizon, and a non-cold graph. | Do not widen the CLI down to the schema. Do not hold this consumer for a schema rewrite. |
| `synthetic_launchlab_shape` | Inherited name. This batch's checked-in rows are sealed defaults, so the LaunchLab shape is not in the example. | Still not an enum lock if a later reader meets the parent fixture. |
| `graph_slot_shape_not_a_score` | Inherited name. This stamp keeps slots null. | `graph_lift` stays null. |
| `dec005_draft_unmerged` | Embedded packets still carry `clock_source=dec005_draft_pr8_unmerged`. | Hooks only. Not a claim that DEC-005 merged. |
| `sealed_book_rpc_slice_incomplete` | Every packet, scoreboard, and this batch keep the slice `incomplete`. | This batch does not close the sealed book. |
| `label_share_is_not_a_return` | Scoreboard shares are count fractions. | Do not read them as EV. |
| `synthetic_sealed_day_not_a_host_extract` | Day strings match calendar days sealed observe also uses. | These files are synthetic. |
| `local_subset_is_not_dropped_history` | Inherited scoreboard rule for expectation rows absent from a smaller set. | Unused checklist rows are not a dropped reject arm. |

Named on this registration:

| Watch id | What it is | Why it does not block |
| --- | --- | --- |
| `host_jsonl_not_readable_from_ci` | Host Oracle JSONL is not readable from CI or from a cloud agent. | The checked-in run is the synthetic pair. A missing host file is not a failed measure. |
| `counts_are_not_returns` | Rollup `2/4` and per-day `1/2` are stamp counts. The skipped mark's `price_proxy` is not a horizon. | Counts are not returns, not EV, and not lift. |
| `graph_stays_cold_on_this_stamp` | Slots stay null. | Do not treat a later graph scalar as already scored here. |

---

## CLI

From the repo root:

```bash
python -m tools.paper_batch_oracle_sealed_day_incomplete_rpc_v0 example --which two-day
python -m tools.paper_batch_oracle_sealed_day_incomplete_rpc_v0 validate \
  fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/two_day.json
python -m tools.paper_batch_oracle_sealed_day_incomplete_rpc_v0 batch \
  --jsonl fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/observe-2026-09-20.jsonl \
  --jsonl fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/observe-2026-09-21.jsonl \
  --expectation fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/sealed_day_2026-09-20_expectation.json \
  --expectation fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/sealed_day_2026-09-21_expectation.json
```

`--which` value: `two-day`.

`batch` pairs each `observe-YYYY-MM-DD.jsonl` with the expectation whose `day` field matches. It prints one batch. It does not call RPC, does not tail observe, and does not write a side book. Exit `0` when a batch is printed. Exit `1` when a file is missing, a line is not valid JSON, a create cannot project, or an expectation is not a synthetic incomplete checklist. There is no measure exit code.

| Fixture | What it shows |
| --- | --- |
| `observe-2026-09-20.jsonl` | Two synthetic creates and one skipped `outcome_mark`. Blank line ignored |
| `observe-2026-09-21.jsonl` | Two synthetic creates and one skipped migration row |
| `sealed_day_2026-09-20_expectation.json` | Synthetic checklist for the day-20 identities. `origin=synthetic` |
| `sealed_day_2026-09-21_expectation.json` | Synthetic checklist for the day-21 identities |
| `two_day.json` | The example batch. Both arms, both days, book left incomplete |

Checked-in fixtures are **synthetic**. They are not Oracle extracts and they are not lift evidence.

---

## Non-goals

| Cap | Held |
| --- | --- |
| Live capital, trading keys, PumpPortal trade API | Out |
| `observe/client.py` / encoder promote | Untouched |
| Continuous observe-wiring | Out |
| Production lock of `global_95bps` or `launchlab_init` | Out. Both stay **Proposed** |
| Discovery promote, Graph revive, X ingest, mark densify | Out |
| EXP-002c retune, new evaluate thresholds | Out |
| Scored Oracle measure, invented lift / EV / cohort alpha | Out |
| `PASS` / `FAIL_NO_LIFT` measure exit | Out |
| Joining marks or filling `Δ_exec` with a number | Out |
| Closing `sealed_book_rpc_slice` | Out. Stays `incomplete` |
| Claiming a host sealed-book run from CI | Out. `executed_host_sealed_book=false` |
| Soft watches above | Listed, **non-blocking**. Soft GATE still **required** before merge |

---

## Cross-links

- Scoreboard: [PAPER-SCOREBOARD-SEALED-FIXTURE-V0.md](PAPER-SCOREBOARD-SEALED-FIXTURE-V0.md), [paper-scoreboard-sealed-fixture-v0.schema.json](paper-scoreboard-sealed-fixture-v0.schema.json)
- Evaluate: [PAPER-EVALUATE-HOT-PACKET-V0.md](PAPER-EVALUATE-HOT-PACKET-V0.md), [paper-evaluate-hot-packet-v0.schema.json](paper-evaluate-hot-packet-v0.schema.json)
- Decode packet: [HOT-PACKET-V0.md](HOT-PACKET-V0.md), [hot-packet-v0.schema.json](hot-packet-v0.schema.json)
- Sealed row: [OBSERVE-JSONL-SCHEMA.md](OBSERVE-JSONL-SCHEMA.md)
- Pipeline: [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md)
- Full book: [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md)
- Manager digest: [SUMMARY.md](SUMMARY.md)
- Clocks draft (unmerged): [PR #8](https://github.com/vaanai/MAL/pull/8)
- Parent registrations: [PR #49](https://github.com/vaanai/MAL/pull/49), [PR #50](https://github.com/vaanai/MAL/pull/50), [PR #51](https://github.com/vaanai/MAL/pull/51)
- Operator-local flags only: [paper_batch_oracle_run.md](../tools/paper_batch_oracle_run.md)
