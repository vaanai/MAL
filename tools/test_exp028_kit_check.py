"""Tests for tools/exp028_kit_check.py (EXP-028 Amendment 1 item 5: the kit equivalence check). Synthetic data only; no block, no wave result, no sealed path.

Run with the pricing interpreter (numpy 2.5.3): /data/mal/venv/bin/python -m pytest tools/test_exp028_kit_check.py
The (B) tests skip when numpy is not 2.5.3 (the reference is defined under that version only) and the git-history tests skip when 489fa7e is not in the clone.
"""
from __future__ import annotations

import copy
import io
import json
import math
import re
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import pytest

from tools import exp028_kit_check as K

REPO = K.REPO_ROOT
REF_SHA = K.REFERENCE_SHA
REF_PATH = REPO / K.REFERENCE_REL
SIDECAR = REF_PATH.with_name(REF_PATH.name + ".sha256")
EXP_FILE = REPO / "EXP" / "EXP-028-hunt23-bank-part1-prereg.md"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args], check=True, capture_output=True, text=True).stdout.strip()


def _has_ref() -> bool:
    return subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", f"{REF_SHA}^{{commit}}"], capture_output=True).returncode == 0


def _numpy_ok() -> bool:
    try:
        import numpy

        return numpy.__version__ == K.REQUIRED_NUMPY
    except ImportError:
        return False


needs_ref = pytest.mark.skipif(not _has_ref(), reason="489fa7e is not in this clone")
needs_b = pytest.mark.skipif(not (_has_ref() and _numpy_ok()), reason=f"needs 489fa7e in the clone and numpy {K.REQUIRED_NUMPY}")


@pytest.fixture(scope="module")
def ref_doc() -> dict:
    return json.loads(REF_PATH.read_text())


# ------------------------------------------------------------------------------------------------------------------------------ a synthetic repo of kit variants
def _edit(src: str, old: str, new: str) -> str:
    assert src.count(old) == 1, old
    return src.replace(old, new)


VARIANTS = {
    "const": (K.ARENA_SCREEN, lambda s: _edit(s, "LAT_DECIDING = 1.9", "LAT_DECIDING = 1.8")),
    "helper": (K.ARENA_SCREEN, lambda s: _edit(s, "p_pct, p_null = (1 + c_pct) / (1 + B), (1 + c_null) / (1 + B)", "p_pct, p_null = c_pct / (1 + B), c_null / (1 + B)")),
    "unlisted_class": (K.ARENA_SCREEN, lambda s: _edit(s, '"""A guard refused: nothing was read or written."""', '"""A guard refused."""')),
    "unlisted_const": (K.ARENA_SCREEN, lambda s: _edit(s, 'SCREEN_P_SOURCE = "QP ruling', 'SCREEN_P_SOURCE = "A QP ruling')),
    "unlisted_other_file": (K.ARENA_STACK, lambda s: _edit(s, "RENT_REPORT = 2_039_280", "RENT_REPORT = 2_039_281")),
    "comment_only": (K.ARENA_SCREEN, lambda s: _edit(s, "def _max_p(ps: Sequence[float | None]) -> float | None:\n", "def _max_p(ps: Sequence[float | None]) -> float | None:  # a comment\n    # another comment\n")),
    "ghost_name": (K.ARENA_SCREEN, lambda s: _edit(s, "    return None if (not ps or any(p is None for p in ps)) else max(ps)", "    _ghost_helper(ps)\n    return None if (not ps or any(p is None for p in ps)) else max(ps)")),
    "bad_attribute": (K.ARENA_SCREEN, lambda s: _edit(s, "    return None if (not ps or any(p is None for p in ps)) else max(ps)", "    F.no_such_attribute\n    return None if (not ps or any(p is None for p in ps)) else max(ps)")),
    "legs": (K.ARENA_SCREEN, lambda s: _edit(s, "    pnl = pnl - extra\n", "    pnl = pnl - extra - 0.0\n")),
    "broken": (K.ARENA_SCREEN, lambda s: s + "\nraise RuntimeError('broken kit')\n"),
}


