"""QP ruling 2026-10-22 s2 (iv): the decode path of tools/h5_shadow.py is source-identical between the frozen 1f7a89c and this head.

A3's decoder parity (tools/a3_redeploy_check.py, FROZEN_FILES) is run on the frozen decoders; it transfers to a Q* = 50 book only if every
function outside the Q* change is byte-identical (AST, line numbers ignored). The changed names are listed here and nowhere else."""
from __future__ import annotations

import ast
import subprocess
import unittest
from pathlib import Path

FROZEN = "1f7a89c"
SRC = Path(__file__).resolve().parent / "h5_shadow.py"
REPO = SRC.parent.parent

# top-level definitions changed on purpose (the flag, its checks, the Q50 constants, the start record, replay compare)
CHANGED_TOP = {"Q50_REPLAY_CUTOFF_HOUR", "refuse_replay", "check_q_star", "_q_star_arg", "Q_STAR_ALLOWED", "Q50_RULE_ID", "Q50_RULE_SHA256", "Engine", "run_live", "run_replay", "compare_frozen", "build_parser", "main",
               "SUCC_END_MS"}  # S* (QP-RULING-1024-successor R7(b)); S* vs F50 identity: tools/test_h5_shadow_succ_identity.py
# Engine methods changed: the constructor and the places that compared against the constant (+ the trigger record)
CHANGED_ENGINE = {"__init__", "_fire", "_evaluate", "_eval",
                  "_h5_sealed_now"}  # S*: the fresh-forward end (QP-RULING-1024-successor R7(b))
# decode path: must be listed as unchanged (sanity that the allowlists did not swallow them)
MUST_BE_IDENTICAL = {"decode_notice", "post_state", "tier_fee", "fill_round_trip", "Pr", "SlotClock", "SynClassifier", "PreEventClassifier", "UnionChain",
                     "classify_via_rpc", "_rpc_tx_parts", "replay", "replay_rows", "JsonlSink", "redact_argv", "code_sha", "bx10_exit", "project_e", "sps_ok"}


def _frozen_source() -> str | None:
    r = subprocess.run(["git", "-C", str(REPO), "show", f"{FROZEN}:tools/h5_shadow.py"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def _defs(tree: ast.Module) -> dict[str, ast.AST]:
    out: dict[str, ast.AST] = {}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[n.name] = n
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = n
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            out[n.target.id] = n
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            out["import:" + ast.dump(n)] = n
    return out


def _strip_doc(n: ast.AST) -> str:
    return ast.dump(n)


class DecodePathIdentity(unittest.TestCase):
    def setUp(self):
        if _frozen_source() is None:  # fail, never skip: a clone without the frozen object proves nothing
            self.fail(f"object {FROZEN} is not in this clone: git fetch it")
        self.old = _defs(ast.parse(_frozen_source()))
        self.new = _defs(ast.parse(SRC.read_text()))

    def test_every_top_level_name_outside_the_change_is_identical(self):
        diff = sorted(k for k in set(self.old) | set(self.new) if k not in CHANGED_TOP and (k not in self.old or k not in self.new
                      or _strip_doc(self.old[k]) != _strip_doc(self.new[k])))
        self.assertEqual(diff, [])

    def test_the_decode_path_names_are_present_and_unchanged(self):
        for k in MUST_BE_IDENTICAL:
            self.assertIn(k, self.old, k)
            self.assertEqual(_strip_doc(self.old[k]), _strip_doc(self.new[k]), k)

    def test_engine_methods_outside_the_change_are_identical(self):
        om = {n.name: n for n in self.old["Engine"].body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        nm = {n.name: n for n in self.new["Engine"].body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertEqual(set(om), set(nm))
        changed = sorted(k for k in om if _strip_doc(om[k]) != _strip_doc(nm[k]))
        self.assertTrue(set(changed) <= CHANGED_ENGINE | self._q_users(), changed)
        for k in ("on_trade", "on_create_pool", "_union_fields", "emit"):
            if k in om:
                self.assertEqual(_strip_doc(om[k]), _strip_doc(nm[k]), k)

    def _q_users(self) -> set[str]:
        """Engine methods whose frozen source reads Q_STAR_SOL: exactly the ones that must change."""
        return {n.name for n in self.old["Engine"].body if isinstance(n, ast.FunctionDef)
                and any(isinstance(x, ast.Name) and x.id == "Q_STAR_SOL" for x in ast.walk(n))}

    def test_no_engine_method_reads_the_constant_any_more(self):
        for n in self.new["Engine"].body:
            if isinstance(n, ast.FunctionDef) and n.name not in ("__init__", "_fire"):  # _fire compares with the default to add the record key
                self.assertFalse(any(isinstance(x, ast.Name) and x.id == "Q_STAR_SOL" for x in ast.walk(n)), n.name)


if __name__ == "__main__":
    unittest.main()
