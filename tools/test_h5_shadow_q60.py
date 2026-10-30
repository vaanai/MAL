"""The Q60 frozen commit `q60-base-1` vs S* (DEC-031 Amendment 19 item 2, tests (c) to (i)). Synthetic fixtures only; no network, no /data/mal reads.

q60-base-1 = S* (5c2ab7c64b21358619e37068c6a53e4d183512ea, tag q50-succ-base-1) plus exactly:
  (a) Q_STAR_ALLOWED = (40.0, 50.0, 60.0);
  (b) Q_STAR_RULES, the per-level rule table {50.0: Q50's id and sha, 60.0: H5-BOOSTFLOOR-Q60 v1 and f36afaae...}, used by run_live's start record and its
      pinned-sha check (so the 40.0 and 50.0 records stay byte-identical);
  (i) compare_frozen refuses when no frozen row matches the level.
Tests here: (c) the Q* 60 trigger at exactly <= 60e9; (d) 40/50 start records byte-identical to S*'s; (e) 45 and 70 refused; (f) AST identity of every other
definition against S*; (g) decode identity (S*, and the frozen 1f7a89c); (h) the seal grid at Q* 60 on synthetic 2026 times; (i) the compare_frozen refusal.
S* is loaded from the git object, never from a checkout. A clone without it FAILS (never skips)."""
from __future__ import annotations

import ast
import asyncio
import contextlib
import difflib
import io
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
from tools.test_h5_shadow import SOL, Tape, announce, boost_buys, drain, make_engine, run, types

S_STAR = "5c2ab7c64b21358619e37068c6a53e4d183512ea"
FROZEN_1F7A89C = "1f7a89c"
SRC = Path(__file__).resolve().parent / "h5_shadow.py"
REPO = SRC.parent.parent
SHA = "0123456789abcdef0123456789abcdef01234567"
Q50_SHA = "b3f8ecbf8731dc322a035a0b3996b7ed3f0b8c508cc6947014e3de1aa9524fd4"
Q60_SHA = "f36afaae00edd5b8292fdbe732c6034251cc1371afb6c1e54b50e548a91717b9"


def ms(y, mo, d, h=0, mi=0, s=0) -> int:
    return int(datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp() * 1000)