@pytest.fixture(scope="module")
def synth(tmp_path_factory) -> dict:
    """A throwaway repo whose base commit holds tools/*.py and observe/*.py of 489fa7e (non-test) and one commit per variant above."""
    if not _has_ref():
        pytest.skip("489fa7e is not in this clone")
    root = tmp_path_factory.mktemp("synth")
    names = [n for n in _git(REPO, "ls-tree", "-r", "--name-only", REF_SHA, "tools", "observe").splitlines() if re.fullmatch(r"(tools|observe)/[^/]+\.py", n) and not Path(n).name.startswith("test_")]
    tar = subprocess.run(["git", "-C", str(REPO), "archive", REF_SHA, "--", *names], check=True, capture_output=True).stdout
    tarfile.open(fileobj=io.BytesIO(tar)).extractall(root, filter="data")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base: tools of 489fa7e")
    out = {"base": _git(root, "rev-parse", "HEAD"), "root": root}
    for name, (path, fn) in VARIANTS.items():
        _git(root, "checkout", "-q", "--detach", out["base"])
        (root / path).write_text(fn((root / path).read_text()))
        _git(root, "commit", "-q", "-am", name)
        out[name] = _git(root, "rev-parse", "HEAD")
    return out


def _a(synth: dict, name: str, strict: bool = False, ref: str | None = None) -> dict:
    return K.check_a(synth["root"], synth[name], ref or synth["base"], strict)


# ------------------------------------------------------------------------------------------------------------------------------ the list of 22
def test_listed_names_are_quant_proofs_22_verbatim_from_the_amendment():
    text = EXP_FILE.read_text()
    block = text[text.index("(A) Source identity of the estimator closure"):]
    block = block[:block.index("any further kit-defined name")]
    s1 = block[block.index("in `tools/arena_screen.py`:"):block.index("in `tools/arena_stack.py`:")]
    s2 = block[block.index("in `tools/arena_stack.py`:"):]
    found = {K.ARENA_SCREEN: re.findall(r"`([A-Za-z_][A-Za-z_0-9]*)`", s1.split(":", 1)[1]), K.ARENA_STACK: re.findall(r"`([A-Za-z_][A-Za-z_0-9]*)`", s2.split(":", 1)[1])}
    assert {p: list(ns) for p, ns in K.LISTED} == found
    assert len(K.LISTED_KEYS) == 22
    assert K.REFERENCE_ARENA_SCREEN_SHA256 in text and K.REFERENCE_SHA in text and K.REQUIRED_NUMPY in text  # the pinned values are the amendment's


# ------------------------------------------------------------------------------------------------------------------------------ the reference file
def test_canon_is_sorted_repr_floats_and_numpy_safe():
    import numpy as np

    assert K.canon({"b": 1, "a": 0.1 + 0.2, "c": [float("nan"), float("inf"), None, True]}) == '{\n "a": 0.30000000000000004,\n "b": 1,\n "c": [\n  "nan",\n  "inf",\n  null,\n  true\n ]\n}\n'
    assert K.canon({"x": np.float64(0.1), "y": np.int64(3), "z": np.bool_(True)}) == K.canon({"x": 0.1, "y": 3, "z": True})
    with pytest.raises(TypeError):
        K.canon({"x": object()})


def test_reference_file_provenance_and_sidecar(ref_doc):
    text = REF_PATH.read_text()
    assert text == K.canon(ref_doc)  # the file is exactly its canonical form
    assert SIDECAR.read_text() == f"{K.sha256_hex(text)}  {REF_PATH.name}\n"
    assert ref_doc["schema"] == K.SCHEMA_REFERENCE and ref_doc["numpy_version"] == "2.5.3"
    assert ref_doc["git_head"] == ref_doc["kit_sha"] == REF_SHA and ref_doc["git_dirty"] is False
    assert ref_doc["kit_files_sha256"][K.ARENA_SCREEN] == K.REFERENCE_ARENA_SCREEN_SHA256
    assert ref_doc["outputs_sha256"] == K.sha256_hex(K.canon(ref_doc["outputs"]))
    assert ref_doc["fixture"]["inputs_sha256"] == K.inputs_sha256()
    assert ref_doc["fixture"]["seed"] == 1 and ref_doc["fixture"]["B"] == 10_000


