"""Q50 successor base S* vs the frozen F50 (QP-RULING-1024-successor R7(b) item 3). Synthetic fixtures only; no network, no /data/mal reads.

S* = F50 (a5f41370) plus one decision-relevant change: a --fresh-forward ARENA engine seals at SUCC_END_MS (2026-12-01T00Z) instead of
H5_LOOK2_END_MS (2026-11-06T00Z). The 2026-09-20 md5 replay never reaches either end constant, so these tests exercise the changed line:
  * AST identity: every definition is byte-identical to F50 except SUCC_END_MS (new), Engine._h5_sealed_now and run_live, and those two equal
    F50 once the change is undone mechanically (so the diff is exactly the end constant);
  * seal grid on synthetic 2026 times, F50 and S* side by side, for the non-fresh engines (with and without the observed flag) and the fresh one;
  * end to end through the Engine that run_live itself builds (both modules, Q* 40 and 50): records byte-identical to F50 for s0 before
    2026-11-06T00Z and at or after SUCC_END_MS; for s0 in [2026-11-06T00Z, SUCC_END_MS) the decisions (trigger records) equal F50's and only the
    outcome-bearing records are added.
F50 is loaded from the git object, never from a checkout. A clone without it FAILS (never skips)."""
from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types as pytypes
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tools import h5_shadow as h5
from tools.test_h5_shadow import SPS, types
from tools.test_h5_shadow_fresh_forward import pool_tape, run_pools

F50 = "a5f413704eb120c8f529101a6186e3a95de7f83e"
SRC = Path(__file__).resolve().parent / "h5_shadow.py"
REPO = SRC.parent.parent
SHA = "0123456789abcdef0123456789abcdef01234567"


def ms(y, mo, d, h=0, mi=0, s=0) -> int:
    return int(datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp() * 1000)


L2_START, L2_END, SUCC = ms(2026, 10, 16), ms(2026, 11, 6), ms(2026, 12, 1)
# s0 instants (ms) across every boundary, synthetic 2026 times (plus one 2027)
GRID = sorted({ms(2026, 9, 20, 12), L2_START - 1_000, L2_START, ms(2026, 10, 24), L2_END - 1_000, L2_END, ms(2026, 11, 10), ms(2026, 11, 14) - 1_000,
               ms(2026, 11, 14), ms(2026, 11, 20), SUCC - 1_000, SUCC, SUCC + 1_000, ms(2026, 12, 15), ms(2027, 1, 15)})