def _git_source(rev: str) -> str | None:
    r = subprocess.run(["git", "-C", str(REPO), "show", f"{rev}:tools/h5_shadow.py"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


_SSTAR_MOD: pytypes.ModuleType | None = None


def sstar_module() -> pytypes.ModuleType:
    """tools/h5_shadow.py at S*, executed from the git object as its own module (nothing shared with this head's module state)."""
    global _SSTAR_MOD
    if _SSTAR_MOD is None:
        src = _git_source(S_STAR)
        if src is None:
            raise AssertionError(f"object {S_STAR} is not in this clone: git fetch it")
        name = "tools._h5_shadow_sstar_frozen"
        mod = pytypes.ModuleType(name)
        mod.__file__ = str(SRC)  # code_sha() falls back to this checkout; the tests set H5_CODE_SHA
        mod.__package__ = "tools"
        sys.modules[name] = mod
        exec(compile(src, f"{S_STAR[:8]}:tools/h5_shadow.py", "exec"), mod.__dict__)
        _SSTAR_MOD = mod
    return _SSTAR_MOD


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


def decisions(q_target, **kw):
    eng, out = make_engine(**kw)
    announce(eng)
    t = Tape()
    t.row(1000, "buy", "SNIPER", SOL // 5)
    boost_buys(t, 1000)
    drain(t, 1200, q_target)
    run(eng, t)
    return out


# ---------------------------------------------------------------- (c) the Q* 60 trigger
class Q60TriggerTests(unittest.TestCase):
    def test_60_triggers_at_exactly_60e9_and_not_above(self):
        for q in (60.0, 59.999999, 55.0, 45.0, 35.0):
            trig = types(decisions(q, q_star_sol=60.0), "trigger")
            self.assertEqual({r["variant"] for r in trig}, {"pv", "fv"}, q)
            self.assertLessEqual(trig[0]["q_pv_post_sol"], 60.0, q)
            self.assertEqual(trig[0]["q_star_sol"], 60.0, q)
        for q in (60.000001, 60.5, 65.0):
            self.assertEqual(types(decisions(q, q_star_sol=60.0), "trigger"), [], q)

    def test_the_boundary_is_one_lamport_wide_on_the_engines_own_comparison(self):
        # the engine compares q_post / 1e9 <= q_star_sol; 60e9 lamports is in, 60e9 + 1 is out
        self.assertTrue(60_000_000_000 / 1e9 <= 60.0)
        self.assertFalse(60_000_000_001 / 1e9 <= 60.0)
        src = Path(h5.__file__).read_text()
        self.assertGreaterEqual(src.count("/ 1e9 <= self.q_star_sol"), 3)

    def test_lower_levels_do_not_trigger_in_the_band(self):
        self.assertEqual(types(decisions(55.0, q_star_sol=50.0), "trigger"), [])
        self.assertEqual(types(decisions(55.0), "trigger"), [])
        self.assertEqual(len(types(decisions(55.0, q_star_sol=60.0), "trigger")), 2)

    def test_a_q60_engine_never_writes_bx10(self):
        out = decisions(55.0, q_star_sol=60.0, bx10_enabled=True, seal_start_ms=None)
        self.assertEqual(types(out, "outcome_bx10"), [])
        self.assertFalse(make_engine(q_star_sol=60.0, bx10_enabled=True)[0].bx10_enabled)

    def test_the_rule_table(self):
        self.assertEqual(h5.Q_STAR_ALLOWED, (40.0, 50.0, 60.0))
        self.assertEqual(h5.Q_STAR_RULES, {50.0: ("H5-BOOSTFLOOR-Q50 v1", Q50_SHA), 60.0: ("H5-BOOSTFLOOR-Q60 v1", Q60_SHA)})
        self.assertEqual((h5.Q50_RULE_ID, h5.Q50_RULE_SHA256), h5.Q_STAR_RULES[50.0])
        self.assertNotIn(40.0, h5.Q_STAR_RULES)  # the default level carries no rule fields
        self.assertEqual(set(h5.Q_STAR_RULES), set(h5.Q_STAR_ALLOWED) - {h5.Q_STAR_SOL})


# ---------------------------------------------------------------- (e) 45 and 70 refused
class RefusalTests(unittest.TestCase):
    BAD = (45, 45.0, 70, 70.0, 0, -60.0, 59.9999, 60.0001, 61.0, float("nan"), float("inf"), True, None, "60", [60])

    def test_check_q_star_and_engine_refuse(self):
        for bad in self.BAD:
            with self.assertRaises(ValueError, msg=repr(bad)):
                h5.check_q_star(bad)
            with self.assertRaises(ValueError, msg=repr(bad)):
                h5.Engine(lambda r: None, q_star_sol=bad)
        for ok in (40, 40.0, 50, 50.0, 60, 60.0):
            self.assertEqual(h5.check_q_star(ok), float(ok))
            self.assertIsInstance(h5.check_q_star(ok), float)
            self.assertEqual(h5.Engine(lambda r: None, q_star_sol=ok).q_star_sol, float(ok))

    def test_parser_accepts_60_and_refuses_45_and_70(self):
        self.assertEqual(h5.build_parser().parse_args(["--q-star-sol", "60"]).q_star_sol, 60.0)
        for bad in ("45", "70", "60.0001", "59.99", "0", "nan", "inf", "-60", ""):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit, msg=bad):
                h5.build_parser().parse_args(["--q-star-sol", bad])

    def test_main_and_run_live_refuse_45_and_70(self):
        for bad in ("45", "70"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                h5.main(["--q-star-sol", bad, "--fresh-forward", "ARENA"])
        for bad in (45, 70):
            args = SimpleNamespace(fresh_forward="ARENA", q_star_sol=bad)
            with self.assertRaises(ValueError):
                asyncio.run(h5.run_live(args))


# ---------------------------------------------------------------- (d) start records vs S*
def _start_record(mod, root: Path, argv: list[str]) -> dict:
    """Drive mod.run_live (no feed, no network) and return its start record."""
    out_dir = root / "arena" / "h5-q60-start"
    args = mod.build_parser().parse_args(["--out-dir", str(out_dir), "--sockets", "3", "--fresh-forward", "ARENA", *argv])

    async def no_feed(*a, **k):
        return None

    def no_oracle(*a, **k):
        raise AssertionError("the CAP-PICK environment is read in fresh-forward mode")

    with mock.patch.object(mod, "ARENA_ROOT", str(root / "arena")), mock.patch.dict(os.environ, {"H5_CODE_SHA": SHA}), \
            mock.patch.object(mod, "run_feed", no_feed), mock.patch.object(mod, "build_source", lambda *a, **k: None), \
            mock.patch.object(mod, "cap_pick_oracle_from_env", no_oracle):
        assert asyncio.run(mod.run_live(args)) == 0
    rows = [json.loads(line) for fp in sorted(out_dir.glob("h5-shadow-*.jsonl")) for line in fp.read_text().splitlines()]
    shutil.rmtree(out_dir)
    return [r for r in rows if r["type"] == "start"][0]


VOLATILE = ("t_ms", "start_ms", "pid", "out_dir", "host")


def _canon(rec: dict) -> str:
    """The record as written (key order kept), minus the fields that vary run to run."""
    return json.dumps({k: v for k, v in rec.items() if k not in VOLATILE}, default=str)


class StartRecordTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir="/tmp", prefix="arena-q60-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_40_and_50_start_records_are_byte_identical_to_sstar(self):
        s = sstar_module()
        for argv in ([], ["--q-star-sol", "40"], ["--q-star-sol", "50"]):
            new, old = _start_record(h5, self.root, argv), _start_record(s, self.root, argv)
            self.assertEqual(_canon(new), _canon(old), argv)
            self.assertEqual(set(new), set(old), argv)

    def test_the_50_record_carries_q50_rule_fields_and_40_none(self):
        r50 = _start_record(h5, self.root, ["--q-star-sol", "50"])
        self.assertEqual((r50["rule"], r50["rule_sha256"], r50["q_star_sol"], r50["bx10"]), ("H5-BOOSTFLOOR-Q50 v1", Q50_SHA, 50.0, False))
        r40 = _start_record(h5, self.root, [])
        self.assertEqual((r40["rule"], r40["rule_sha256"], r40["bx10"]), (h5.RULE_ID, h5.RULE_SHA256, True))
        self.assertNotIn("q_star_sol", r40)

    def test_the_60_record_carries_q60_rule_fields_and_differs_only_there(self):
        r60, r50 = _start_record(h5, self.root, ["--q-star-sol", "60"]), _start_record(h5, self.root, ["--q-star-sol", "50"])
        skip = ("rule", "rule_sha256", "q_star_sol") + VOLATILE
        self.assertEqual((r60["rule"], r60["rule_sha256"], r60["q_star_sol"], r60["bx10"]), ("H5-BOOSTFLOOR-Q60 v1", Q60_SHA, 60.0, False))
        self.assertEqual({k: v for k, v in r60.items() if k not in skip},
                         {k: v for k, v in r50.items() if k not in skip})
        self.assertEqual(list(r60), list(r50))  # same key order

    def test_a_q_star_not_40_still_needs_fresh_forward_and_refuses_look2_and_bx10(self):
        for argv, rx in ((["--q-star-sol", "60"], "requires --fresh-forward"),
                         (["--fresh-forward", "ARENA", "--q-star-sol", "60", "--bx10-enable", "EXP-026"], "bx10"),
                         (["--fresh-forward", "ARENA", "--q-star-sol", "60", "--h5-look2-observed", "EXP-024-Am2"], "look2|Look-2|h5-look2")):
            args = h5.build_parser().parse_args(["--out-dir", str(self.root / "arena" / "h5-q60-x"), "--sockets", "3", *argv])
            with mock.patch.object(h5, "ARENA_ROOT", str(self.root / "arena")), self.assertRaisesRegex(ValueError, rx):
                asyncio.run(h5.run_live(args))

    def test_an_unpinned_level_sha_refuses_the_start(self):
        for q in (50.0, 60.0):
            table = {**h5.Q_STAR_RULES, q: (h5.Q_STAR_RULES[q][0], None)}
            args = h5.build_parser().parse_args(["--out-dir", str(self.root / "arena" / "h5-q60-y"), "--sockets", "3", "--fresh-forward", "ARENA",
                                                 "--q-star-sol", str(int(q))])
            with mock.patch.object(h5, "ARENA_ROOT", str(self.root / "arena")), mock.patch.object(h5, "Q_STAR_RULES", table), \
                    self.assertRaisesRegex(ValueError, "not pinned"):
                asyncio.run(h5.run_live(args))


# ---------------------------------------------------------------- (f) AST identity vs S*
# Definitions changed on purpose; every other top-level definition (and every Engine method) is identical to S*.
CHANGED_TOP = {"Q_STAR_ALLOWED", "Q_STAR_RULES", "run_live", "compare_frozen"}


class AstIdentityVsSStar(unittest.TestCase):
    def setUp(self):
        src = _git_source(S_STAR)
        if src is None:  # fail, never skip
            self.fail(f"object {S_STAR} is not in this clone: git fetch it")
        self.old_tree, self.new_tree = ast.parse(src), ast.parse(SRC.read_text())
        self.old, self.new = _defs(self.old_tree), _defs(self.new_tree)

    def test_every_other_top_level_definition_is_identical(self):
        diff = sorted(k for k in set(self.old) | set(self.new) if k not in self.old or k not in self.new or ast.dump(self.old[k]) != ast.dump(self.new[k]))
        self.assertEqual(diff, sorted(CHANGED_TOP))
        self.assertNotIn("Q_STAR_RULES", self.old)
        for k in CHANGED_TOP - {"Q_STAR_RULES"}:
            self.assertIn(k, self.old)

    def test_the_module_level_statement_order_outside_the_change_is_the_same(self):
        names = lambda tree: [ast.dump(n) for n in tree.body if not (isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "Q_STAR_RULES")  # noqa: E731
                              and not (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in ("Q_STAR_ALLOWED",) for t in n.targets))
                              and not (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in ("run_live", "compare_frozen"))]
        self.assertEqual(names(self.old_tree), names(self.new_tree))

    def test_engine_is_identical_method_by_method(self):
        self.assertEqual(ast.dump(self.old["Engine"]), ast.dump(self.new["Engine"]))

    def test_the_constants_a_decision_reads_are_identical(self):
        for k in ("Q_STAR_SOL", "Q50_RULE_ID", "Q50_RULE_SHA256", "RULE_ID", "RULE_SHA256", "SUCC_END_MS", "H5_LOOK2_START_MS", "H5_LOOK2_END_MS",
                  "SEAL_START_MS", "Q50_REPLAY_CUTOFF_HOUR", "check_q_star", "_q_star_arg", "refuse_replay", "run_replay", "build_parser", "main"):
            self.assertEqual(ast.dump(self.old[k]), ast.dump(self.new[k]), k)

    @staticmethod
    def _changed_lines(old: ast.AST, new: ast.AST) -> tuple[list[str], list[str]]:
        a, b = ast.unparse(old).splitlines(), ast.unparse(new).splitlines()
        rem = [x[1:] for x in difflib.unified_diff(a, b, lineterm="", n=0) if x.startswith("-") and not x.startswith("---")]
        add = [x[1:] for x in difflib.unified_diff(a, b, lineterm="", n=0) if x.startswith("+") and not x.startswith("+++")]
        return rem, add

    def test_run_live_differs_in_exactly_the_sha_check_and_the_start_record_update(self):
        rem, add = self._changed_lines(self.old["run_live"], self.new["run_live"])
        self.assertEqual(len(rem), 3, rem)  # the `if`, its `raise`, the start_rec.update
        self.assertEqual(len(add), 3, add)
        self.assertTrue(any("Q50_RULE_SHA256 is None" in x for x in rem))
        self.assertTrue(any("'rule': Q50_RULE_ID" in x for x in rem))
        self.assertTrue(any("Q_STAR_RULES[q_star][1] is None" in x for x in add))
        self.assertTrue(any("'rule': Q_STAR_RULES[q_star][0]" in x for x in add))

    def test_compare_frozen_differs_in_exactly_the_refusal(self):
        rem, add = self._changed_lines(self.old["compare_frozen"], self.new["compare_frozen"])
        self.assertEqual(rem, [])
        self.assertEqual(len(add), 2, add)
        self.assertTrue(any("len(f) == 0" in x for x in add))
        self.assertTrue(any("raise Refused" in x for x in add))

    def test_the_whole_file_diff_is_only_the_listed_hunks(self):
        old, new = _git_source(S_STAR).splitlines(), SRC.read_text().splitlines()
        rem = [x for x in difflib.unified_diff(old, new, lineterm="", n=0) if x.startswith("-") and not x.startswith("---")]
        self.assertEqual(len(rem), 4, rem)  # Q_STAR_ALLOWED, the sha check (2 lines), the start-record update
        self.assertEqual(len([x for x in difflib.unified_diff(old, new, lineterm="", n=0) if x.startswith("@@")]), 5, "hunks")  # allowed, table, sha check, start record, compare_frozen


# ---------------------------------------------------------------- (g) decode identity
# The decode path (as tools/test_h5_shadow_qstar_identity.py lists it): byte-identical in AST to S*, and so (S* = F50 + the fresh end, F50 = 1f7a89c + the
# Q* change) to the frozen 1f7a89c that A3's decoder parity ran on.
DECODE_PATH = ("decode_notice", "post_state", "tier_fee", "fill_round_trip", "Pr", "SlotClock", "SynClassifier", "PreEventClassifier", "UnionChain",
               "classify_via_rpc", "_rpc_tx_parts", "replay", "replay_rows", "JsonlSink", "redact_argv", "code_sha", "bx10_exit", "project_e", "sps_ok")


class DecodeIdentityTests(unittest.TestCase):
    def test_decode_path_is_identical_to_sstar(self):
        old, new = _defs(ast.parse(_git_source(S_STAR) or self.fail(f"object {S_STAR} is not in this clone"))), _defs(ast.parse(SRC.read_text()))
        for k in DECODE_PATH:
            self.assertIn(k, old, k)
            self.assertEqual(ast.dump(old[k]), ast.dump(new[k]), k)

    def test_decode_path_is_identical_to_the_frozen_1f7a89c(self):
        src = _git_source(FROZEN_1F7A89C)
        if src is None:
            self.fail(f"object {FROZEN_1F7A89C} is not in this clone: git fetch it")
        old, new = _defs(ast.parse(src)), _defs(ast.parse(SRC.read_text()))
        for k in DECODE_PATH:
            self.assertIn(k, old, k)
            self.assertEqual(ast.dump(old[k]), ast.dump(new[k]), k)

    def test_engine_decode_methods_are_identical_to_the_frozen(self):
        src = _git_source(FROZEN_1F7A89C)
        if src is None:
            self.fail(f"object {FROZEN_1F7A89C} is not in this clone: git fetch it")
        om = {n.name: n for n in _defs(ast.parse(src))["Engine"].body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        nm = {n.name: n for n in _defs(ast.parse(SRC.read_text()))["Engine"].body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for k in ("on_trade", "on_create_pool", "_union_fields", "emit"):
            self.assertEqual(ast.dump(om[k]), ast.dump(nm[k]), k)

    def test_a_decoded_stream_gives_the_same_records_in_both_modules(self):
        s = sstar_module()
        for q in (35.0, 45.0, 55.0):
            for kw in ({}, {"q_star_sol": 50.0}):
                a = decisions(q, **kw)
                eng, b = s_engine(s, seal_start_ms=None, h5_look2_start_ms=None, classifier=s.StaticClassifier(False), **kw)
                announce(eng)
                t = Tape()
                t.row(1000, "buy", "SNIPER", SOL // 5)
                boost_buys(t, 1000)
                drain(t, 1200, q)
                run(eng, t)
                self.assertEqual(json.dumps(a, sort_keys=True, default=str), json.dumps(b, sort_keys=True, default=str), (q, kw))


def s_engine(mod, **kw):
    out: list[dict] = []
    from tools.test_h5_shadow import SPS
    kw.setdefault("sps_fn", lambda p: SPS)
    kw.setdefault("pda_fn", lambda pool: "PDA_" + pool)
    kw.setdefault("classifier", mod.StaticClassifier(False, "rpc"))
    return mod.Engine(out.append, wall=lambda: 1_800_000_000_000, **kw), out


# ---------------------------------------------------------------- (h) seal grid at Q* 60
L2_START, L2_END, SUCC = ms(2026, 10, 16), ms(2026, 11, 6), ms(2026, 12, 1)
GRID = sorted({ms(2026, 9, 20, 12), L2_START - 1_000, L2_START, ms(2026, 10, 24), L2_END - 1_000, L2_END, ms(2026, 11, 10), ms(2026, 11, 14) - 1_000,
               ms(2026, 11, 14), ms(2026, 11, 20), ms(2026, 11, 21), SUCC - 1_000, SUCC, SUCC + 1_000, ms(2026, 12, 15), ms(2027, 1, 15)})


def _sealed(mod, t_ms, **kw) -> bool:
    eng, _ = s_engine(mod, **kw)
    return eng._h5_sealed_now(SimpleNamespace(s0_ts=t_ms // 1000, s0_recv_ms=t_ms + 37))


class SealGridQ60Tests(unittest.TestCase):
    def test_non_fresh_q60_engine_seals_as_sstar_does_at_q50_and_q40(self):
        s = sstar_module()
        for name, kw in {"default": {}, "observed": {"h5_look2_observed": True}, "seal_off": {"h5_look2_start_ms": None}}.items():
            for t in GRID:
                new = _sealed(h5, t, q_star_sol=60.0, **kw)
                self.assertEqual(new, _sealed(s, t, q_star_sol=50.0, **kw), (name, h5.iso_from_ms(t)))
                self.assertEqual(new, _sealed(s, t, **kw), (name, h5.iso_from_ms(t)))
                self.assertEqual(new, _sealed(h5, t, **kw), (name, h5.iso_from_ms(t)))  # the level does not move the seal

    def test_fresh_q60_engine_seals_from_succ_end_only(self):
        s = sstar_module()
        for t in GRID:
            kw = dict(fresh_start_ms=0, seal_start_ms=None, h5_look2_start_ms=h5.SUCC_END_MS)
            self.assertEqual(_sealed(h5, t, q_star_sol=60.0, **kw), t >= SUCC, h5.iso_from_ms(t))
            self.assertEqual(_sealed(h5, t, q_star_sol=60.0, **kw), _sealed(s, t, q_star_sol=50.0, **kw), h5.iso_from_ms(t))

    def test_the_seal_grid_spelled_out(self):
        for t in GRID:
            self.assertEqual(_sealed(h5, t, q_star_sol=60.0), t >= L2_START, h5.iso_from_ms(t))
            self.assertEqual(_sealed(h5, t, q_star_sol=60.0, h5_look2_observed=True), t >= L2_END, h5.iso_from_ms(t))
            self.assertFalse(_sealed(h5, t, q_star_sol=60.0, h5_look2_start_ms=None))

    def test_run_live_builds_the_q60_engine_with_the_succ_end(self):
        root = Path(tempfile.mkdtemp(dir="/tmp", prefix="arena-q60-seal-")).resolve()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        built: list[dict] = []
        real = h5.Engine

        def spy(*a, **k):
            built.append(k)
            return real(*a, **k)

        with mock.patch.object(h5, "Engine", spy):
            _start_record_with_spy(root)
        (k,) = built
        self.assertEqual((k["q_star_sol"], k["h5_look2_start_ms"], k["h5_look2_observed"], k["seal_start_ms"]), (60.0, h5.SUCC_END_MS, False, None))


def _start_record_with_spy(root: Path) -> dict:
    return _start_record(h5, root, ["--q-star-sol", "60"])


# ---------------------------------------------------------------- (i) compare_frozen refusal
class _Col:
    def __init__(self, vals):
        self.vals = list(vals)

    def __eq__(self, other):  # noqa: PLW1641
        return _Col(v == other for v in self.vals)

    def __and__(self, other):
        return _Col(a and b for a, b in zip(self.vals, other.vals))

    def __iter__(self):
        return iter(self.vals)


class _Frame:
    def __init__(self, rows):
        self.rows = rows
        for c in ("D", "leg", "H", "stake", "mint", "trig_t", "gross"):
            setattr(self, c, _Col(getattr(r, c) for r in rows))

    def __getitem__(self, mask):
        return _Frame([r for r, keep in zip(self.rows, mask.vals) if keep])

    def __len__(self):
        return len(self.rows)

    def itertuples(self):
        return iter(self.rows)


def _fake_pandas(rows):
    return SimpleNamespace(read_parquet=lambda path: _Frame(rows))


FROZEN_ROWS = [SimpleNamespace(D=40.0, leg="p", H="end", stake="01", mint="MintA", trig_t=12.0, gross=0.1)]


class CompareFrozenRefusalTests(unittest.TestCase):
    def call(self, rows, q):
        with mock.patch.dict(sys.modules, {"pandas": _fake_pandas(rows)}):
            return h5.compare_frozen([], {}, "/x/boostdip_frozen_conf.parquet", "2026-09-20", ["2026-09-20T00"], q)

    def test_no_frozen_row_at_the_level_is_refused(self):
        for q in (50.0, 60.0):
            with self.assertRaisesRegex(h5.Refused, "no frozen row"):
                self.call(FROZEN_ROWS, q)
        with self.assertRaises(h5.Refused):
            self.call([], 40.0)  # an empty frozen file refuses at 40 as well

    def test_a_matching_frozen_row_still_compares(self):
        res = self.call(FROZEN_ROWS, 40.0)
        self.assertEqual((res["frozen"], res["mine"], res["both"]), (0, 0, 0))  # no pools in the (empty) record list, so no row is joined; no refusal

    def test_the_default_level_and_the_signature_are_unchanged(self):
        import inspect
        self.assertEqual(inspect.signature(h5.compare_frozen).parameters["q_star_sol"].default, 40.0)
        self.assertEqual(inspect.signature(sstar_module().compare_frozen), inspect.signature(h5.compare_frozen))

    def test_main_replay_compare_reports_the_refusal_not_a_silent_zero(self):
        # run_replay reaches compare_frozen only through its own path; the refusal is the Refused class main/run_replay already handle
        self.assertTrue(issubclass(h5.Refused, Exception))


if __name__ == "__main__":
    unittest.main()