def test_the_fixed_inputs_are_deterministic_and_cover_the_listed_paths(ref_doc):
    assert K.build_inputs() == K.build_inputs() and K.inputs_sha256() == K.inputs_sha256()
    sub, cells = ref_doc["fixture"]["sub_cases"], ref_doc["fixture"]["cell_cases"]
    for name, n, k in (("n0_empty", 0, 0), ("n1_single", 1, 1), ("nan_attempt", 120, 5), ("one_date", 100, 1), ("n2_both_positive", 2, 2), ("five_equal_positives", 5, 5),
                       ("negative_mean_400_8", 400, 8), ("right_skewed_300_7", 300, 7)):  # the eight cases of QP's item 5 (B)
        assert (sub[name]["n"], sub[name]["k_dates"]) == (n, k), name
    assert min(c["n"] for c in sub.values() if c["n"]) <= 5 and max(c["n"] for c in sub.values()) >= 3000  # n small and large
    assert min(c["k_dates"] for c in sub.values() if c["k_dates"]) == 1 and max(c["k_dates"] for c in sub.values()) >= 60  # dates few and many
    o = ref_doc["outputs"]["screen_p_sub"]
    assert o["n2_both_positive"]["p"] == o["five_equal_positives"]["p"] == 1 / 10_001  # QP's measured floor
    assert o["all_negative_300_6"]["p"] == 1.0 and o["all_positive_300_6"]["p"] == 1 / 10_001 and o["zeros_50_5"]["p"] == 1.0  # all-negative, all-positive, mean exactly 0
    assert all(o[n]["p"] is None for n in ("n0_empty", "n1_single", "nan_attempt", "inf_attempt"))  # the None cases
    assert o["one_date"]["date_cluster"]["p"] is None and o["one_date"]["p"] is not None
    assert o["ci_match"]["ci90_lo_first1000_equals_stat"] is True and o["ci_match"]["date_cluster"]["ci90_lo_first1000_equals_stat"] is True
    assert o["ci_perturbed"]["ci90_lo_first1000_equals_stat"] is False and o["right_skewed_300_7"]["ci90_lo_first1000_equals_stat"] is None
    assert any(o[n]["tail_count_pct"] != o[n]["tail_count_null"] for n in o if o[n]["p"] is not None)  # both tails exercised
    assert all(c["B"] == 10_000 for n, c in sub.items() if n != "skewed_B_min") and len(cells) >= 6
    c = ref_doc["outputs"]["screen_p_cell"]
    assert c["cell_n0"]["screen_p"]["cell_p"] is None and c["cell_nan_one_sub"]["screen_p"]["cell_p"] is None and c["cell_ok_300_7"]["screen_p"]["cell_p"] is not None
    assert [call[:3] for call in c["cell_ok_300_7"]["legs_calls"]] == [[0.25e9, 1.9, "total"], [0.25e9, 1.9, "sc"]]  # the stub saw the deciding stake and latency


# ------------------------------------------------------------------------------------------------------------------------------ (A)
@needs_ref
def test_a_489fa7e_against_itself_passes_and_lists_what_it_compared():
    a = K.check_a(REPO, REF_SHA, REF_SHA)
    assert a["verdict"] == "PASS" and a["differing"] == [] and a["unresolved_references"] == [] and a["listed_names_missing"] == [] and a["listed_names"] == 22
    unlisted = set(a["unlisted_transitive_names"])
    assert {"tools/arena_screen.py:Refused", "tools/arena_screen.py:SCREEN_P_SOURCE", "tools/arena_stack.py:RENT_REPORT"} <= unlisted  # followed, not skipped
    assert all(r["equal"] and r["sha256_at_sha"] == r["sha256_at_ref"] for r in a["compared"])
    assert [b["name"] for b in a["boundary_not_compared"]] == ["legs"] and a["boundary_not_compared"][0]["equal"] is True
    assert a["kit_files"][K.ARENA_SCREEN]["sha256_at_sha"] == K.REFERENCE_ARENA_SCREEN_SHA256
    assert K.check_a(REPO, REF_SHA, REF_SHA, strict_pricing=True)["verdict"] == "PASS"


def test_a_a_mutated_constant_fails(synth):
    a = _a(synth, "const")
    assert a["verdict"] == "FAIL" and "tools/arena_screen.py:LAT_DECIDING" in a["differing"]


def test_a_a_mutated_helper_fails(synth):
    a = _a(synth, "helper")
    assert a["verdict"] == "FAIL" and "tools/arena_screen.py:_tails" in a["differing"]


