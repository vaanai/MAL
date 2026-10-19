"""arena_port_check tests: a toy kit rule and its hand-written streaming twin agree on a synthetic arena-format tape (PASS), and each injected defect (an off-by-one decision slot,
a dropped pool, a cooldown mismatch, no pool-age refusal, an undeclared hold, a non-deterministic port, a port that raises) is a FAIL with the right diff. Every path refusal.
No network, no real tape, no arena path, no ledger. Python: /data/mal/venv/bin/python -m pytest -q tools/test_arena_port_check.py"""

from __future__ import annotations

import inspect
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import arena_port_check as M
from tools import arena_screen as S
from tools.arena_paper import ArenaRefused
from tools.arena_port_check_testkit import ToyKit, ToyPort, write_toy_tape
from tools.arena_rules import RULE_MODULES, position_id
from tools.arena_rules_testkit import POOL

KIT = "tools.arena_port_check_testkit:ToyKit"
PORT = "tools.arena_port_check_testkit"


@pytest.fixture(scope="module")
def tape(tmp_path_factory) -> list[Path]:
    return write_toy_tape(tmp_path_factory.mktemp("pc") / "tape")


def check(tmp_path: Path, tape: list[Path], port: dict | None = None, kit: dict | None = None, **kw) -> dict:
    return M.run_check(KIT, PORT, tape, tmp_path / "out", kit_kwargs=json.dumps(kit) if kit else None, port_kwargs=json.dumps(port) if port else None, **kw)


@pytest.fixture(scope="module")
def baseline(tape, tmp_path_factory) -> dict:
    return check(tmp_path_factory.mktemp("base"), tape)


# ------------------------------------------------------------------------------------------------------------------------------ PASS
def test_matching_port_passes_with_equal_md5_on_every_hour_and_a_deterministic_replay(baseline):
    d = baseline
    assert d["verdict"] == "PASS" and d["reasons"] == []
    assert len(d["per_hour"]) == 2 and d["totals"]["hours_matching"] == 2
    for h, v in d["per_hour"].items():
        assert v["n_kit"] == v["n_port"] > 0 and v["md5_kit"] == v["md5_port"] and v["match"], h
    assert d["determinism"]["identical"] and d["determinism"]["decision_log_md5_a"] == d["determinism"]["decision_log_md5_b"]
    assert d["differences_first_20"] == [] and d["port_hook_errors"] == 0
    assert d["kit_counters"]["refused_pool_age_lt_360s"] == 4  # the first two triggers of the young pools A and E: the kit refused them and the twin did too (else the md5s would differ)


def test_the_record_carries_inputs_head_shas_and_counts_but_no_price(baseline, tape, tmp_path):
    d = baseline
    assert set(d) >= {"schema", "verdict", "reasons", "inputs", "provenance", "per_hour", "totals", "differences_first_20", "determinism", "kit_counters", "scope"}
    pv = d["provenance"]
    assert pv["git_head"] and len(pv["git_head"]) == 40 and isinstance(pv["git_dirty"], bool)
    for k in ("tool_sha256", "arena_screen_sha256", "arena_rules_sha256", "kit_rule_sha256", "port_rule_sha256"):
        assert len(pv[k]) == 64, k
    assert [f["hour"] for f in d["inputs"]["hour_files"]] == ["2026-09-25T06", "2026-09-25T07"]
    assert all(len(f["sha256"]) == 64 for f in d["inputs"]["hour_files"])
    keys: set[str] = set()

    def walk_keys(x) -> None:
        if isinstance(x, dict):
            keys.update(x)
            for v in x.values():
                walk_keys(v)
        elif isinstance(x, list):
            for v in x:
                walk_keys(v)

    walk_keys({k: v for k, v in d.items() if k not in ("per_hour",)} | {"per_hour": list(d["per_hour"].values())})
    assert not any("pnl" in k or "price" in k or "lamport" in k for k in keys) and "not a profit claim" in d["scope"]


def test_the_record_is_written_to_the_out_dir(tape, tmp_path):
    d = check(tmp_path, tape)
    on_disk = json.loads((tmp_path / "out" / "port_check.json").read_text())
    assert on_disk == d and (tmp_path / "out" / "scratch-a" / "rules" / "decisions").is_dir() and (tmp_path / "out" / "scratch-b" / "rules").is_dir()


