"""Offline tests for paper-laya-decision-packet-batch-oracle-sealed-day-incomplete-rpc-v0."""

from __future__ import annotations

import copy
import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from tools.paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0 import (
    BATCH_ID,
    EXAMPLE_DAYS,
    HOST_ROOT,
    PIPELINE,
    SOFT_WATCH_ITEMS,
    assemble,
    example_two_day,
    expectation_from_packets,
    main,
    manifest_for_day,
    project_day,
    validate_batch,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0"
SCHEMA = ROOT / "ARTIFACTS" / "paper-laya-decision-packet-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json"
SCOREBOARD_SCHEMA = ROOT / "ARTIFACTS" / "paper-laya-decision-packet-scoreboard-sealed-fixture-v0.schema.json"
DECISION_SCHEMA = ROOT / "ARTIFACTS" / "paper-laya-precompute-decision-packet-v0.schema.json"
OBSERVE_CLIENT = ROOT / "observe" / "client.py"
BATCH_NAME = "two_day.json"


def _registry() -> Registry:
    resources: dict[str, Resource] = {}
    for path in (SCHEMA, SCOREBOARD_SCHEMA, DECISION_SCHEMA):
        data = json.loads(path.read_text(encoding="utf-8"))
        resources[data["$id"]] = Resource.from_contents(data)
    return Registry().with_resources(resources.items())


def _schema_errors(doc: object) -> list[str]:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, registry=_registry())
    return [err.message for err in validator.iter_errors(doc)]


def _quiet(argv: list[str]) -> int:
    with redirect_stdout(io.StringIO()):
        return main(argv)