def _f50_source() -> str | None:
    r = subprocess.run(["git", "-C", str(REPO), "show", f"{F50}:tools/h5_shadow.py"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


_F50_MOD: pytypes.ModuleType | None = None


def f50_module() -> pytypes.ModuleType:
    """tools/h5_shadow.py at F50, executed from the git object as its own module (same imports, nothing shared with S*'s module state)."""
    global _F50_MOD
    if _F50_MOD is None:
        src = _f50_source()
        if src is None:
            raise AssertionError(f"object {F50} is not in this clone: git fetch it")
        name = "tools._h5_shadow_f50_frozen"
        mod = pytypes.ModuleType(name)
        mod.__file__ = str(SRC)  # code_sha() falls back to this checkout; no test reads it (H5_CODE_SHA is set)
        mod.__package__ = "tools"
        sys.modules[name] = mod
        exec(compile(src, f"{F50[:8]}:tools/h5_shadow.py", "exec"), mod.__dict__)
        _F50_MOD = mod
    return _F50_MOD


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


class _UndoSucc(ast.NodeTransformer):
    """Undo the S* change mechanically: `SUCC_END_MS if self.fresh_start_ms is not None else H5_LOOK2_END_MS` -> `H5_LOOK2_END_MS`, then every
    remaining name SUCC_END_MS -> H5_LOOK2_END_MS. If the result equals F50, the S* diff in that definition is exactly the end constant."""

    def visit_IfExp(self, node):
        if (isinstance(node.body, ast.Name) and node.body.id == "SUCC_END_MS" and isinstance(node.orelse, ast.Name) and node.orelse.id == "H5_LOOK2_END_MS"
                and ast.dump(node.test) == ast.dump(ast.parse("self.fresh_start_ms is not None", mode="eval").body)):
            self.ifexp += 1
            return node.orelse
        return self.generic_visit(node)

    def visit_Name(self, node):
        if node.id == "SUCC_END_MS":
            self.names += 1
            return ast.copy_location(ast.Name(id="H5_LOOK2_END_MS", ctx=node.ctx), node)
        return node

    def __init__(self):
        self.ifexp = self.names = 0


def _undo(node: ast.AST) -> tuple[str, int, int]:
    """(dump after undo, conditional ends undone, bare SUCC_END_MS names undone)."""
    t = _UndoSucc()
    out = t.visit(copy.deepcopy(node))
    return ast.dump(out), t.ifexp, t.names


class AstIdentityVsF50(unittest.TestCase):
    def setUp(self):
        src = _f50_source()
        if src is None:  # fail, never skip
            self.fail(f"object {F50} is not in this clone: git fetch it")
        self.old = _defs(ast.parse(src))
        self.new = _defs(ast.parse(SRC.read_text()))

    def test_only_succ_end_engine_and_run_live_differ_at_top_level(self):
        diff = sorted(k for k in set(self.old) | set(self.new) if k not in self.old or k not in self.new or ast.dump(self.old[k]) != ast.dump(self.new[k]))
        self.assertEqual(diff, ["Engine", "SUCC_END_MS", "run_live"])
        self.assertNotIn("SUCC_END_MS", self.old)
        for k in ("H5_LOOK2_START_MS", "H5_LOOK2_END_MS", "SEAL_START_MS", "Q_STAR_SOL", "Q50_RULE_SHA256", "run_replay", "main", "replay", "replay_rows"):
            self.assertEqual(ast.dump(self.old[k]), ast.dump(self.new[k]), k)

    def test_only_h5_sealed_now_differs_inside_engine(self):
        om = {n.name: n for n in self.old["Engine"].body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        nm = {n.name: n for n in self.new["Engine"].body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertEqual(set(om), set(nm))
        self.assertEqual(sorted(k for k in om if ast.dump(om[k]) != ast.dump(nm[k])), ["_h5_sealed_now"])
        strip = lambda cls: [ast.dump(n) for n in cls.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]  # noqa: E731
        self.assertEqual(strip(self.old["Engine"]), strip(self.new["Engine"]))
        self.assertEqual([ast.dump(b) for b in self.old["Engine"].bases], [ast.dump(b) for b in self.new["Engine"].bases])

    def test_the_changed_definitions_are_f50_once_the_end_constant_is_undone(self):
        nm = {n.name: n for n in self.new["Engine"].body if isinstance(n, ast.FunctionDef)}
        om = {n.name: n for n in self.old["Engine"].body if isinstance(n, ast.FunctionDef)}
        self.assertEqual(_undo(nm["_h5_sealed_now"]), (ast.dump(om["_h5_sealed_now"]), 1, 0))  # one end conditional on fresh_start_ms, nothing else
        self.assertEqual(_undo(self.new["run_live"]), (ast.dump(self.old["run_live"]), 0, 3))  # the fresh Engine call and the fresh start record's end_only (2 names)

    def test_succ_end_is_read_only_by_the_two_changed_definitions(self):
        users = sorted(k for k, n in self.new.items() if k != "SUCC_END_MS" and any(isinstance(x, ast.Name) and x.id == "SUCC_END_MS" for x in ast.walk(n)))
        self.assertEqual(users, ["Engine", "run_live"])
        eng_users = sorted(n.name for n in self.new["Engine"].body if isinstance(n, ast.FunctionDef)
                           and any(isinstance(x, ast.Name) and x.id == "SUCC_END_MS" for x in ast.walk(n)))
        self.assertEqual(eng_users, ["_h5_sealed_now"])


class ConstantTests(unittest.TestCase):
    def test_succ_end_is_pinned_and_the_old_seal_is_not_moved(self):
        self.assertEqual(h5.SUCC_END_MS, 1_796_083_200_000)
        self.assertEqual(h5.iso_from_ms(h5.SUCC_END_MS), "2026-12-01T00:00:00.000Z")
        self.assertGreaterEqual(h5.SUCC_END_MS, ms(2026, 11, 14))  # R7(b)5: at or after EXP-024's latest end state
        f = f50_module()
        self.assertEqual((h5.H5_LOOK2_START_MS, h5.H5_LOOK2_END_MS, h5.SEAL_START_MS), (f.H5_LOOK2_START_MS, f.H5_LOOK2_END_MS, f.SEAL_START_MS))
        self.assertEqual(h5.H5_LOOK2_END_MS, L2_END)
        self.assertFalse(hasattr(f, "SUCC_END_MS"))
        self.assertEqual(h5.look2_start_info(None), f.look2_start_info(None))  # the non-fresh start record's seal block
        self.assertEqual(h5.look2_start_info("EXP-024-Am2"), f.look2_start_info("EXP-024-Am2"))


def _engine(mod, **kw):
    out: list[dict] = []
    kw.setdefault("sps_fn", lambda p: SPS)
    kw.setdefault("pda_fn", lambda pool: "PDA_" + pool)
    kw.setdefault("classifier", mod.StaticClassifier(False, "rpc"))
    eng = mod.Engine(out.append, wall=lambda: 1_800_000_000_000, **kw)
    return eng, out


def _sealed(mod, t_ms, **kw) -> bool:
    eng, _ = _engine(mod, **kw)
    return eng._h5_sealed_now(SimpleNamespace(s0_ts=t_ms // 1000, s0_recv_ms=t_ms + 37))


# non-fresh engines: run_live's default (observed off / on), the replay engine (no seal arguments), seal off
NON_FRESH = {
    "default": {},
    "observed": {"h5_look2_observed": True},
    "seal_off": {"h5_look2_start_ms": None},
    "default_q50": {"q_star_sol": 50.0},
}


class SealGridTests(unittest.TestCase):
    def test_non_fresh_engines_seal_exactly_as_f50(self):
        f = f50_module()
        for name, kw in NON_FRESH.items():
            for t in GRID:
                self.assertEqual(_sealed(h5, t, **kw), _sealed(f, t, **kw), (name, h5.iso_from_ms(t)))
        # and what F50 does there, spelled out (so equality is not equality of two wrong answers)
        for t in GRID:
            self.assertEqual(_sealed(h5, t), t >= L2_START, h5.iso_from_ms(t))
            self.assertEqual(_sealed(h5, t, h5_look2_observed=True), t >= L2_END, h5.iso_from_ms(t))
            self.assertFalse(_sealed(h5, t, h5_look2_start_ms=None))

    def test_fresh_engine_as_run_live_builds_it_seals_from_succ_end_only(self):
        f = f50_module()
        for t in GRID:
            self.assertEqual(_sealed(h5, t, fresh_start_ms=0, seal_start_ms=None, h5_look2_start_ms=h5.SUCC_END_MS), t >= SUCC, h5.iso_from_ms(t))
            self.assertEqual(_sealed(f, t, fresh_start_ms=0, seal_start_ms=None, h5_look2_start_ms=f.H5_LOOK2_END_MS), t >= L2_END, h5.iso_from_ms(t))

    def test_fresh_engine_with_f50_arguments_or_seal_off_is_unchanged(self):
        f = f50_module()
        for kw in ({"h5_look2_start_ms": L2_END}, {"h5_look2_start_ms": None}, {}, {"h5_look2_start_ms": L2_START}):
            for t in GRID:
                self.assertEqual(_sealed(h5, t, fresh_start_ms=0, seal_start_ms=None, **kw), _sealed(f, t, fresh_start_ms=0, seal_start_ms=None, **kw),
                                 (kw, h5.iso_from_ms(t)))


def _run_live_engine_kwargs(mod, root: Path, argv: list[str]) -> tuple[dict, dict]:
    """Drive mod.run_live (no feed, no network) and return the Engine kwargs it built and its start record."""
    out_dir = root / "arena" / "h5-shadow-succ"
    args = mod.build_parser().parse_args(["--out-dir", str(out_dir), "--sockets", "3", *argv])
    built: list[dict] = []
    real = mod.Engine

    def spy(*a, **k):
        built.append(k)
        return real(*a, **k)

    async def no_feed(*a, **k):
        return None

    def no_oracle(*a, **k):
        raise AssertionError("the CAP-PICK environment is read in fresh-forward mode")

    with mock.patch.object(mod, "ARENA_ROOT", str(root / "arena")), mock.patch.dict(os.environ, {"H5_CODE_SHA": SHA}), \
            mock.patch.object(mod, "run_feed", no_feed), mock.patch.object(mod, "build_source", lambda *a, **k: None), \
            mock.patch.object(mod, "Engine", spy), mock.patch.object(mod, "cap_pick_oracle_from_env", no_oracle):
        assert asyncio.run(mod.run_live(args)) == 0
    rows = [json.loads(line) for fp in sorted(out_dir.glob("h5-shadow-*.jsonl")) for line in fp.read_text().splitlines()]
    shutil.rmtree(out_dir)
    (k,) = built
    return k, [r for r in rows if r["type"] == "start"][0]


def _md5(rows) -> str:
    return hashlib.md5(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()


class RunLiveEndToEndTests(unittest.TestCase):
    """The Engine that run_live --fresh-forward ARENA builds, in F50 and in S*, fed the same synthetic pools."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir="/tmp", prefix="arena-succ-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.f = f50_module()

    def engines(self, argv):
        ks, ss = _run_live_engine_kwargs(h5, self.root, ["--fresh-forward", "ARENA", *argv])
        kf, sf = _run_live_engine_kwargs(self.f, self.root, ["--fresh-forward", "ARENA", *argv])
        return (ks, ss), (kf, sf)

    def run_both(self, argv, ts0):
        (ks, _), (kf, _) = self.engines(argv)
        res = []
        for mod, k in ((h5, ks), (self.f, kf)):
            k = {**k, "classifier": mod.StaticClassifier(False, "rpc"), "fresh_start_ms": ms(2026, 10, 25)}  # the live classifier would call the RPC
            eng, out = _engine(mod, **k)
            run_pools(eng, pool_tape("PoolAAAA", 1000, ts0 // 1000))
            res.append((out, dict(eng.counters)))
        return res

    def test_engine_arguments_differ_from_f50_only_in_the_fresh_end(self):
        for argv in ([], ["--q-star-sol", "50"]):
            (ks, ss), (kf, sf) = self.engines(argv)
            self.assertEqual((ks["h5_look2_start_ms"], kf["h5_look2_start_ms"]), (h5.SUCC_END_MS, L2_END), argv)
            strip = lambda k: {x: v for x, v in k.items() if x not in ("h5_look2_start_ms", "classifier", "fresh_start_ms")}  # noqa: E731
            self.assertEqual(strip(ks), strip(kf), argv)
            self.assertEqual((ks["h5_look2_observed"], ks["seal_start_ms"], ks["pick_oracle"]), (False, None, None))
            self.assertEqual((ss["h5_look2"]["end_only_ms"], sf["h5_look2"]["end_only_ms"]), (h5.SUCC_END_MS, L2_END))
            vol = ("t_ms", "start_ms", "pid", "out_dir", "h5_look2", "host")
            self.assertEqual({x: v for x, v in ss.items() if x not in vol}, {x: v for x, v in sf.items() if x not in vol}, argv)
            self.assertEqual({x: v for x, v in ss["h5_look2"].items() if not x.startswith("end_only")},
                             {x: v for x, v in sf["h5_look2"].items() if not x.startswith("end_only")})

    def test_outside_the_new_span_every_record_equals_f50(self):
        for argv in ([], ["--q-star-sol", "50"]):
            for ts0 in (L2_END - 3_000_000, L2_END - 1_000, SUCC, SUCC + 3_000_000, ms(2027, 1, 15)):
                (os_, cs), (of, cf) = self.run_both(argv, ts0)
                self.assertTrue(types(os_, "trigger"), (argv, h5.iso_from_ms(ts0)))  # the fixture reaches a decision
                self.assertEqual(_md5(os_), _md5(of), (argv, h5.iso_from_ms(ts0)))
                self.assertEqual(cs, cf, (argv, h5.iso_from_ms(ts0)))
                whole = ts0 < L2_END
                self.assertEqual(bool(types(os_, "outcome")), whole, (argv, h5.iso_from_ms(ts0)))

    def test_inside_the_new_span_decisions_equal_f50_and_outcomes_are_added(self):
        for argv in ([], ["--q-star-sol", "50"]):
            for ts0 in (L2_END, L2_END + 100_000, ms(2026, 11, 14), SUCC - 3_000_000):
                (os_, _), (of, _) = self.run_both(argv, ts0)
                ts, tf = types(os_, "trigger"), types(of, "trigger")
                self.assertEqual(len(ts), 2, (argv, h5.iso_from_ms(ts0)))
                self.assertEqual(len(tf), 2)
                for a, b in zip(ts, tf):  # the decisions themselves: every shared field equal; only the seal marker and the outcome-bearing ladder differ
                    self.assertIs(b["h5_look2_sealed"], True)
                    self.assertEqual((set(a) - set(b), set(b) - set(a)), ({"exit_ladder_trigger_slots"}, {"h5_look2_sealed"}))
                    self.assertEqual({k: a[k] for k in set(a) & set(b)}, {k: b[k] for k in set(a) & set(b)}, (argv, h5.iso_from_ms(ts0)))
                self.assertEqual(_md5(types(os_, "gap")), _md5(types(of, "gap")))
                bs, bf = types(os_, "boost_slice"), types(of, "boost_slice")  # decision-time BOOST slices: F50 marks them sealed, S* does not
                self.assertTrue(bf and all(b.pop("h5_look2_sealed") is True for b in bf))
                self.assertFalse([b for b in bs if "h5_look2_sealed" in b])
                self.assertEqual(_md5(bs), _md5(bf))
                self.assertEqual(types(of, "outcome"), [])  # F50: decision only
                self.assertEqual(len(types(os_, "outcome")), 2)  # S*: whole
                self.assertEqual(len(types(os_, "strip")), 1)
                self.assertIsNotNone(types(os_, "pool")[0]["min_q_pv_sol"])
                self.assertIsNone(types(of, "pool")[0]["min_q_pv_sol"])
                # Q* != 40: no bx10 either way; Q* = 40: bx10 follows the outcome
                self.assertEqual(len(types(os_, "outcome_bx10")), 0 if argv else 2)
                self.assertEqual(types(of, "outcome_bx10"), [])


if __name__ == "__main__":
    unittest.main()
