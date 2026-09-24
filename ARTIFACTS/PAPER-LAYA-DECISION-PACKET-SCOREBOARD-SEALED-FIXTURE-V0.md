# Paper LAYA decision-packet scoreboard on sealed-day fixtures — Proposed paper registration

| | |
| --- | --- |
| **ID** | `paper-laya-decision-packet-scoreboard-sealed-fixture-v0` |
| **Status** | **Proposed** paper registration (2026-09-24). Not a measure. Not run. |
| **Owner seat** | Proof (scoreboard + Soft GATE); Scout (decision-packet spine stays on embedded packets); Helm (FORMAL auth) |
| **Commission** | Consumer of merged LAYA precompute decision packet ([#67](https://github.com/vaanai/MAL/pull/67) squash `52a06e7` on `main`) and paper scoreboard sealed-fixture pattern ([#51](https://github.com/vaanai/MAL/pull/51), [#58](https://github.com/vaanai/MAL/pull/58)). Parent #49–#67 CLIs are **not** rewritten. |
| **Schema** | [paper-laya-decision-packet-scoreboard-sealed-fixture-v0.schema.json](paper-laya-decision-packet-scoreboard-sealed-fixture-v0.schema.json) (JSON Schema 2020-12) |
| **Fixtures** | [fixtures/paper_laya_decision_packet_scoreboard_sealed_fixture_v0/](../fixtures/paper_laya_decision_packet_scoreboard_sealed_fixture_v0/) — synthetic, not host extracts |
| **CLI** | `python -m tools.paper_laya_decision_packet_scoreboard_sealed_fixture_v0` — validate / example / score on local JSON only |
| **Soft GATE** | **Pending** for Proof. Watches in [Soft watches](#soft-watches-non-blocking) stay **non-blocking** (`blocking=false`). |

This file registers how a local set of validated [`paper_laya_precompute_decision_packet_v0`](PAPER-LAYA-PRECOMPUTE-DECISION-PACKET-V0.md) objects is counted against a **checked-in sealed-day expectation**. Same honesty class as [PAPER-SCOREBOARD-SEALED-FIXTURE-V0.md](PAPER-SCOREBOARD-SEALED-FIXTURE-V0.md) and [PAPER-FILL-SIM-SCOREBOARD-SEALED-FIXTURE-V0.md](PAPER-FILL-SIM-SCOREBOARD-SEALED-FIXTURE-V0.md), but the stamp input is the #67 decision packet, not raw paper-evaluate or fill-sim bind.

Merge ≠ Oracle measure ≠ LAYA authorize-run ≠ risk-gate unlock ≠ live trading ≠ Discovery promote.

---

## What this scoreboard is

| Piece | v0 fact |
| --- | --- |
| Packet input | One or more local JSON objects that validate as `paper_laya_precompute_decision_packet_v0`, or a directory of those files under `fixtures/paper_laya_precompute_decision_packet_v0/` |
| Fixture side | One checked-in sealed-day expectation. Synthetic only. Rows key on `spine_profile` + `assembly_fingerprint`, not a wildcard |
| Read path | Local JSON only. `rpc=false`. `observe_jsonl_tail=false`. `host_extract_required=false`. `host_jsonl_read=false`. `marks_joined=false`. No `/var/lib/mal` |
| Output | Spine-profile counts, digest label counts, fill-sim digest status counts (when present), null horizons, null `Δ_exec`, join counts |
| Population | `packet_n` is the local set. `digest_stamp_n` sums surround digest stamp counts. `local_set_is_not_the_sealed_book=true`. Day `2026-09-20` is the synthetic sealed-day string on these fixtures |

Rates are **counts**. `share` is `{numerator, denominator}`. `share_kind=count_fraction_not_a_return`. A zero numerator is a count of zero packets or digest stamps. It is not a 0% return.

---

## DEC-007 both arms (spines and digest labels)

Every scoreboard sets `full_book.policy=dec007_both_arms_retained`, `reject_stamps_dropped=0`, and:

| Table | Both arms always present |
| --- | --- |
| `spine_profile_counts` | `non_fill_sim_spine`, `fill_sim_spine`, `mixed_spines` — zero rows stay on the table |
| `label_rates.rows` | `runner` then `reject` on aggregated digest stamp counts |
| `fill_sim_status_counts` | `documented_model_only` then `reject_arm_no_pretend_buy` on aggregated fill-sim digest rows when cited |

`score` counts every valid decision packet it was given. Join disagreement does not relabel or delete packets. Cited packets keep `risk_gate.decision=locked` and `risk_gate.unlock=false`.

---

## Horizons and `Δ_exec`

| Object | Status | Value |
| --- | --- | --- |
| Each embedded decision packet | `decision_packet_null_explicit` | horizon `values` all `null`; `delta_exec.value=null` |
| Scoreboard `horizons` | `fixture_joined_null_explicit` | `values` all `null`; `null_is_not_zero_return=true` |
| Scoreboard `delta_exec` | `fixture_joined_null_explicit` | `value=null`, `reason=laya_decision_packet_sealed_day_fixture_has_no_scored_fill`, `null_is_not_zero_cost=true` |

---

## Sealed book stays incomplete

| Field | Value |
| --- | --- |
| `dual_read.sealed_book_rpc_slice` | `incomplete` |
| `dual_read.closed_book_claim` | `false` |
| `dual_read.incomplete_reason` | `laya_decision_packet_fixture_join_does_not_close_the_sealed_book` |
| `fixture_join.closed_book` | `false` |
| `measure.kind` | `none` |
| `graph_policy` | `cold` |
| `graph_lift` | `null` |

Scoreboard-level `laya.authorize_run=false`, `laya.risk_gate_unlock=false`, `laya.live_trading=false`. Scoreboard-level `risk_gate.decision=locked`, `risk_gate.unlock=false`.

---

## CLI

From the repo root:

```bash
python -m tools.paper_laya_decision_packet_scoreboard_sealed_fixture_v0 example --which all-spines
python -m tools.paper_laya_decision_packet_scoreboard_sealed_fixture_v0 validate \
  fixtures/paper_laya_decision_packet_scoreboard_sealed_fixture_v0/all_spines.json
python -m tools.paper_laya_decision_packet_scoreboard_sealed_fixture_v0 score \
  --expectation fixtures/paper_laya_decision_packet_scoreboard_sealed_fixture_v0/sealed_day_2026-09-20_expectation.json \
  fixtures/paper_laya_precompute_decision_packet_v0/decision_non_fill_sim_surround.json \
  fixtures/paper_laya_precompute_decision_packet_v0/decision_fill_sim_surround.json \
  fixtures/paper_laya_precompute_decision_packet_v0/decision_mixed_spines.json
```

`validate` and `score` refuse `/var/lib/mal`, `//var/lib/mal`, `///var/lib/mal`, relative and `./var/lib/mal/...`, and lexical `..` forms under that root **before** `read_text` / `open` / `stat` / `resolve`.

Exit `0` when a scoreboard prints or validates. Exit `1` on invalid input or a refused path. No `PASS` / `FAIL_NO_LIFT` exit.

Proof:

```bash
python3 -m unittest tools.test_paper_laya_decision_packet_scoreboard_sealed_fixture_v0
```

---

## Soft watches (non-blocking)

**Soft GATE is pending** before Proof treats this registration as cited law. `soft_watches.blocking=false`. Inherited items from the #67 decision-packet Soft GATE chain stay non-blocking. Local watches name digest/spine share misreads and decision-packet lock caps.

Draft 2020-12 now binds scoreboard aggregates to the cited decision-packet fixture sets (same rescore path as `validate_scoreboard`): `spine_profile_counts`, `label_rates`, `fill_sim_status_counts`, `full_book` stamp counts, `fixture_join` / `sealed_days` day and join counts, `packet_rows` digests, `reason_histogram.rows` ordered `prefixItems`, and the checked-in `input.expectation` object. Mutated `n`/shares, duplicate reason rows, and expectation day/digest drift fail schema and CLI. Soft GATE **Formal** is still **pending** — this is not a PASS claim.

Soft GATE **pending** ≠ Discovery promote ≠ observe-wiring ≠ enum lock ≠ densify ≠ EXP-002c retune ≠ Graph revive ≠ X on host ≠ live trading ≠ EXP-006 promote ≠ LAYA authorize-run ≠ **risk-gate unlock**.

---

## Parent chain

Citeable on `main` through #67: [PAPER-LAYA-PRECOMPUTE-DECISION-PACKET-V0.md](PAPER-LAYA-PRECOMPUTE-DECISION-PACKET-V0.md). This scoreboard does not rewrite `python -m tools.paper_laya_precompute_decision_packet_v0` or parent #49–#67 CLIs. `observe/client.py` is untouched.