@pytest.mark.parametrize("variant,name", [("unlisted_class", "tools/arena_screen.py:Refused"), ("unlisted_const", "tools/arena_screen.py:SCREEN_P_SOURCE"),
                                          ("unlisted_other_file", "tools/arena_stack.py:RENT_REPORT")])
def test_a_a_mutated_unlisted_transitive_name_fails(synth, variant, name):
    a = _a(synth, variant)
    assert a["verdict"] == "FAIL" and a["differing"] == [name]  # the only difference is a name that is not among the 22
    assert name in a["unlisted_transitive_names"]


def test_a_comment_and_whitespace_changes_are_not_a_difference(synth):
    assert _a(synth, "comment_only")["verdict"] == "PASS"


def test_a_a_referenced_name_without_a_binding_fails_closed_even_when_both_shas_are_identical(synth):
    a = _a(synth, "ghost_name", ref=synth["ghost_name"])  # the same commit on both sides: nothing differs, but `_ghost_helper` cannot be compared
    assert a["differing"] == [] and a["verdict"] == "FAIL" and any("_ghost_helper" in u for u in a["unresolved_references"])
    b = _a(synth, "bad_attribute", ref=synth["bad_attribute"])
    assert b["differing"] == [] and b["verdict"] == "FAIL" and any("no_such_attribute" in u for u in b["unresolved_references"])


def test_a_a_sha_without_the_kit_file_fails_closed(synth):
    assert K.check_a(synth["root"], synth["base"], synth["base"])["verdict"] == "PASS"
    _git(synth["root"], "checkout", "-q", "--detach", synth["base"])
    _git(synth["root"], "rm", "-q", K.ARENA_STACK)
    _git(synth["root"], "commit", "-q", "-m", "no stack")
    gone = _git(synth["root"], "rev-parse", "HEAD")
    r = K.check_a(synth["root"], gone, synth["base"])
    assert r["verdict"] == "FAIL" and any("arena_stack.py" in u for u in r["unresolved_references"])


def test_a_legs_is_a_named_boundary_covered_by_c_and_strict_pricing_follows_it(synth):
    d = _a(synth, "legs")
    assert d["verdict"] == "PASS" and d["boundary_not_compared"][0]["name"] == "legs" and d["boundary_not_compared"][0]["equal"] is False  # shown, not compared
    s = _a(synth, "legs", strict=True)
    assert s["verdict"] == "FAIL" and s["differing"] == ["tools/arena_screen.py:legs"] and "tools/arena_stack.py:round_trip" in s["unlisted_transitive_names"]


# ------------------------------------------------------------------------------------------------------------------------------ (B)
@pytest.fixture(scope="module")
def worker_at_ref() -> dict:
    with K.worktree(REPO, REF_SHA) as wt:
        return K.run_worker(wt)


@needs_b
def test_b_489fa7e_against_itself_is_byte_identical_to_the_committed_reference(worker_at_ref, ref_doc):
    assert worker_at_ref["numpy_version"] == "2.5.3"
    b = K.compare_b(worker_at_ref, ref_doc, K.reference_problems(ref_doc, REPO, REF_SHA))
    assert b["verdict"] == "PASS" and b["outputs_byte_identical"] and b["numpy_equal_to_reference_and_required"] and b["inputs_sha256_equal"] and b["differing_sections"] == []
    assert K.canon(worker_at_ref["outputs"]) == K.canon(ref_doc["outputs"])


def _main(capsys, *argv: str) -> tuple[int, dict | None, str]:
    rc = K.main(list(argv))
    cap = capsys.readouterr()
    return rc, (json.loads(cap.out) if cap.out.strip() else None), cap.err


@needs_b
def test_check_cli_489fa7e_passes_a_and_b_and_exits_zero(capsys):
    rc, rec, _ = _main(capsys, "check", "--sha", REF_SHA[:12])
    assert rc == 0 and rec["verdicts"] == {"A": "PASS", "B": "PASS", "C": "NOT_RUN"} and rec["numpy_version"] == "2.5.3" and rec["ref_sha_is_pinned"] is True
    assert rec["sha"] == REF_SHA and rec["reference_file_sha256"] == K.sha256_hex(REF_PATH.read_bytes())