def test_tp_sl_trail_declared_equal_on_both_sides_pass_and_an_event_exit_flag_the_kit_lacks_fails(tape, tmp_path):
    exits = {"tp": 2.0, "sl": 0.5, "trail": 0.6}
    ok = check(tmp_path / "ok", tape, port=exits, kit=exits)
    assert ok["verdict"] == "PASS" and ok["totals"]["n_kit"] > 0
    bad = check(tmp_path / "bad", tape, port={**exits, "event_exit": True}, kit=exits)
    assert bad["verdict"] == "FAIL" and bad["totals"]["fields_differ"] == bad["totals"]["n_kit"]
    assert all(x["kind"] == "fields_differ" and x["fields"] == ["event_exit"] for x in bad["differences_first_20"])
    wrong = check(tmp_path / "wrong", tape, port={"tp": 3.0}, kit={"tp": 2.0})
    assert wrong["verdict"] == "FAIL" and wrong["differences_first_20"][0]["fields"] == ["tp"]


def test_a_cell_map_renames_the_port_cell_to_the_kit_cell(tape, tmp_path):
    no_map = check(tmp_path / "a", tape, port={"cell": "port-cell"})
    assert no_map["verdict"] == "FAIL" and no_map["totals"]["only_kit"] == no_map["totals"]["only_port"] == no_map["totals"]["n_kit"]
    mapped = check(tmp_path / "b", tape, port={"cell": "port-cell"}, cell_map={"port-cell": "toy"})
    assert mapped["verdict"] == "PASS"


def test_a_cell_less_port_is_keyed_by_its_bot_id(tape, tmp_path):
    """meta.cell absent: the port's cell is its bot_id (here the kit cell is renamed to match)."""
    d = M.run_check(KIT, PORT, tape, tmp_path / "out", kit_kwargs=json.dumps({"cell": "toy-port"}), port_kwargs=json.dumps({"cell": ""}))
    assert d["verdict"] == "PASS"


def test_zst_hours_give_the_same_md5s_as_plain_hours(tape, baseline, tmp_path):
    if shutil.which("zstd") is None:
        pytest.skip("zstd CLI missing")
    z = tmp_path / "z"
    z.mkdir()
    for p in tape:
        with open(z / (p.name + ".zst"), "wb") as fh:
            subprocess.run(["zstd", "-q", "-c", str(p)], stdout=fh, check=True)
    d = M.run_check(KIT, PORT, sorted(z.glob("*.zst")), tmp_path / "out")
    assert d["verdict"] == "PASS"
    assert {h: v["md5_kit"] for h, v in d["per_hour"].items()} == {h: v["md5_kit"] for h, v in baseline["per_hour"].items()}


def test_a_single_hour_is_enough(tape, tmp_path):
    d = M.run_check(KIT, PORT, tape[:1], tmp_path / "out")
    assert d["verdict"] == "PASS" and list(d["per_hour"]) == ["2026-09-25T06"]


# ------------------------------------------------------------------------------------------------------------------------------ FAIL with the right diff
def test_off_by_one_decision_slot_fails_on_every_entry(tape, baseline, tmp_path):
    d = check(tmp_path, tape, kit={"slot_offset": 1})
    t = d["totals"]
    assert d["verdict"] == "FAIL" and t["n_kit"] == t["n_port"] == baseline["totals"]["n_kit"]  # same number of entries, none on the same slot
    assert t["only_kit"] == t["only_port"] == t["n_kit"] and t["fields_differ"] == t["count_differs"] == 0
    assert not any(v["match"] for v in d["per_hour"].values()) and "differ on 2 of 2 hours" in d["reasons"][0]
    assert len(d["differences_first_20"]) == 20 and d["differences_truncated"] == t["diff_keys"] - 20


def test_off_by_one_slot_pairs_up_key_by_key(tape, tmp_path):
    kit = M.kit_decisions(ToyKit(slot_offset=1), M.check_hour_files(tape))[0]
    base = M.kit_decisions(ToyKit(), M.check_hour_files(tape))[0]
    h = "2026-09-25T06"
    diffs = M.compare_lines(kit[h], base[h], h)
    only_kit = {(d["pool"], d["slot"]) for d in diffs if d["kind"] == "only_kit"}
    only_port = {(d["pool"], d["slot"]) for d in diffs if d["kind"] == "only_port"}
    assert only_kit and {(p, s - 1) for p, s in only_kit} == only_port  # exactly one slot apart


