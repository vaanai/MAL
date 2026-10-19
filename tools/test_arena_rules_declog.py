"""arena_declog: the copied DecisionLog / LogChainError / GENESIS are byte-for-byte hv4_runner's, and behave like it. No network, no tape."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from tools import arena_declog as D

TOOLS = Path(__file__).resolve().parent


def _segments(path: Path, names: tuple[str, ...]) -> dict[str, str]:
    src = path.read_text()
    out: dict[str, str] = {}
    for n in ast.parse(src).body:
        if isinstance(n, ast.ClassDef) and n.name in names:
            out[n.name] = ast.get_source_segment(src, n)
        elif isinstance(n, ast.Assign) and getattr(n.targets[0], "id", None) in names:
            out[n.targets[0].id] = ast.get_source_segment(src, n)
    return out


def test_copied_source_text_is_identical_to_hv4_runner():
    names = ("GENESIS", "LogChainError", "DecisionLog")
    mine, theirs = _segments(TOOLS / "arena_declog.py", names), _segments(TOOLS / "hv4_runner.py", names)
    assert set(mine) == set(theirs) == set(names)
    for n in names:
        assert mine[n] == theirs[n], n  # byte for byte, not "equivalent"


def test_helpers_match_the_originals_they_were_copied_from():
    from tools.hv4_budget import utc_day
    from tools.hv4_prompts import sha256_hex

    for ms in (0, 1_790_316_000_000, 1_790_316_000_999, 1_790_402_399_999):
        assert D.utc_day(ms) == utc_day(ms)
    for x in ("", "abc", b"abc", "é"):
        assert D.sha256_hex(x) == sha256_hex(x)


def test_arena_declog_does_not_import_hv4_runner_or_model_code():
    tree = ast.parse((TOOLS / "arena_declog.py").read_text())
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not [m for m in mods if "hv4" in m or "haiku" in m or "anthropic" in m]


def test_log_chain_roundtrip_verify_and_tamper(tmp_path):
    log = D.DecisionLog(tmp_path)
    assert log.head("b1") == (D.GENESIS, -1)
    r0 = log.append("b1", 1_790_316_000_000, {"kind": "entry", "x": 1})
    r1 = log.append("b1", 1_790_316_001_000, {"kind": "exit", "x": 2})
    assert (r0["seq"], r1["seq"], r1["prev"]) == (0, 1, r0["sha"])
    fresh = D.DecisionLog(tmp_path)
    assert fresh.verify("b1") == (r1["sha"], 1)
    f = fresh.files("b1")[0]
    assert f.parent.name == "b1" and f.name == "2026-09-25.jsonl"
    rows = [json.loads(x) for x in f.read_text().splitlines()]
    rows[0]["x"] = 99  # edit a body: the chain breaks
    f.write_text("\n".join(json.dumps(r, sort_keys=True, separators=(",", ":")) for r in rows) + "\n")
    with pytest.raises(D.LogChainError):
        D.DecisionLog(tmp_path).verify("b1")


def test_torn_last_line_is_refused(tmp_path):
    log = D.DecisionLog(tmp_path)
    log.append("b1", 1_790_316_000_000, {"kind": "entry"})
    f = log.files("b1")[0]
    f.write_bytes(f.read_bytes().rstrip(b"\n"))
    with pytest.raises(D.LogChainError):
        D.DecisionLog(tmp_path).verify("b1")


def test_one_file_per_bot_and_day(tmp_path):
    log = D.DecisionLog(tmp_path)
    log.append("b1", 1_790_316_000_000, {"kind": "entry"})
    log.append("b1", 1_790_316_000_000 + 86_400_000, {"kind": "entry"})
    log.append("b2", 1_790_316_000_000, {"kind": "entry"})
    assert [f.name for f in log.files("b1")] == ["2026-09-25.jsonl", "2026-09-26.jsonl"]
    assert [f.name for f in log.files("b2")] == ["2026-09-25.jsonl"]
    assert log.verify("b1")[1] == 1 and log.verify("b2")[1] == 0