@needs_b
@pytest.mark.parametrize("how", ["value", "value_and_checksum", "numpy_version", "inputs", "extra_case"])
def test_b_a_perturbed_reference_fails(worker_at_ref, ref_doc, how):
    ref = copy.deepcopy(ref_doc)
    if how in ("value", "value_and_checksum"):
        p = ref["outputs"]["screen_p_sub"]["right_skewed_300_7"]["p"]
        ref["outputs"]["screen_p_sub"]["right_skewed_300_7"]["p"] = math.nextafter(p, math.inf)  # ONE ulp: a tolerance of rel 1e-9 in (B) would let this through
        assert ref["outputs"]["screen_p_sub"]["right_skewed_300_7"]["p"] != p and abs(ref["outputs"]["screen_p_sub"]["right_skewed_300_7"]["p"] / p - 1) < 1e-15
        if how == "value_and_checksum":
            ref["outputs_sha256"] = K.sha256_hex(K.canon(ref["outputs"]))  # the reference now agrees with itself; only the byte comparison can catch it
    elif how == "numpy_version":
        ref["numpy_version"] = "2.5.2"
    elif how == "inputs":
        ref["fixture"]["inputs_sha256"] = "0" * 64
    else:
        ref["outputs"]["screen_p_cell"]["cell_extra"] = {}
    b = K.compare_b(worker_at_ref, ref, K.reference_problems(ref, REPO, REF_SHA))
    assert b["verdict"] == "FAIL"
    if how == "value_and_checksum":
        assert b["reference_problems"] == [] and b["outputs_byte_identical"] is False and b["differing_sections"] == ["screen_p_sub/right_skewed_300_7"]
    if how == "value":
        assert any("outputs_sha256" in p for p in b["reference_problems"])


@needs_b
def test_b_a_different_numpy_at_the_sha_fails_even_with_equal_bytes(worker_at_ref, ref_doc):
    w = dict(worker_at_ref, numpy_version="2.5.2")
    b = K.compare_b(w, ref_doc, [])
    assert b["outputs_byte_identical"] is True and b["numpy_equal_to_reference_and_required"] is False and b["verdict"] == "FAIL"


@needs_b
def test_b_and_a_both_fail_on_a_mutated_helper_and_the_cli_still_exits_zero(synth, capsys):
    rc, rec, _ = _main(capsys, "check", "--sha", synth["helper"], "--ref-sha", synth["base"], "--repo", str(synth["root"]))
    assert rc == 0 and rec["verdicts"]["A"] == "FAIL" and rec["verdicts"]["B"] == "FAIL" and rec["ref_sha_is_pinned"] is False
    assert any(s.startswith("screen_p_sub/") for s in rec["B"]["differing_sections"]) and rec["B"]["outputs_byte_identical"] is False


@needs_b
def test_b_a_kit_that_cannot_run_fails_without_crashing(synth, ref_doc):
    b = K.check_b(synth["root"], synth["broken"], synth["base"], ref_doc)
    assert b["verdict"] == "FAIL" and "worker failed" in b["error"]


@needs_b
def test_b_a_comment_only_change_is_byte_identical(synth, ref_doc):
    b = K.check_b(synth["root"], synth["comment_only"], synth["base"], ref_doc)
    assert b["verdict"] == "PASS" and b["numpy_version"] == "2.5.3"


# ------------------------------------------------------------------------------------------------------------------------------ the shared repo
@needs_ref
def test_the_tool_never_runs_a_repo_wide_worktree_cleanup_and_touches_only_its_own_worktree(monkeypatch):
    assert "prune" not in Path(K.__file__).read_text()  # not as a command, not in a comment
    seen: list[list[str]] = []
    real = subprocess.run

    def rec(argv, *a, **k):
        seen.append([str(x) for x in argv])
        return real(argv, *a, **k)

    monkeypatch.setattr(subprocess, "run", rec)
    with K.worktree(REPO, REF_SHA) as wt:
        own = str(wt)
        assert own in _git(REPO, "worktree", "list", "--porcelain")
    monkeypatch.undo()
    cmds = [" ".join(c) for c in seen]
    assert not any("prune" in c for c in cmds)
    assert any(c.endswith(f"worktree add --detach {own} {REF_SHA}") for c in cmds) and any(c.endswith(f"worktree remove --force {own}") for c in cmds)
    removes = [c for c in cmds if " worktree remove" in c]
    assert removes and all(c.endswith(own) for c in removes)  # the only worktree it removes is its own path
    assert own not in _git(REPO, "worktree", "list", "--porcelain") and not Path(own).exists()