def test_a_dropped_pool_fails_with_only_kit_diffs_on_that_pool(tape, baseline, tmp_path):
    d = check(tmp_path, tape, port={"skip_pools": [POOL["A"]]})
    t = d["totals"]
    assert d["verdict"] == "FAIL" and t["only_kit"] > 0 and t["only_port"] == t["count_differs"] == t["fields_differ"] == 0
    assert all(x["kind"] == "only_kit" and x["pool"] == POOL["A"] for x in d["differences_first_20"])
    assert t["n_port"] == baseline["totals"]["n_kit"] - t["only_kit"]  # the other pools still agree


def test_a_cooldown_mismatch_fails_with_only_port_entries(tape, tmp_path):
    cfg = {"every": 12}  # a trigger every 96 s: the second one falls inside hold + cooldown (120 s) after the first
    ok = check(tmp_path / "ok", tape, port=cfg, kit=cfg)
    assert ok["verdict"] == "PASS" and ok["kit_counters"]["overlap_dropped"] > 0  # control: the cooldown is exercised, and a port that honours it matches
    bad = check(tmp_path / "bad", tape, port={**cfg, "cooldown_blind": True}, kit=cfg)
    t = bad["totals"]
    assert bad["verdict"] == "FAIL" and t["only_port"] > 0 and t["only_kit"] == t["count_differs"] == t["fields_differ"] == 0
    assert t["n_port"] == t["n_kit"] + t["only_port"] and all(x["kind"] == "only_port" for x in bad["differences_first_20"])


def test_the_kit_counters_add_up_to_the_kit_decisions(tape, tmp_path):
    cfg = {"every": 12}
    d = check(tmp_path, tape, port=cfg, kit=cfg)
    c = d["kit_counters"]
    refused = sum(v for k, v in c.items() if k.startswith("refused_"))
    assert c["triggers_raw"] - refused - c["overlap_dropped"] == d["totals"]["n_kit"]


def test_a_port_without_the_pool_age_refusal_fails_on_the_young_pools(tape, tmp_path):
    d = check(tmp_path, tape, port={"age_blind": True})
    assert d["verdict"] == "FAIL" and d["totals"]["only_port"] == 4 and d["totals"]["only_kit"] == 0  # A and E, two triggers each, inside their first 360 s
    assert {x["pool"] for x in d["differences_first_20"]} == {POOL["A"], POOL["E"]}


def test_an_undeclared_hold_is_a_field_difference_named_hold_s(tape, tmp_path):
    d = check(tmp_path, tape, port={"no_hold_meta": True})
    t = d["totals"]
    assert d["verdict"] == "FAIL" and t["fields_differ"] == t["n_kit"] == t["n_port"] and t["only_kit"] == t["only_port"] == 0
    assert all(x["fields"] == ["hold_s"] for x in d["differences_first_20"])


def test_a_non_deterministic_port_fails_on_the_two_replays_even_when_the_lines_match(tape, tmp_path):
    d = check(tmp_path, tape, port={"nondeterministic": True})
    assert d["verdict"] == "FAIL" and all(v["match"] for v in d["per_hour"].values())  # the entry lines agree
    assert not d["determinism"]["identical"] and d["determinism"]["decision_log_md5_a"] != d["determinism"]["decision_log_md5_b"]
    assert any("not deterministic" in r for r in d["reasons"])


def test_a_port_that_raises_is_a_fail_with_the_hook_errors_counted(tape, tmp_path):
    d = check(tmp_path, tape, port={"raise_in_on_row": True})
    assert d["verdict"] == "FAIL" and d["port_hook_errors"] > 0 and d["totals"]["n_port"] == 0
    assert any("hook error" in r for r in d["reasons"])


def test_two_empty_sides_are_not_a_pass(tape, tmp_path):
    cfg = {"every": 100_000}
    d = check(tmp_path, tape, port=cfg, kit=cfg)
    assert d["totals"]["n_kit"] == d["totals"]["n_port"] == 0 and d["verdict"] == "FAIL" and any("vacuous" in r for r in d["reasons"])
    ok = check(tmp_path / "again", tape, port=cfg, kit=cfg, min_decisions=0)
    assert ok["verdict"] == "PASS"  # only when the caller says an empty comparison is acceptable


