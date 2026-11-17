"""Tests for tools/floor_exec_equiv.py on a synthetic decision log (repo DecisionLog, no host path, no network, no key)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools import floor_arena_executor as FA
from tools import floor_exec_equiv as E
from tools.arena_declog import DecisionLog

REPO = Path(__file__).resolve().parent.parent
T0 = 1_791_300_000_000


def meta() -> dict:
    return {"rule": FA.BOT_ID, "cell": FA.CELL, "hold_s": FA.L.HOLD_S, "tp": FA.L.TP, "v": (FA.V_LO + FA.V_HI) / 2, "age_s": (FA.L.AGE_LO + FA.L.AGE_HI) / 2}


def make_root(tmp: Path) -> Path:
    root = tmp / "arena"
    dl = DecisionLog(str(root))
    dl.append(E.BOT, T0, {"kind": "start", "binding": "proof", "frozen_sha": E.FROZEN_SHA, "rules": [E.BOT]})
    for i in range(3):
        dl.append(E.BOT, T0 + 1_000 + i, {"kind": "entry", "order_id": f"o{i}", "pool": f"P{i}", "meta": meta(), "decision_slot": 1000 + i, "stake_sol": FA.PAPER_STAKE_SOL, "attempt_key": None})
    return root


def tree_copy(tmp: Path, name: str) -> Path:
    t = tmp / name
    shutil.copytree(REPO, t, ignore=shutil.ignore_patterns(".git", ".claude", "__pycache__", "*.pyc"))
    return t


def run_cli(tmp: Path, a: Path, b: Path, root: Path) -> tuple[int, dict]:
    p = subprocess.run([sys.executable, "-m", "tools.floor_exec_equiv", "run", "--tree-a", str(a), "--tree-b", str(b), "--root-copy", str(root), "--out", str(tmp / "out")],
                       cwd=REPO, capture_output=True, text=True, timeout=600)
    return p.returncode, json.loads(p.stdout)


@pytest.fixture(scope="module")
def trees(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("equiv")
    return tmp, tree_copy(tmp, "a"), tree_copy(tmp, "b"), make_root(tmp)


def test_equal_trees_equal_then_planted_difference(trees):
    tmp, a, b, root = trees
    rc, res = run_cli(tmp, a, b, root)
    assert rc == 0 and res["equal"] is True and res["nonempty"] is True
    assert res["md5_a"] == res["md5_b"] and res["dropped_keys"] == []
    assert any(k.endswith(":buy_sent") for k in res["rows_by_kind_a"]), res["rows_by_kind_a"]
    # planted behaviour change in tree B only: the dry balance is 1 lamport, so buys are refused
    f = b / "tools" / "floor_lane_executor.py"
    s = f.read_text()
    assert "B.DryPort(cfg.dry_balance_lamports)" in s
    f.write_text(s.replace("B.DryPort(cfg.dry_balance_lamports)", "B.DryPort(1)"))
    rc, res = run_cli(tmp, a, b, root)
    assert rc == 1 and res["equal"] is False and res["md5_a"] != res["md5_b"]


def test_strip_drops_only_allowed_keys():
    a = [{"kind": "buy_sent", "x": 1}]
    b = [{"kind": "buy_sent", "x": 1, "stage_ms": {"a": 1}, "presend_batch": False, "exec_stage_ms": {}, "other_new": 5, "n": {"stage_z": 1}}]
    assert E.dropped_keys(a, b) == ["exec_stage_ms", "presend_batch", "stage_ms", "stage_z"]
    out = E.strip(b[0], set(E.dropped_keys(a, b)))
    assert out == {"kind": "buy_sent", "x": 1, "other_new": 5, "n": {}}
    assert E.dropped_keys(b, b) == []  # a key present in A is never dropped
    assert E.compare({"s": a}, {"s": b})["equal"] is False  # a non-allowed new key still makes the md5 differ
    assert E.compare({"s": a}, {"s": [{"kind": "buy_sent", "x": 1, "stage_ms": 3}]})["equal"] is True


def test_readers_accept_new_keys(capsys):
    rc = E.main(["readers"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["all_ok"] and len(out["readers"]) >= 4
    assert {"exec_stage_ms", "presend_batch", "stage_ms"} <= set(out["new_keys"])


def test_generated_config_has_no_host_path(tmp_path):
    work, root = tmp_path / "work", tmp_path / "root"
    cfg = E.gen_config(root, work / "state", work, T0 + 1, 1)
    paths = []

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, str) and o.startswith("/"):
            paths.append(o)

    walk(cfg)
    assert {"stop_file", "halt_file", "live_ok_path"} <= set(cfg) and "presend_batch" not in cfg
    assert paths and all(p.startswith((str(work), str(root))) for p in paths), paths


def _cli(args):
    r = subprocess.run([sys.executable, "-m", "tools.floor_exec_equiv", *args], cwd=REPO, capture_output=True, text=True, timeout=900)
    return r.returncode, json.loads(r.stdout)


def test_require_buy_and_config_derived(trees):
    tmp, a, _b, root = trees
    base = ["run", "--tree-a", str(a), "--tree-b", str(a), "--out", str(tmp / "out_rb"), "--require-buy"]
    rc, j = _cli(base + ["--root-copy", str(root)])
    assert rc == 0 and j["buy_sent_a"] >= 1 and j["config_presend_batch"] == "absent"
    empty = tmp / "empty"
    DecisionLog(str(empty)).append(E.BOT, T0, {"kind": "start", "binding": "proof", "frozen_sha": E.FROZEN_SHA, "rules": [E.BOT]})
    rc, j = _cli(base + ["--root-copy", str(empty)])
    assert rc == 1 and j.get("require_buy_failed") is True


def test_per_row(trees):
    tmp, a, _b, root = trees
    rc, j = _cli(["per-row", "--tree-a", str(a), "--tree-b", str(a), "--root-copy", str(root), "--out", str(tmp / "out_pr")])
    assert rc == 0 and j["all_ok"] and j["entry_rows"] == 3 and len(j["runs"]) == 4
    assert [r["outcome_a"] for r in j["runs"][:3]] == ["buy_sent"] * 3  # 100 SOL: the wallet floor never binds
    last = j["runs"][3]
    assert last["balance_lamports"] == 900_000_000 and last["equal"] and last["dropped_keys"] == []  # same tree both sides