# ------------------------------------------------------------------------------------------------------------------------------ reference command
@needs_b
def test_reference_regenerates_the_committed_file_byte_for_byte(tmp_path, capsys):
    out = tmp_path / "p_reference_489fa7e.json"
    rc, info, _ = _main(capsys, "reference", "--out", str(out))
    assert rc == 0 and out.read_bytes() == REF_PATH.read_bytes() and out.with_name(out.name + ".sha256").read_text() == SIDECAR.read_text()
    assert info["numpy_version"] == "2.5.3" and info["git_dirty"] is False and info["git_head"] == REF_SHA


@needs_ref
def test_reference_refuses_a_wrong_head_a_dirty_tree_and_a_wrong_numpy(tmp_path, capsys, monkeypatch):
    out = tmp_path / "ref.json"
    rc, _, err = _main(capsys, "reference", "--kit-root", str(REPO), "--out", str(out))  # this checkout is not at 489fa7e
    assert rc == 2 and "REFUSED" in err and not out.exists()
    with K.worktree(REPO, REF_SHA) as wt:
        (wt / "tools" / "stray.py").write_text("x = 1\n")
        rc, _, err = _main(capsys, "reference", "--kit-root", str(wt), "--out", str(out))
        assert rc == 2 and "uncommitted" in err and not out.exists()
        (wt / "tools" / "stray.py").unlink()
        monkeypatch.setattr(K, "run_worker", lambda kit_root: {"numpy_version": "2.5.2", "python_version": "3", "inputs_sha256": "x", "outputs": {}, "fixture": {}})
        rc, _, err = _main(capsys, "reference", "--kit-root", str(wt), "--out", str(out))
        assert rc == 2 and "numpy 2.5.2" in err and not out.exists()


@needs_ref
def test_check_refuses_an_unknown_sha_with_exit_two(capsys):
    rc, rec, err = _main(capsys, "check", "--sha", "0123456789abcdef0123456789abcdef01234567")
    assert rc == 2 and rec is None and "not a commit" in err


# ------------------------------------------------------------------------------------------------------------------------------ (C)
def _results(ref_doc: dict, names: tuple[str, ...] = ("cell_ok_300_7", "cell_small_20_3")) -> dict:
    cells = {n: {"screen_p": copy.deepcopy(ref_doc["outputs"]["screen_p_cell"][n]["screen_p"]), "stats": copy.deepcopy(ref_doc["outputs"]["screen_p_cell"][n]["stats"]),
                 "kill": True, "kill_reasons": ["ci_lo <= 0 in total|flat"]} for n in names}
    return {"meta": {"screen_p": {"enabled": True, "report_only": True, "B": 10_000, "seed": 1}}, "cells": cells}


def _write(tmp_path: Path, doc: Any, name: str = "results.json") -> str:
    p = tmp_path / name
    p.write_text(json.dumps(doc, indent=1))
    return str(p)


def test_c_a_clean_results_pass_and_report_only_booleans_and_counts(tmp_path, ref_doc):
    c = K.check_c([_write(tmp_path, _results(ref_doc))])
    assert c["verdict"] == "PASS" and c["files"][0]["cells_checked"] == 2 and c["files"][0]["cells_failing"] == 0 and c["files"][0]["meta_B_10000_seed_1"] is True
    cell = c["files"][0]["cells"]["cell_ok_300_7"]
    assert cell["flags_true"] == 8 and cell["flags_all_true"] and cell["B_10000_everywhere"] and cell["seed_1_everywhere"] and cell["pass"]
    assert all(v == {"n_equal": True, "k_dates_equal": True, "xbar_equal_mean": True} for v in cell["sub_cells"].values())


def _fail_only(tmp_path, doc, cell="cell_ok_300_7"):
    c = K.check_c([_write(tmp_path, doc)])
    files = c["files"][0]
    assert c["verdict"] == "FAIL" and files["cells_failing"] == 1 and files["cells"][cell]["pass"] is False and files["cells"]["cell_small_20_3"]["pass"] is True
    return files["cells"][cell]