def test_the_md5_is_over_sorted_canonical_lines():
    a, b = M.make_line("P", 5, "c", 60, None, None, None, False), M.make_line("P", 4, "c", 60.0, None, None, None, False)
    assert M.md5_lines([a, b]) == M.md5_lines([b, a]) and M.render(a) == "P|5|c|60|-|-|-|0" and M.render(a)[:3] == "P|5"
    assert M.md5_lines([a]) != M.md5_lines([M.make_line("P", 6, "c", 60, None, None, None, False)])
    assert M.make_line("P", 5, "c", 60, None, None, None, False) == M.make_line("P", 5, "c", 60.0, None, None, None, False)  # 60 and 60.0 are the same hold


def test_compare_lines_reports_keys_and_field_names_only():
    k = [M.make_line("P", 1, "c", 60, 2.0, None, None, False), M.make_line("P", 2, "c", 60, None, None, None, False), M.make_line("Q", 3, "c", 60, None, None, None, False)]
    p = [M.make_line("P", 1, "c", 60, 3.0, None, None, False), M.make_line("Q", 3, "c", 60, None, None, None, False), M.make_line("Q", 3, "c", 61, None, None, None, False),
         M.make_line("R", 9, "c", 60, None, None, None, False)]
    d = {(x["pool"], x["slot"]): x for x in M.compare_lines(k, p, "h")}
    assert d[("P", 1)]["kind"] == "fields_differ" and d[("P", 1)]["fields"] == ["tp"]
    assert d[("P", 2)]["kind"] == "only_kit" and d[("R", 9)]["kind"] == "only_port" and d[("Q", 3)]["kind"] == "count_differs"
    assert all(set(x) == {"hour", "pool", "slot", "cell", "kind", "n_kit", "n_port", "fields"} for x in d.values())


# ------------------------------------------------------------------------------------------------------------------------------ the pieces
def test_the_recorder_copies_the_kits_overlap_loop_and_nothing_changes_it_silently():
    src = inspect.getsource(S.Screen._price_store)
    for line in ("if slot < nxt:", 'c["overlap_dropped"] += 1', "xs, why, clamped = resolve_exit(a, pp, slot, spec, rate[hi], sps_of(hi))",
                 "nxt = xs + int(np.rint(self.cells[ci].cooldown_s * rate[hi]))", "sorted((t for t in s.trigs if t[0] == ci), key=lambda t: t[1])"):
        assert line in src, line  # the loop in DecisionScreen._price_store is a copy of these lines: if the kit changes one, re-copy and re-prove
    mine = inspect.getsource(M.DecisionScreen._price_store)
    assert "S.resolve_exit(a, pp, slot, spec, rate[hi], 1.0 / rate[hi])" in mine and "int(np.rint(self.cells[ci].cooldown_s * rate[hi]))" in mine
    assert S.Screen._price_store is not M.DecisionScreen._price_store and M.DecisionScreen.run_block is S.Screen.run_block and M.DecisionScreen._validate is S.Screen._validate


def test_decision_only_engine_surface():
    e = M.DecisionOnlyEngine()
    pid = e.buy("b", "pool", 0.25, 1_000, 7, {"x": 1})
    assert pid == position_id("b", "pool", 1_000, 7) and pid in e.positions and e.positions[pid].decision_slot == 7
    e.sell("b", pid, 1.0, 2_000, 9, "r")
    with pytest.raises(ArenaRefused, match="no open position"):
        e.sell("b", pid, 1.0, 3_000, 9, "r")
    assert e.recover() == 0 and e.start_record("b") is False


def test_a_registered_name_loads_through_the_hosts_own_loader():
    name = sorted(RULE_MODULES)[0]
    rules, srcs = M.load_port(name)
    assert [r.bot_id for r in rules] == [name] and srcs[0].name == "arena_rule_h2ps.py"