class PaperLayaDecisionPacketBatchOracleSealedDayIncompleteRpcV0Tests(unittest.TestCase):
    def test_example_validates_and_matches_fixture(self) -> None:
        batch = example_two_day()
        self.assertEqual(validate_batch(batch), [])
        on_disk = json.loads((FIXTURES / BATCH_NAME).read_text(encoding="utf-8"))
        self.assertEqual(on_disk, batch)
        self.assertEqual(_quiet(["validate", str(FIXTURES / BATCH_NAME)]), 0)
        self.assertEqual(_schema_errors(batch), [])

    def test_manifest_and_expectation_files(self) -> None:
        batch = example_two_day()
        for day_input in batch["input"]["days"]:
            day = day_input["day"]
            manifest_on_disk = json.loads((FIXTURES / f"decision-day-{day}.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest_on_disk, manifest_for_day(day))
            exp_on_disk = json.loads((FIXTURES / f"sealed_day_{day}_expectation.json").read_text(encoding="utf-8"))
            self.assertEqual(exp_on_disk, day_input["expectation"])

    def test_pipeline_and_caps(self) -> None:
        batch = example_two_day()
        self.assertEqual(batch["input"]["pipeline"], list(PIPELINE))
        self.assertEqual(batch["id"], BATCH_ID)
        self.assertIs(batch["dual_read"]["closed_book_claim"], False)
        self.assertEqual(batch["dual_read"]["sealed_book_rpc_slice"], "incomplete")
        self.assertEqual(batch["measure"]["kind"], "none")
        self.assertIsNone(batch["graph_lift"])
        self.assertFalse(batch["soft_watches"]["blocking"])
        for item in SOFT_WATCH_ITEMS:
            self.assertIn(item, batch["soft_watches"]["items"])

    def test_rollup_digest_counts(self) -> None:
        batch = example_two_day()
        self.assertEqual(batch["rollup"]["packet_n"], 5)
        self.assertEqual(batch["rollup"]["n"], 30)
        self.assertEqual(batch["rollup"]["runner_n"], 24)
        self.assertEqual(batch["rollup"]["reject_n"], 6)
        self.assertEqual(batch["rollup"]["days"], list(EXAMPLE_DAYS))

    def test_each_day_scoreboard_caps(self) -> None:
        batch = example_two_day()
        for day_block in batch["days"]:
            board = day_block["scoreboard"]
            self.assertEqual(board["risk_gate"]["decision"], "locked")
            self.assertIs(board["risk_gate"]["unlock"], False)
            self.assertIs(board["laya"]["authorize_run"], False)
            self.assertIs(board["laya"]["risk_gate_unlock"], False)
            self.assertIs(board["laya"]["live_trading"], False)
            profiles = {row["spine_profile"]: row["n"] for row in board["spine_profile_counts"]}
            self.assertIn("non_fill_sim_spine", profiles)
            self.assertIn("fill_sim_spine", profiles)
            self.assertIn("mixed_spines", profiles)

    def test_example_and_batch_cli(self) -> None:
        expected = example_two_day()
        self.assertEqual(_quiet(["example", "--which", "two-day"]), 0)
        code = _quiet(
            [
                "batch",
                "--manifest",
                str(FIXTURES / "decision-day-2026-09-21.json"),
                "--manifest",
                str(FIXTURES / "decision-day-2026-09-20.json"),
                "--expectation",
                str(FIXTURES / "sealed_day_2026-09-21_expectation.json"),
                "--expectation",
                str(FIXTURES / "sealed_day_2026-09-20_expectation.json"),
            ]
        )
        self.assertEqual(code, 0)

    def test_batch_refuses_var_lib_mal_without_read(self) -> None:
        real_read_text = Path.read_text

        def _guarded_read_text(self: Path, *args: object, **kwargs: object) -> str:
            text = os.fspath(self)
            collapsed = "/" + text.lstrip("/") if text.startswith("//") else text
            if collapsed == HOST_ROOT or collapsed.startswith(HOST_ROOT + os.sep):
                raise AssertionError(f"read_text called on host path: {text}")
            return real_read_text(self, *args, **kwargs)

        argv = [
            "batch",
            "--manifest",
            "//var/lib/mal/decision-day-2026-09-20.json",
            "--expectation",
            str(FIXTURES / "sealed_day_2026-09-20_expectation.json"),
        ]
        with mock.patch.object(Path, "read_text", _guarded_read_text):
            err = io.StringIO()
            with redirect_stderr(err):
                code = main(argv)
        self.assertEqual(code, 1)
        self.assertIn("does not open", err.getvalue())

    def _assert_refuses_without_host_fs_touch(self, argv: list[str], *, cwd: str = "/") -> None:
        real_read_text = Path.read_text
        real_open = Path.open
        real_stat = Path.stat
        real_resolve = Path.resolve

        def _is_host_path(text: str) -> bool:
            collapsed = "/" + text.lstrip("/") if text.startswith("//") else text
            norm = os.path.normpath(collapsed)
            return norm == HOST_ROOT or norm.startswith(HOST_ROOT + os.sep)

        def _guarded_read_text(self: Path, *args: object, **kwargs: object) -> str:
            text = os.fspath(self)
            if _is_host_path(text):
                raise AssertionError(f"read_text called on host path: {text}")
            return real_read_text(self, *args, **kwargs)

        def _guarded_open(self: Path, *args: object, **kwargs: object) -> object:
            text = os.fspath(self)
            if _is_host_path(text):
                raise AssertionError(f"open called on host path: {text}")
            return real_open(self, *args, **kwargs)

        def _guarded_stat(self: Path, *args: object, **kwargs: object) -> object:
            text = os.fspath(self)
            if _is_host_path(text):
                raise AssertionError(f"stat called on host path: {text}")
            return real_stat(self, *args, **kwargs)

        def _guarded_resolve(self: Path, *args: object, **kwargs: object) -> Path:
            text = os.fspath(self)
            if _is_host_path(text):
                raise AssertionError(f"resolve called on host path: {text}")
            return real_resolve(self, *args, **kwargs)

        with (
            mock.patch.object(Path, "read_text", _guarded_read_text),
            mock.patch.object(Path, "open", _guarded_open),
            mock.patch.object(Path, "stat", _guarded_stat),
            mock.patch.object(Path, "resolve", _guarded_resolve),
            mock.patch("os.getcwd", return_value=cwd),
        ):
            err = io.StringIO()
            with redirect_stderr(err):
                code = main(argv)
        self.assertEqual(code, 1)
        self.assertIn("does not open", err.getvalue())
        self.assertIn(HOST_ROOT, err.getvalue())

    def test_batch_refuses_relative_var_lib_mal_at_root_cwd(self) -> None:
        self._assert_refuses_without_host_fs_touch(
            [
                "batch",
                "--manifest",
                "var/lib/mal/decision-day-2026-09-20.json",
                "--expectation",
                str(FIXTURES / "sealed_day_2026-09-20_expectation.json"),
            ],
            cwd="/",
        )

    def test_batch_refuses_dot_relative_var_lib_mal(self) -> None:
        self._assert_refuses_without_host_fs_touch(
            [
                "batch",
                "--manifest",
                "./var/lib/mal/decision-day-2026-09-20.json",
                "--expectation",
                str(FIXTURES / "sealed_day_2026-09-20_expectation.json"),
            ],
        )

    def test_validate_refuses_host_path(self) -> None:
        code = _quiet(["validate", "/var/lib/mal/batch.json"])
        self.assertEqual(code, 1)

    def test_dishonest_closed_book_fails_validate(self) -> None:
        batch = copy.deepcopy(example_two_day())
        batch["dual_read"]["closed_book_claim"] = True
        self.assertTrue(_schema_errors(batch))
        self.assertTrue(validate_batch(batch))

    def test_dishonest_risk_gate_unlock_fails_validate(self) -> None:
        batch = copy.deepcopy(example_two_day())
        board = batch["days"][0]["scoreboard"]
        board["risk_gate"]["unlock"] = True
        self.assertTrue(validate_batch(batch))

    def test_observe_client_untouched(self) -> None:
        before = OBSERVE_CLIENT.read_text(encoding="utf-8")
        example_two_day()
        after = OBSERVE_CLIENT.read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def test_project_day_rebuilds_expectation(self) -> None:
        for day in EXAMPLE_DAYS:
            manifest = manifest_for_day(day)
            packets, _, errors = project_day(manifest, day=day, fixture_origin="synthetic")
            self.assertEqual(errors, [])
            exp = expectation_from_packets(day, packets)
            self.assertEqual(exp["day"], day)
            for row in exp["rows"]:
                self.assertEqual(row["source_day"], day)

    def test_schema_file_locks_pipeline(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(
            schema["title"],
            "MAL paper-laya-decision-packet-batch-oracle-sealed-day-incomplete-rpc v0 (Proposed paper batch)",
        )
        pipeline = schema["$defs"]["input"]["properties"]["pipeline"]
        self.assertEqual(
            [item["const"] for item in pipeline["prefixItems"]],
            list(PIPELINE),
        )

    def _assert_soft_gate_fail1(self, mutate: object, *, label: str) -> None:
        batch = copy.deepcopy(example_two_day())
        mutate(batch)
        with self.subTest(label=label):
            self.assertTrue(_schema_errors(batch), label)
            self.assertTrue(validate_batch(batch), label)

    def test_soft_gate_fail1_numeric_horizons_and_delta_exec_fail_schema_and_cli(self) -> None:
        self._assert_soft_gate_fail1(
            lambda b: b["days"][0]["scoreboard"]["horizons"]["values"].update({"5s": 0.01}),
            label="numeric horizon",
        )
        self._assert_soft_gate_fail1(
            lambda b: b["days"][0]["scoreboard"]["delta_exec"].update({"value": 0}),
            label="numeric delta_exec",
        )
        self._assert_soft_gate_fail1(
            lambda b: b["days"][0]["scoreboard"]["horizons"]["values"].update({"15s": 1}),
            label="numeric horizon joined slot",
        )

    def test_soft_gate_fail1_unbound_counts_fail_schema_and_cli(self) -> None:
        self._assert_soft_gate_fail1(
            lambda b: b["rollup"].update({"n": 99}),
            label="rollup n",
        )
        self._assert_soft_gate_fail1(
            lambda b: b["rollup"]["rows"][0].update({"n": 99}),
            label="rollup runner n",
        )
        self._assert_soft_gate_fail1(
            lambda b: b["full_book"].update({"packet_n": 99}),
            label="full_book packet_n",
        )
        self._assert_soft_gate_fail1(
            lambda b: b["days"][0]["packet_census"].update({"listed_n": 99}),
            label="packet_census listed_n",
        )
        self._assert_soft_gate_fail1(
            lambda b: b["days"][0]["scoreboard"]["spine_profile_counts"][0].update({"n": 99}),
            label="spine_profile_counts n",
        )
        self._assert_soft_gate_fail1(
            lambda b: b["days"][0]["scoreboard"]["label_rates"]["rows"][0].update({"n": 99}),
            label="label_rates runner n",
        )
        self._assert_soft_gate_fail1(
            lambda b: b["days"][0]["scoreboard"]["packet_rows"][0].update({"digest_n": 99}),
            label="packet_row digest_n",
        )

    def test_soft_gate_fail1_soft_watches_order_and_uniqueness_fail_schema_and_cli(self) -> None:
        items = example_two_day()["soft_watches"]["items"]
        self._assert_soft_gate_fail1(
            lambda b: b["soft_watches"].update({"items": list(reversed(items))}),
            label="reversed soft_watches",
        )
        self._assert_soft_gate_fail1(
            lambda b: b["soft_watches"].update(
                {"items": [items[0], items[0], *items[1:]]}
            ),
            label="duplicate soft_watches",
        )
        self._assert_soft_gate_fail1(
            lambda b: b["soft_watches"].update({"items": items[:-1]}),
            label="dropped soft_watches",
        )

    def test_soft_gate_fail1_manifest_paths_fail_schema_and_cli(self) -> None:
        bad = "fixtures/paper_laya_precompute_decision_packet_v0/../paper_laya_precompute_decision_packet_v0/decision_non_fill_sim_surround.json"
        self._assert_soft_gate_fail1(
            lambda b: b["input"]["days"][0]["manifest"]["decision_packet_paths"].append(bad),
            label="manifest dotdot path",
        )
        self._assert_soft_gate_fail1(
            lambda b: b["input"]["days"][0]["manifest"]["decision_packet_paths"].__setitem__(
                0, "/fixtures/paper_laya_precompute_decision_packet_v0/decision_non_fill_sim_surround.json"
            ),
            label="manifest absolute path",
        )
        collapse = (
            "fixtures/paper_laya_precompute_decision_packet_v0//decision_non_fill_sim_surround.json"
        )
        self._assert_soft_gate_fail1(
            lambda b: b["input"]["days"][0]["manifest"]["decision_packet_paths"].__setitem__(
                0, collapse
            ),
            label="manifest double slash",
        )
        self._assert_soft_gate_fail1(
            lambda b: b["input"]["days"][0]["manifest"]["decision_packet_paths"].__setitem__(
                0,
                "fixtures/paper_laya_precompute_decision_packet_v0/./decision_non_fill_sim_surround.json",
            ),
            label="manifest dot segment",
        )
        self._assert_soft_gate_fail1(
            lambda b: b["input"]["days"][0]["manifest"]["decision_packet_paths"].__setitem__(
                0,
                "fixtures/paper_laya_precompute_decision_packet_v0/decision_non_fill_sim_surround.json/",
            ),
            label="manifest trailing slash",
        )
        self._assert_soft_gate_fail1(
            lambda b: b["input"]["days"][0]["manifest"]["decision_packet_paths"].__setitem__(
                0,
                "fixtures\\paper_laya_precompute_decision_packet_v0\\decision_non_fill_sim_surround.json",
            ),
            label="manifest backslash",
        )

    def test_soft_gate_fail1_incomplete_reason_and_ev_fail_schema_and_cli(self) -> None:
        self._assert_soft_gate_fail1(
            lambda b: b["dual_read"].update(
                {"incomplete_reason": "closed_book_projection"}
            ),
            label="batch incomplete_reason drift",
        )
        self._assert_soft_gate_fail1(
            lambda b: b["days"][0]["scoreboard"]["dual_read"].update(
                {"incomplete_reason": "host_sealed_book_close"}
            ),
            label="embedded scoreboard incomplete_reason drift",
        )
        self._assert_soft_gate_fail1(
            lambda b: b.update({"ev": 1.0}),
            label="invented ev key",
        )

    def test_soft_gate_fail1_dropped_spine_row_fail_schema_and_cli(self) -> None:
        self._assert_soft_gate_fail1(
            lambda b: b["days"][0]["scoreboard"].update(
                {
                    "spine_profile_counts": [
                        b["days"][0]["scoreboard"]["spine_profile_counts"][0]
                    ]
                }
            ),
            label="dropped spine_profile_counts row",
        )

    def _assert_soft_gate_fail2(self, mutate: object, *, label: str) -> None:
        batch = copy.deepcopy(example_two_day())
        mutate(batch)
        with self.subTest(label=label):
            self.assertTrue(_schema_errors(batch), label)
            self.assertTrue(validate_batch(batch), label)

    def test_soft_gate_fail2_shortened_manifest_paths_fail_schema_and_cli(self) -> None:
        self._assert_soft_gate_fail2(
            lambda b: b["input"]["days"][0]["manifest"]["decision_packet_paths"].pop(),
            label="shortened day-20 manifest paths",
        )
        self._assert_soft_gate_fail2(
            lambda b: b["input"]["days"][1]["manifest"]["decision_packet_paths"].pop(),
            label="shortened day-21 manifest paths",
        )

    def test_soft_gate_fail2_day_order_fail_schema_and_cli(self) -> None:
        self._assert_soft_gate_fail2(
            lambda b: b.__setitem__("days", list(reversed(b["days"]))),
            label="reversed output days",
        )
        self._assert_soft_gate_fail2(
            lambda b: b["input"].__setitem__("days", list(reversed(b["input"]["days"]))),
            label="reversed input days",
        )

    def test_soft_gate_fail2_manifest_assemblies_with_paths_fail_schema_and_cli(self) -> None:
        self._assert_soft_gate_fail2(
            lambda b: b["input"]["days"][0]["manifest"].update(
                {
                    "assemblies": [
                        {
                            "surround_paths": [
                                "fixtures/paper_laya_precompute_surround_packet_v0/mixed_scoreboard.json"
                            ],
                            "lock_receipt_paths": [
                                "fixtures/paper_laya_risk_gate_lock_receipt_v0/receipt_non_fill_sim_surround.json"
                            ],
                        }
                    ]
                }
            ),
            label="assemblies beside decision_packet_paths",
        )

    def test_soft_gate_fail2_duplicate_day_block_fail_schema_and_cli(self) -> None:
        def _dup_day(batch: dict) -> None:
            extra = copy.deepcopy(batch["days"][0])
            extra["scoreboard"]["packet_rows"][0]["digest_n"] = 99
            batch["days"].append(extra)

        self._assert_soft_gate_fail2(_dup_day, label="duplicate day-20 output block")

    def test_soft_gate_fail2_phantom_fixture_filename_fail_schema_and_cli(self) -> None:
        phantom = (
            "fixtures/paper_laya_precompute_decision_packet_v0/decision_phantom.json"
        )
        self._assert_soft_gate_fail2(
            lambda b: b["input"]["days"][0]["manifest"]["decision_packet_paths"].__setitem__(
                0, phantom
            ),
            label="phantom decision-packet filename",
        )
        manifest = manifest_for_day("2026-09-20")
        manifest["decision_packet_paths"] = [phantom]
        _, _, errors = project_day(manifest, day="2026-09-20", fixture_origin="synthetic")
        self.assertTrue(errors)

    def _assert_soft_gate_fail3(self, mutate: object, *, label: str) -> None:
        batch = copy.deepcopy(example_two_day())
        mutate(batch)
        with self.subTest(label=label):
            self.assertTrue(_schema_errors(batch), label)
            self.assertTrue(validate_batch(batch), label)

    def test_soft_gate_fail3_neither_manifest_mode_fail_schema_and_cli(self) -> None:
        def _drop_paths(batch: dict) -> None:
            del batch["input"]["days"][0]["manifest"]["decision_packet_paths"]

        self._assert_soft_gate_fail3(_drop_paths, label="neither assemblies nor decision_packet_paths")

    def test_soft_gate_fail3_assemblies_only_leftover_board_fail_schema_and_cli(self) -> None:
        def _assemblies_leftover(batch: dict) -> None:
            manifest = batch["input"]["days"][0]["manifest"]
            manifest.pop("decision_packet_paths")
            manifest["assemblies"] = [
                {
                    "surround_paths": [
                        "fixtures/paper_laya_precompute_fill_sim_surround_packet_v0/mixed_scoreboard.json"
                    ],
                    "lock_receipt_paths": [
                        "fixtures/paper_laya_risk_gate_lock_receipt_v0/receipt_fill_sim_surround.json"
                    ],
                }
            ]
            batch["rollup"]["n"] = 99
            batch["days"][0]["packet_census"]["listed_n"] = 99
            batch["days"][0]["scoreboard"]["packet_rows"][0]["digest_n"] = 99

        self._assert_soft_gate_fail3(_assemblies_leftover, label="assemblies-only with unbound counts")

    def test_soft_gate_fail3_split_day_identity_fail_schema_and_cli(self) -> None:
        split_day = "2026-09-22"

        def _split_identity(batch: dict) -> None:
            batch["input"]["days"][0]["day"] = split_day
            batch["input"]["days"][0]["manifest_name"] = f"decision-day-{split_day}.json"
            batch["input"]["days"][0]["expectation"]["day"] = split_day
            batch["days"][0]["day"] = split_day
            batch["days"][0]["manifest_name"] = f"decision-day-{split_day}.json"
            batch["rollup"]["days"] = [split_day, batch["rollup"]["days"][1]]
            batch["input"]["days"][0]["manifest"]["decision_packet_paths"].pop()

        self._assert_soft_gate_fail3(_split_identity, label="split calendar day vs manifest.day")

        manifest = manifest_for_day("2026-09-20")
        manifest["decision_packet_paths"] = list(manifest["decision_packet_paths"][:-1])
        _, _, errors = project_day(manifest, day=split_day, fixture_origin="synthetic")
        self.assertTrue(errors)

    def _assert_soft_gate_fail4(self, mutate: object, *, label: str) -> None:
        batch = copy.deepcopy(example_two_day())
        mutate(batch)
        with self.subTest(label=label):
            self.assertTrue(_schema_errors(batch), label)
            self.assertTrue(validate_batch(batch), label)

    def test_soft_gate_fail4_expectation_rows_bound_on_day20_paths_fail_schema_and_cli(
        self,
    ) -> None:
        self._assert_soft_gate_fail4(
            lambda b: b["input"]["days"][0]["expectation"]["rows"][0].update(
                {"digest_n": 99}
            ),
            label="mutated digest_n on day-20 expectation row",
        )
        self._assert_soft_gate_fail4(
            lambda b: b["input"]["days"][0]["expectation"]["rows"][0].update(
                {"source_day": "2026-09-21"}
            ),
            label="mutated source_day on day-20 expectation row",
        )
        self._assert_soft_gate_fail4(
            lambda b: b["input"]["days"][0]["expectation"]["rows"][0].update(
                {"assembly_fingerprint": "phantom|fingerprint"}
            ),
            label="mutated assembly_fingerprint on day-20 expectation row",
        )
        self._assert_soft_gate_fail4(
            lambda b: b["input"]["days"][0]["expectation"]["rows"].pop(),
            label="dropped day-20 expectation row",
        )
        self._assert_soft_gate_fail4(
            lambda b: b["input"]["days"][0]["expectation"]["rows"].append(
                copy.deepcopy(b["input"]["days"][0]["expectation"]["rows"][0])
            ),
            label="extra day-20 expectation row",
        )

    def test_soft_gate_fail4_output_manifest_name_bound_fail_schema_and_cli(self) -> None:
        self._assert_soft_gate_fail4(
            lambda b: b["days"][0].__setitem__(
                "manifest_name", "decision-day-1999-01-01.json"
            ),
            label="phantom output manifest_name on day-20",
        )

    def test_soft_gate_fail4_day21_only_rollup_full_book_bound_fail_schema_and_cli(
        self,
    ) -> None:
        day = "2026-09-21"
        manifest = manifest_for_day(day)
        packets, _, errors = project_day(manifest, day=day, fixture_origin="synthetic")
        self.assertEqual(errors, [])
        spec = [
            {
                "day": day,
                "manifest_name": f"decision-day-{day}.json",
                "manifest": manifest,
                "expectation": expectation_from_packets(day, packets),
            }
        ]
        batch, problems = assemble(spec, fixture_origin="synthetic")
        self.assertEqual(problems, [])
        self.assertIsNotNone(batch)

        def _rollup_n(batch_doc: dict) -> None:
            batch_doc["rollup"]["n"] = 99

        def _packet_n(batch_doc: dict) -> None:
            batch_doc["rollup"]["packet_n"] = 99
            batch_doc["full_book"]["packet_n"] = 99

        for mutate, label in (
            (_rollup_n, "day-21-only rollup.n drift"),
            (_packet_n, "day-21-only rollup/full_book packet_n drift"),
        ):
            doc = copy.deepcopy(batch)
            mutate(doc)
            with self.subTest(label=label):
                self.assertTrue(_schema_errors(doc), label)
                self.assertTrue(validate_batch(doc), label)

    def test_soft_gate_fail1_cli_refuses_lexical_manifest_paths_before_fs(self) -> None:
        bad_paths = [
            "fixtures\\paper_laya_precompute_decision_packet_v0\\decision_non_fill_sim_surround.json",
            "fixtures/paper_laya_precompute_decision_packet_v0//decision_non_fill_sim_surround.json",
            "./fixtures/paper_laya_precompute_decision_packet_v0/decision_non_fill_sim_surround.json",
        ]
        for bad in bad_paths:
            with self.subTest(path=bad):
                manifest = manifest_for_day("2026-09-20")
                manifest["decision_packet_paths"] = [bad]
                _, _, errors = project_day(manifest, day="2026-09-20", fixture_origin="synthetic")
                self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