@pytest.mark.parametrize("path", [("sc|flat",), ("sc|press", "date_cluster"), ("total|flat",), ("total|press", "date_cluster")])
@pytest.mark.parametrize("bad", [False, None, "MISSING", "true", 1])
def test_c_one_flag_not_literally_true_fails_that_cell(tmp_path, ref_doc, path, bad):
    doc = _results(ref_doc)
    holder = doc["cells"]["cell_ok_300_7"]["screen_p"]["sub_cells"][path[0]]
    holder = holder["date_cluster"] if len(path) > 1 else holder
    if bad == "MISSING":
        del holder["ci90_lo_first1000_equals_stat"]
    else:
        holder["ci90_lo_first1000_equals_stat"] = bad
    cell = _fail_only(tmp_path, doc)
    assert cell["flags_true"] == 7 and cell["flags_all_true"] is False


@pytest.mark.parametrize("where", ["cell", "sub", "date_cluster", "meta"])
@pytest.mark.parametrize("bad", [1000, 10001, "10000", 10000.0, True])
def test_c_b_other_than_10000_anywhere_fails(tmp_path, ref_doc, where, bad):
    doc = _results(ref_doc)
    sp = doc["cells"]["cell_ok_300_7"]["screen_p"]
    if where == "meta":
        doc["meta"]["screen_p"]["B"] = bad
        c = K.check_c([_write(tmp_path, doc)])
        assert c["verdict"] == "FAIL" and c["files"][0]["meta_B_10000_seed_1"] is False
        return
    {"cell": sp, "sub": sp["sub_cells"]["total|flat"], "date_cluster": sp["sub_cells"]["sc|press"]["date_cluster"]}[where]["B"] = bad
    assert _fail_only(tmp_path, doc)["B_10000_everywhere"] is False


@pytest.mark.parametrize("where", ["cell", "sub", "date_cluster", "meta"])
@pytest.mark.parametrize("bad", [2, 0, True, 1.0])
def test_c_seed_other_than_1_anywhere_fails_and_true_is_not_1(tmp_path, ref_doc, where, bad):
    doc = _results(ref_doc)
    sp = doc["cells"]["cell_ok_300_7"]["screen_p"]
    if where == "meta":
        doc["meta"]["screen_p"]["seed"] = bad
        assert K.check_c([_write(tmp_path, doc)])["verdict"] == "FAIL"
        return
    {"cell": sp, "sub": sp["sub_cells"]["sc|flat"], "date_cluster": sp["sub_cells"]["total|press"]["date_cluster"]}[where]["seed"] = bad
    assert _fail_only(tmp_path, doc)["seed_1_everywhere"] is False


def test_c_a_mismatched_n_k_dates_or_xbar_fails_that_sub_cell(tmp_path, ref_doc):
    for field, expect in (("n", "n_equal"), ("k_dates", "k_dates_equal"), ("xbar", "xbar_equal_mean")):
        doc = _results(ref_doc)
        sub = doc["cells"]["cell_ok_300_7"]["screen_p"]["sub_cells"]["sc|press"]
        if field == "n":
            sub["n"] += 1
        elif field == "k_dates":
            sub["date_cluster"]["k_dates"] += 1
        else:
            sub["xbar"] += 1e-12
        cell = _fail_only(tmp_path, doc)
        assert cell["sub_cells"]["sc|press"][expect] is False and sum(not v for s in cell["sub_cells"].values() for v in s.values()) == 1


def test_c_the_kills_side_of_the_comparison_is_read_too(tmp_path, ref_doc):
    for field in ("n", "days", "mean"):
        doc = _results(ref_doc)
        st = doc["cells"]["cell_ok_300_7"]["stats"]["1.9|total|rent0|flat"]
        st[field] = st[field] + (1 if field != "mean" else 1e-12)
        assert _fail_only(tmp_path, doc)["pass"] is False