def test_a_factory_may_return_several_books():
    rules, _ = M.load_port("tools.arena_port_check_testkit:make_rule", json.dumps({"bot_id": "x1"}))
    assert [r.bot_id for r in rules] == ["x1"] and isinstance(rules[0], ToyPort)
    with pytest.raises(M.PortCheckRefused, match="port_not_loadable"):
        M.load_port("tools.arena_port_check_testkit:no_such_factory")
    with pytest.raises(M.PortCheckRefused, match="port_not_a_rule"):
        M.load_port("tools.arena_port_check_testkit:ToyKit")  # a kit rule is not a streaming Rule


def test_the_seal_and_the_kits_seal_are_the_same_window():
    assert M.SEALED_HOURS == S.SEALED_HOURS == (datetime(2026, 10, 2, 10, tzinfo=timezone.utc), datetime(2026, 10, 16, 1, tzinfo=timezone.utc))
    for w in ("forward-", "truth-", "fresh-08", "exp011-", "exp012-forward", "tip-tape", "/var/lib/mal/sealed", "shadow", "canary", "probe", "walk", "_shakedown", "kalshi"):
        assert w in M.FORBIDDEN


# ------------------------------------------------------------------------------------------------------------------------------ refusals
def touch(d: Path, hour: str, suffix: str = ".jsonl") -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"tape-{hour}{suffix}"
    p.write_text("")
    return p


@pytest.mark.parametrize("word", list(M.FORBIDDEN))
def test_a_forbidden_word_in_an_hour_path_is_refused(word, tmp_path):
    d = tmp_path.joinpath(*(f"a{word}b" if "/" not in word else word.strip("/")).split("/"))
    p = touch(d, "2026-09-25T06")
    with pytest.raises(M.PortCheckRefused, match="path_forbidden_word"):
        M.check_hour_files([p])


@pytest.mark.parametrize("word", list(M.FORBIDDEN))
def test_a_forbidden_word_in_the_out_path_is_refused(word, tmp_path):
    out = tmp_path.joinpath(*(f"a{word}b" if "/" not in word else word.strip("/")).split("/"))
    with pytest.raises(M.PortCheckRefused, match="path_forbidden_word"):
        M.check_out_dir(out)
    assert not out.exists()