def test_c_a_cell_without_screen_p_or_stats_or_with_the_no_attempt_shape_fails(tmp_path, ref_doc):
    doc = _results(ref_doc)
    del doc["cells"]["cell_ok_300_7"]["screen_p"]  # a --no-screen-p run: no EXP-028 p
    assert _fail_only(tmp_path, doc)["flags_true"] == 0
    doc = _results(ref_doc)
    del doc["cells"]["cell_ok_300_7"]["stats"]
    assert _fail_only(tmp_path, doc)["flags_true"] == 8
    doc = _results(ref_doc, ("cell_n0", "cell_small_20_3"))  # n = 0: every flag None, stats absent
    cell = _fail_only(tmp_path, doc, "cell_n0")
    assert cell["flags_true"] == 0


def test_c_a_sub_cell_missing_fails_and_meta_missing_fails_and_unreadable_fails(tmp_path, ref_doc):
    doc = _results(ref_doc)
    del doc["cells"]["cell_ok_300_7"]["screen_p"]["sub_cells"]["total|press"]
    _fail_only(tmp_path, doc)
    doc = _results(ref_doc)
    del doc["meta"]["screen_p"]
    assert K.check_c([_write(tmp_path, doc)])["verdict"] == "FAIL"
    assert K.check_c([_write(tmp_path, {"meta": doc["meta"], "cells": {}})])["verdict"] == "FAIL"
    assert K.check_c([str(tmp_path / "nope.json")])["verdict"] == "FAIL"
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert K.check_c([str(bad)])["verdict"] == "FAIL"


def test_c_several_files_all_must_pass(tmp_path, ref_doc):
    good, bad = _results(ref_doc), _results(ref_doc)
    bad["cells"]["cell_ok_300_7"]["screen_p"]["sub_cells"]["sc|flat"]["ci90_lo_first1000_equals_stat"] = False
    c = K.check_c([_write(tmp_path, good, "a.json"), _write(tmp_path, bad, "b.json")])
    assert c["verdict"] == "FAIL" and [f["pass"] for f in c["files"]] == [True, False]


# ------------------------------------------------------------------------------------------------------------------------------ the printer
def _floats(o: Any):
    if isinstance(o, float):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from _floats(v)
    elif isinstance(o, list):
        for v in o:
            yield from _floats(v)


@needs_ref
def test_the_printer_never_emits_a_mean_total_p_or_kill_value(tmp_path, ref_doc, capsys, monkeypatch):
    doc = _results(ref_doc)
    doc["cells"]["cell_ok_300_7"]["screen_p"]["sub_cells"]["sc|flat"]["xbar"] = 0.123456789012345  # a mismatch: n/k/xbar are compared silently
    doc["cells"]["cell_ok_300_7"]["stats"]["1.9|sc|rent0|flat"].update(mean=0.987654321098765, total=0.55555555555555, ex_top3=-0.31415926535, ex_best_day=0.271828182845)
    doc["cells"]["cell_ok_300_7"]["screen_p"]["cell_p"] = 0.000999900009999
    doc["cells"]["cell_ok_300_7"]["kill_reasons"] = ["ex-top-3 total -0.31415926535 <= 0"]
    secret_values = {repr(x) for x in _floats(doc) if len(repr(x)) >= 8} | {"ex-top-3 total", "kill_reasons"}
    assert len(secret_values) > 50
    monkeypatch.setattr(K, "check_b", lambda *a, **k: {"verdict": "PASS"})  # (B) has its own tests; here only the (C) printer
    rc, rec, _ = _main(capsys, "check", "--sha", REF_SHA, "--results", _write(tmp_path, doc))
    text = K.canon(rec)
    assert rc == 0 and rec["verdicts"] == {"A": "PASS", "B": "PASS", "C": "FAIL"}
    assert rec["C"]["files"][0]["cells"]["cell_ok_300_7"]["sub_cells"]["sc|flat"] == {"n_equal": True, "k_dates_equal": True, "xbar_equal_mean": False}
    assert not any(v in text for v in secret_values)  # no value of the results file reaches the output
    assert list(_floats(rec)) == []  # and the whole record holds no float at all
    good = _write(tmp_path, _results(ref_doc), "good.json")
    rc, rec, _ = _main(capsys, "check", "--sha", REF_SHA, "--results", good)
    assert rec["verdicts"]["C"] == "PASS" and list(_floats(rec)) == []
    assert not any(repr(x) in K.canon(rec) for x in _floats(_results(ref_doc)) if len(repr(x)) >= 8)