def test_a_forbidden_word_in_a_rule_spec_or_source_is_refused(tape, tmp_path):
    bad = tmp_path / "probe-rule"
    bad.mkdir()
    (bad / "r.py").write_text("class F:\n    pass\n")
    with pytest.raises(M.PortCheckRefused, match="path_forbidden_word"):
        M.run_check(f"{bad / 'r.py'}:F", PORT, tape, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_a_symlink_into_a_forbidden_tree_is_refused_by_its_resolved_path(tmp_path):
    real = touch(tmp_path / "walk-area", "2026-09-25T06")
    link_dir = tmp_path / "clean"
    link_dir.mkdir()
    (link_dir / real.name).symlink_to(real)
    with pytest.raises(M.PortCheckRefused, match="path_forbidden_word:walk"):
        M.check_hour_files([link_dir / real.name])


def test_the_out_dir_may_not_be_inside_the_arena_tree_or_the_clean_view(tmp_path, monkeypatch):
    arena, cv = tmp_path / "arena-root", tmp_path / "cv-root"
    monkeypatch.setattr(M, "ARENA_ROOT", str(arena))
    monkeypatch.setattr(M, "CLEAN_VIEW", str(cv))
    for out in (arena, arena / "scratch", cv / "x"):
        with pytest.raises(M.PortCheckRefused, match="out_dir_inside_protected_tree"):
            M.check_out_dir(out)
    assert not arena.exists() and not cv.exists()
    assert M.check_out_dir(tmp_path / "fine") == (tmp_path / "fine").resolve()


def test_the_out_dir_must_be_fresh(tape, tmp_path):
    check(tmp_path, tape)
    with pytest.raises(M.PortCheckRefused, match="out_not_empty"):
        check(tmp_path, tape)  # a second run into the same tree would chain onto the first run's decision logs


def test_an_arena_path_other_than_a_closed_hour_in_the_tape_dir_is_refused(tmp_path, monkeypatch):
    root = tmp_path / "arenaroot"
    monkeypatch.setattr(M, "ARENA_ROOT", str(root))
    monkeypatch.setattr(M, "ARENA_TAPE", str(root / "tape"))
    closed = touch(root / "tape", "2026-10-19T09", ".jsonl.zst")
    assert M.check_hour_files([closed])[0][1] == closed  # a closed hour directly in the tape dir
    for bad in (touch(root / "tape", "2026-10-19T10", ".jsonl"), touch(root / "ledger", "2026-10-19T11", ".jsonl.zst"), touch(root / "tape-sub" / "x", "2026-10-19T12", ".jsonl.zst")):
        with pytest.raises(M.PortCheckRefused, match="arena_path_refused"):
            M.check_hour_files([bad])


def test_a_zst_with_its_jsonl_still_there_is_not_closed(tmp_path):
    z = touch(tmp_path, "2026-09-25T06", ".jsonl.zst")
    touch(tmp_path, "2026-09-25T06", ".jsonl")
    with pytest.raises(M.PortCheckRefused, match="hour_not_closed"):
        M.check_hour_files([z])


@pytest.mark.parametrize("hour,refused", [("2026-10-02T09", False), ("2026-10-02T10", True), ("2026-10-09T12", True), ("2026-10-16T00", True), ("2026-10-16T01", False),
                                          ("2026-10-19T08", False)])
def test_the_d2_d3_seal_window_is_refused(hour, refused, tmp_path):
    p = touch(tmp_path, hour)
    if refused:
        with pytest.raises(M.PortCheckRefused, match="sealed_hour"):
            M.check_hour_files([p])
    else:
        assert [t for t, _ in M.check_hour_files([p])] == [datetime.strptime(hour, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)]


def test_one_sealed_hour_among_good_ones_refuses_the_whole_list(tmp_path):
    ps = [touch(tmp_path, h) for h in ("2026-10-01T23", "2026-10-02T00", "2026-10-02T10")]
    with pytest.raises(M.PortCheckRefused, match="sealed_hour"):
        M.check_hour_files(ps)


def test_hours_must_be_consecutive_unique_and_well_named(tmp_path):
    a, b, c = (touch(tmp_path, h) for h in ("2026-09-25T06", "2026-09-25T07", "2026-09-25T09"))
    assert len(M.check_hour_files([b, a])) == 2 and [t.hour for t, _ in M.check_hour_files([b, a])] == [6, 7]  # sorted
    with pytest.raises(M.PortCheckRefused, match="hours_not_consecutive"):
        M.check_hour_files([a, c])
    other = touch(tmp_path / "o", "2026-09-25T06")
    with pytest.raises(M.PortCheckRefused, match="duplicate_hour"):
        M.check_hour_files([a, other])
    with pytest.raises(M.PortCheckRefused, match="not_an_arena_hour_file"):
        M.check_hour_files([tmp_path / "trades-2026-09-25T06.jsonl.zst"])
    with pytest.raises(M.PortCheckRefused, match="hour_file_missing"):
        M.check_hour_files([tmp_path / "tape-2026-09-25T10.jsonl"])
    with pytest.raises(M.PortCheckRefused, match="no_hours"):
        M.check_hour_files([])


def test_a_rule_that_names_a_banned_model_family_is_refused_before_it_runs(tape, tmp_path):
    d = tmp_path / "rules"
    d.mkdir()
    (d / "r.py").write_text("# a model of the " + "la" + "ya" + " family\nclass F:\n    pass\n")
    with pytest.raises(M.PortCheckRefused, match="rule_module_banned"):
        M.run_check(f"{d / 'r.py'}:F", PORT, tape, tmp_path / "out")
    assert not (tmp_path / "out").exists()


# ------------------------------------------------------------------------------------------------------------------------------ CLI
def test_cli_exit_codes_pass_fail_refused(tape, tmp_path, capsys):
    args = ["--kit-rule", KIT, "--port", PORT, "--hours", *map(str, tape)]
    assert M.main([*args, "--out", str(tmp_path / "o1")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "PASS" and out["totals"]["n_kit"] == out["totals"]["n_port"] > 0 and "pnl" not in json.dumps(out)
    assert M.main([*args, "--out", str(tmp_path / "o2"), "--kit-kwargs", '{"slot_offset": 1}']) == 1
    assert json.loads(capsys.readouterr().out)["verdict"] == "FAIL"
    assert M.main([*args[:-2], str(tmp_path / "missing" / "tape-2026-09-25T06.jsonl"), "--out", str(tmp_path / "o3")]) == 2
    assert "REFUSED: hour_file_missing" in capsys.readouterr().err and not (tmp_path / "o3").exists()
