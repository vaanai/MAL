"""Tests for tools/h5_tipauth_decision_change.py (the counts-only parser differential and decision-change account). Synthetic files only.

  python3 -m unittest tools.test_h5_tipauth_decision_change

Two small fake executor modules (written to a temp dir) carry most of the tests, so they do not depend on git objects. One class loads the real head
file (tools/h5_executor.py of this checkout) and, if the object is in the clone, the installed build b7ddeb60 through --base-git-sha; it skips with a
printed reason when the executor cannot be imported here (for example no `solders`) or the object is missing."""
from __future__ import annotations

import collections
import contextlib
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tools import h5_tipauth_decision_change as D
from tools import h5_tipauth_item4 as I4
from tools.test_h5_tipauth_item4 import H, MID, T0, bt, hb, start, trig

SHA = "5db78a663a0617527daacb22f5ce6c1a7e92dfa3"
INSTALLED_SHA = "b7ddeb60aa4ced9243211775cf3d5a22ccc42dff"
WS = "bad_intent:base_breaks_unresolved_settled"
UNTIL = T0 + H - 1
HEAD_REAL = Path(D.__file__).with_name("h5_executor.py")

FAKE_BASE = '''
from dataclasses import dataclass
WS = "bad_intent:base_breaks_unresolved_settled"
TRIGGER_VARIANT = "pv"


@dataclass(frozen=True)
class H5Trigger:
    mint: str
    pool: str
    q: int


def _isint(v):
    return isinstance(v, int) and not isinstance(v, bool)


def parse_shadow_trigger(row, variant="pv"):
    if not isinstance(row, dict) or row.get("type") != "trigger" or row.get("variant") != variant:
        return None, None
    if row.get("suppressed"):
        return None, "bad_intent:suppressed"
    if not _isint(row.get("q")):
        return None, "bad_intent:q"
    ws = row.get("base_breaks_unresolved_settled")
    if not _isint(ws) or ws != 0:
        return None, WS
    if row.get("boom"):
        raise ValueError("boom")
    if row.get("late"):
        return None, "bad_intent:late"
    return H5Trigger(row["mint"], row["pool"], row["q"]), None
'''

GATES = {
    "item4": "ok, why = I4.would_pass(rec)\n    return (True, 'ok') if ok else (False, 'tipauth:' + why)",
    "lenient": "return True, 'ok'",
    "strict": "return False, 'tipauth:nope'",
    "reason": "ok, why = I4.would_pass(rec)\n    return (True, 'ok') if ok else (False, 'tipauth:' + why + '_x')",
}
AVAIL = {
    "clear": "class H5Executor:\n    def _tipauth_availability(self):\n        return '' if self.exit_mode == 'v1' and not self.counters.tipauth.get('latched') else 'tipauth:latched_off'\n",
    "none": "",
    "latched": "class H5Executor:\n    def _tipauth_availability(self):\n        return 'tipauth:latched_off'\n",
    "raises": "class H5Executor:\n    def _tipauth_availability(self):\n        raise RuntimeError('x')\n",
}

FAKE_HEAD = '''
from dataclasses import dataclass
from tools import h5_tipauth_item4 as I4
WS = "bad_intent:base_breaks_unresolved_settled"
BASE_BREAKS_WS_REFUSAL = WS
TRIGGER_VARIANT = "pv"
EXIT_MODE_V1 = "v1"
PLANT = @PLANT@


@dataclass(frozen=True)
class H5Trigger:
    mint: str
    pool: str
    q: int
    gate_path: str = None


def _isint(v):
    return isinstance(v, int) and not isinstance(v, bool)


def tipauth_gate(rec):
    @GATE@


def parse_shadow_trigger(row, variant="pv", tipauth=None):
    if not isinstance(row, dict) or row.get("type") != "trigger" or row.get("variant") != variant:
        return None, None
    if row.get("suppressed"):
        if PLANT == "other_pass" and tipauth is not None:
            return H5Trigger(row["mint"], row["pool"], 1, "tipauth"), None
        return None, "bad_intent:suppressed"
    if not _isint(row.get("q")):
        if PLANT == "other_reason" and tipauth is not None:
            return None, "bad_intent:q2"
        return None, "bad_intent:q"
    q = row["q"]
    if PLANT == "b_q13" and q == 13:
        return None, "bad_intent:q"
    if PLANT == "b_fields" and q == 14:
        return H5Trigger(row["mint"], row["pool"], q + 1, None), None
    if PLANT == "b_raise" and q == 17:
        raise KeyError("x")
    if PLANT == "many" and q >= 100:
        return None, "bad_intent:many%d" % q
    ws = row.get("base_breaks_unresolved_settled")
    gate_path = None
    if not _isint(ws) or ws != 0:
        if tipauth is None or not _isint(ws) or ws < 1:
            return None, WS
        if tipauth:
            return None, tipauth
        ok, why = tipauth_gate(row)
        if not ok:
            return None, why
        gate_path = "tipauth"
    elif PLANT == "p2r" and tipauth is not None and q == 15:
        return None, "tipauth:oops"
    if row.get("boom"):
        raise ValueError("boom")
    if row.get("late"):
        return None, "bad_intent:late"
    return H5Trigger(row["mint"], row["pool"], q, gate_path), None


@AVAIL@
'''


def head_src(plant: str = "", gate: str = "item4", avail: str = "clear") -> str:
    return FAKE_HEAD.replace("@PLANT@", repr(plant)).replace("@GATE@", GATES[gate]).replace("@AVAIL@", AVAIL[avail])


class Gen:
    """Unique mints, pools and signatures, remembered so a test can look for them in the output."""

    def __init__(self) -> None:
        self.n = 0
        self.ids: list[str] = []

    def nxt(self) -> int:
        self.n += 1
        i = self.n
        self.ids += [f"MINTx{i:05d}", f"POOLx{i:05d}", f"SIGx{i:05d}"]
        return i

    def trig(self, **kw):
        i = self.nxt()
        d = dict(t=MID + i, pool=f"POOLx{i:05d}", btd=bt(dropped=[[f"SIGx{i:05d}", 0, 105]]), mint=f"MINTx{i:05d}", q=1)
        d.update(kw)
        return trig(**d)


def write_hour(d: Path, items, hour_ms: int = T0) -> None:
    name = "h5-shadow-" + time.strftime("%Y-%m-%dT%H", time.gmtime(hour_ms // 1000)) + ".jsonl"
    with open(d / name, "ab") as fh:
        for it in items:
            raw = it if isinstance(it, bytes) else (it.encode() if isinstance(it, str) else json.dumps(it).encode())
            fh.write(raw + b"\n")


def fake_corpus(g: Gen) -> list:
    """22 trigger records in the window (18 unsealed, 4 sealed) and 2 outside it. See test_counts_of_the_fake_corpus for the expected numbers."""
    items: list = [start(t=T0 + 1000, sha=SHA)]
    items += [g.trig(ws=0, uval=0) for _ in range(3)]  # websocket passes
    items += [g.trig() for _ in range(4)]  # ws 1, would-pass: the path admits them
    items += [g.trig(late=True) for _ in range(2)]  # ws 1, would-pass, then refused by a later check of the parser
    items += [g.trig(gap=True), g.trig(gap=True), g.trig(tval=1)]  # ws 1, the gate refuses
    items += [g.trig(variant="fv"), g.trig(variant="fv", ws=0, uval=0)]  # not the traded variant: ignored
    items += [g.trig(suppressed=True, ws=0, uval=0)]  # the sealed-stub shape
    items += [dict(g.trig(ws=0, uval=0), q=None)]  # a malformed value
    items += [g.trig(sealed=True), g.trig(sealed=True, h5_look2_sealed=True), g.trig(h5_look2_sealed=1), g.trig(sealed="yes")]  # excluded and counted
    items += [g.trig(sealed=False), g.trig(sealed=None, h5_look2_sealed=False)]  # not sealed: read; ws 1 would-pass: admitted by the path
    items += [g.trig(t=T0 + 500), g.trig(t=UNTIL + 1)]  # before the origin, after until
    items += [{"type": "gap", "t_ms": MID, "kind": "reconnect"}, hb(MID)]
    return items


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.shadow = self.tmp / "shadow"
        self.shadow.mkdir()
        self.n_run = 0

    def put(self, name: str, text: str) -> Path:
        p = self.tmp / name
        p.write_text(text)
        return p

    def run_tool(self, items=None, base_src: str = FAKE_BASE, head: str | None = None, argv_extra=(), expect: str = SHA, since: int = T0, until: int = UNTIL,
                 base_args=None, write: bool = True):
        if items is not None and write:
            write_hour(self.shadow, items)
        self.n_run += 1
        base_p = self.put(f"base{self.n_run}.py", base_src)
        head_p = self.put(f"head{self.n_run}.py", head if head is not None else head_src())
        argv = ["--shadow-dir", str(self.shadow), "--since-ms", str(since), "--until-ms", str(until), "--expect-code-sha", expect,
                *(base_args if base_args is not None else ["--base-file", str(base_p)]), "--head-file", str(head_p), *argv_extra]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = D.main(argv)
        return code, json.JSONDecoder().raw_decode(buf.getvalue())[0], buf.getvalue()


def class_map(res: dict, section: str) -> dict:
    return {(c["reason_a"], c["reason_b"]): c["n"] for c in section["difference_classes"]}


class FakeCorpusTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.g = Gen()
        self.code, self.res, self.text = self.run_tool(fake_corpus(self.g))

    def test_counts_of_the_fake_corpus(self):
        r = self.res
        self.assertEqual(self.code, 0)
        rec = r["records"]
        self.assertEqual(rec["trigger_records_in_window"], 22)
        self.assertEqual(rec["sealed_excluded"], {"sealed": 3, "h5_look2_sealed_only": 1, "both_flags": 1, "total": 4})
        self.assertEqual(rec["unsealed_read"], 18)
        self.assertEqual(rec["by_variant"], {"pv": 16, "fv": 2, "other": 0})
        self.assertEqual(rec["suppressed_stubs"], 1)
        self.assertEqual(r["scan"]["trigger_records_outside_window"], 2)
        self.assertEqual(r["window"]["origin_t_ms"], T0 + 1000)
        self.assertTrue(r["window"]["origin_code_sha_matches"])

    def test_path_off_is_identical_to_base(self):
        ab = self.res["a_vs_b"]
        for k in ("head_path_off_default_call", "head_path_off_tipauth_none"):
            self.assertEqual(ab[k]["records_compared"], 18)
            self.assertEqual(ab[k]["records_differ"], 0)
            self.assertEqual(ab[k]["difference_classes"], [])
        self.assertEqual(self.res["verdict"], {k: True for k in D.VERDICT_KEYS})

    def test_refusals_that_become_passes_and_the_rest_of_the_transition_table(self):
        c = self.res["a_vs_c"]
        self.assertEqual(c["a_passes"], 3)
        self.assertEqual(c["c_passes"], 9)
        self.assertEqual(c["a_ws_refusals"], 11)
        self.assertEqual(c["refusals_to_passes"], {"total": 6, "by_a_reason": {WS: 6}, "gate_path_tipauth": 6})
        self.assertEqual(c["ws_refusals_still_refused"]["by_tipauth_reason_decision_unchanged"], {"tipauth:gap": 2, "tipauth:value_not_zero": 1})
        self.assertEqual(c["ws_refusals_still_refused"]["by_later_check_reason"], {"bad_intent:late": 2})
        self.assertEqual(c["ws_refusals_still_refused"]["total"], 5)
        self.assertEqual(c["passes_to_refusals"], {"total": 0, "by_c_reason": {}})
        self.assertEqual(c["other_changes"]["total"], 0)
        self.assertEqual(c["ws_refusals_that_become_exceptions"], 0)
        tr = {(t["a"], t["c"]): t["n"] for t in c["transitions"]}
        self.assertEqual(tr[("PASS", "PASS")], 3)
        self.assertEqual(tr[(WS, "PASS")], 6)
        self.assertEqual(tr[(WS, "bad_intent:late")], 2)
        self.assertEqual(tr[(WS, "tipauth:gap")], 2)
        self.assertEqual(tr[("IGNORED", "IGNORED")], 2)
        self.assertEqual(tr[("bad_intent:suppressed", "bad_intent:suppressed")], 1)
        self.assertEqual(tr[("bad_intent:q", "bad_intent:q")], 1)
        self.assertEqual(sum(tr.values()), c["records"])
        self.assertEqual(c["unchanged"] + c["refusals_to_passes"]["total"] + c["ws_refusals_still_refused"]["total"], c["records"])

    def test_the_item4_predicate_agrees(self):
        w = self.res["would_pass"]
        self.assertEqual(w["item4_predicate_true"], 8)
        self.assertEqual(w["item4_predicate_true_by_c_outcome"], {"PASS": 6, "bad_intent:late": 2})
        self.assertEqual((w["refusals_to_passes"], w["refusals_to_passes_item4_true"]), (6, 6))
        self.assertEqual(w["disagreements"], {"became_pass_item4_false": 0, "item4_true_gate_refused": 0, "gate_reason_differs": 0, "total": 0})

    def test_the_selection_equals_the_item4_tools(self):
        r4 = I4.analyze(self.shadow, T0, UNTIL, expect_code_sha=SHA)
        r = self.res
        self.assertEqual(r["records"]["in_item4_selection"], r4["records"]["triggers"])
        self.assertEqual(r["would_pass"]["item4_predicate_true_in_item4_selection"], r4["item6_would_pass"]["would_pass_n"])
        sk = r4["skipped"]
        se = r["records"]["sealed_excluded"]
        self.assertEqual((se["sealed"], se["h5_look2_sealed_only"], se["both_flags"]), (sk["sealed_trigger"], sk["h5_look2_sealed_trigger"], sk["both_flags"]))

    def test_the_verdict_has_exactly_the_required_zero_booleans(self):
        self.assertEqual(sorted(self.res["verdict"]), sorted(D.VERDICT_KEYS))
        self.assertTrue(all(isinstance(v, bool) for v in self.res["verdict"].values()))

    def test_no_forbidden_value_or_key_in_the_output(self):
        for ident in self.g.ids:
            self.assertNotIn(ident, self.text, ident)

        def keys(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    yield k
                    yield from keys(v)
            elif isinstance(o, list):
                for v in o:
                    yield from keys(v)
        bad = {"mint", "pool", "signature", "sig", "slot", "price", "reserve", "amount", "dropped", "mints", "pools", "signatures"}
        self.assertFalse(bad & set(keys(self.res)))

    def test_hashes_and_sources(self):
        r = self.res
        self.assertEqual(r["tool_sha256"], hashlib.sha256(Path(D.__file__).read_bytes()).hexdigest())
        self.assertEqual(r["base"]["sha256"], hashlib.sha256(FAKE_BASE.encode()).hexdigest())
        self.assertEqual(r["head"]["sha256"], hashlib.sha256(head_src().encode()).hexdigest())
        self.assertFalse(r["base"]["parse_has_tipauth_parameter"])
        self.assertEqual(r["head"]["trigger_fields_only_in_head"], ["gate_path"])
        self.assertTrue(r["availability"]["source"].startswith("H5Executor._tipauth_availability on a stub"))
        self.assertEqual(r["availability"]["tipauth_argument_for_C"], "")


class ScanTests(Base):
    def test_malformed_partial_nan_and_pool_not_str(self):
        g = Gen()
        items = [start(t=T0 + 1000, sha=SHA), g.trig(ws=0, uval=0), g.trig(pool=7), g.trig(ws=float("nan"), uval=0), g.trig(variant="zz"), g.trig(ws=0, uval=0, variant=["x"]),
                 "{not json", "[1, 2]", b"\xff\xfe\x00 not utf8", '{"type": "trigger", "variant": "pv", "pool": "PO']  # the last line is cut off
        code, r, text = self.run_tool(items)
        self.assertEqual(code, 0)
        s = r["scan"]
        self.assertEqual((s["bad_line"], s["bad_line_not_utf8"]), (4, 1))
        self.assertEqual(s["trigger_records_parsed"], 5)
        self.assertEqual(s["trigger_pattern_matches"], 6)  # the cut-off line matches the pattern and does not parse
        self.assertFalse(s["trigger_pattern_matches_equal_trigger_records_parsed"])
        rec = r["records"]
        self.assertEqual(rec["unsealed_read"], 5)
        self.assertEqual(rec["by_variant"], {"pv": 3, "fv": 0, "other": 2})
        self.assertEqual(rec["pool_not_str"], 1)
        self.assertEqual(rec["not_strict_json"], 1)  # the NaN line: the executor's json.loads reads it, the item-4 loader does not
        r4 = I4.analyze(self.shadow, T0, UNTIL, expect_code_sha=SHA)
        self.assertEqual(rec["in_item4_selection"], r4["records"]["triggers"])
        self.assertEqual(rec["in_item4_selection"], 1)
        self.assertEqual(rec["pool_not_str"], r4["skipped"]["record_pool_not_str"])

    def test_clean_files_match_pattern_equals_parsed(self):
        g = Gen()
        _, r, _ = self.run_tool([start(t=T0 + 1000, sha=SHA), g.trig(), hb(MID)])
        self.assertTrue(r["scan"]["trigger_pattern_matches_equal_trigger_records_parsed"])
        self.assertEqual(r["scan"]["files"], ["h5-shadow-" + time.strftime("%Y-%m-%dT%H", time.gmtime(T0 // 1000)) + ".jsonl"])

    def test_last_line_without_newline_is_counted(self):
        g = Gen()
        write_hour(self.shadow, [start(t=T0 + 1000, sha=SHA), g.trig()])
        name = next(self.shadow.iterdir())
        with open(name, "ab") as fh:
            fh.write(json.dumps(g.trig(ws=0, uval=0)).encode())  # no newline
        _, r, _ = self.run_tool()
        self.assertEqual(r["scan"]["files_last_line_without_newline"], 1)
        self.assertEqual(r["records"]["unsealed_read"], 2)

    def test_window_bounds_are_inclusive_and_origin_is_the_last_start(self):
        g = Gen()
        items = [start(t=T0 + 100, sha="a" * 40), g.trig(t=T0 + 200), start(t=T0 + 1000, sha=SHA), g.trig(t=T0 + 999), g.trig(t=T0 + 1000), g.trig(t=UNTIL), g.trig(t=UNTIL + 1)]
        _, r, _ = self.run_tool(items)
        self.assertEqual(r["window"]["n_starts_in_range"], 2)
        self.assertEqual(r["records"]["unsealed_read"], 2)  # T0+1000 and UNTIL
        self.assertEqual(r["scan"]["trigger_records_outside_window"], 3)

    def test_a_record_without_an_integer_t_ms_is_counted_and_not_parsed(self):
        g = Gen()
        _, r, _ = self.run_tool([start(t=T0 + 1000, sha=SHA), dict(g.trig(), t_ms=None), dict(g.trig(), t_ms=True), g.trig()])
        self.assertEqual(r["scan"]["trigger_records_without_int_t_ms"], 2)
        self.assertEqual(r["records"]["unsealed_read"], 1)

    def test_a_sealed_record_never_reaches_a_parser(self):
        g = Gen()
        spy = head_src() + "\nSEEN = []\n_orig = parse_shadow_trigger\n\ndef parse_shadow_trigger(row, variant='pv', tipauth=None):\n    SEEN.append(row.get('mint'))\n    return _orig(row, variant, tipauth=tipauth)\n"
        sealed = [g.trig(sealed=True), g.trig(h5_look2_sealed=True), g.trig(sealed=1, h5_look2_sealed="x")]
        keep = g.trig()
        code, r, _ = self.run_tool([start(t=T0 + 1000, sha=SHA), *sealed, keep], head=spy)
        self.assertEqual(code, 0)
        seen = set(sys.modules[D.HEAD_MODNAME].SEEN)
        self.assertEqual(seen, {keep["mint"]})
        self.assertEqual(r["records"]["sealed_excluded"]["total"], 3)

    def test_reads_only_the_shadow_dir_and_the_executor_files(self):
        g = Gen()
        write_hour(self.shadow, [start(t=T0 + 1000, sha=SHA), g.trig(), g.trig(sealed=True)])
        base_p, head_p = self.put("b.py", FAKE_BASE), self.put("h.py", head_src())
        seen: list[str] = []
        real_open = open

        def spy(file, *a, **k):
            seen.append(str(file))
            return real_open(file, *a, **k)
        argv = ["--shadow-dir", str(self.shadow), "--since-ms", str(T0), "--until-ms", str(UNTIL), "--expect-code-sha", SHA, "--base-file", str(base_p), "--head-file", str(head_p)]
        with mock.patch("builtins.open", spy), mock.patch("io.open", spy), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(D.main(argv), 0)
        allowed = (str(self.shadow), str(base_p), str(head_p), str(Path(D.__file__)))
        outside = [p for p in seen if not p.startswith(allowed)]
        self.assertEqual(outside, [])
        self.assertTrue(any(p.startswith(str(self.shadow)) for p in seen))


class PlantedTests(Base):
    def corpus(self, g, rows):
        return [start(t=T0 + 1000, sha=SHA), *rows]

    def test_a_planted_difference_on_the_path_off_side_is_counted_and_classed(self):
        g = Gen()
        rows = [g.trig(ws=0, uval=0, q=13), g.trig(ws=0, uval=0, q=13), g.trig(ws=0, uval=0, q=14), g.trig(ws=0, uval=0, q=17), g.trig(ws=0, uval=0, q=1)]
        code, r, text = self.run_tool(self.corpus(g, rows), head=head_src("b_q13"))
        self.assertEqual(code, 0)
        d = r["a_vs_b"]["head_path_off_default_call"]
        self.assertEqual(d["records_differ"], 2)
        self.assertEqual(class_map(r, d), {("PASS", "bad_intent:q"): 2})
        code, r, text = self.run_tool(None, head=head_src("b_fields"), write=False)
        d = r["a_vs_b"]["head_path_off_tipauth_none"]
        self.assertEqual((d["records_differ"], class_map(r, d), d["differing_trigger_field_names"]), (1, {("PASS", "PASS:fields_differ"): 1}, {"q": 1}))
        code, r, _ = self.run_tool(None, head=head_src("b_raise"), write=False)
        d = r["a_vs_b"]["head_path_off_default_call"]
        self.assertEqual(class_map(r, d), {("PASS", "raise:KeyError"): 1})
        self.assertFalse(r["verdict"]["a_vs_b_default_zero_differences"])
        self.assertFalse(r["verdict"]["a_vs_b_tipauth_none_zero_differences"])
        for ident in g.ids:
            self.assertNotIn(ident, text)

    def test_difference_classes_are_listed_up_to_twenty(self):
        g = Gen()
        rows = [g.trig(ws=0, uval=0, q=100 + i) for i in range(25)]
        _, r, _ = self.run_tool(self.corpus(g, rows), head=head_src("many"))
        d = r["a_vs_b"]["head_path_off_default_call"]
        self.assertEqual((d["records_differ"], d["difference_classes_total"], len(d["difference_classes"]), d["difference_classes_truncated"]), (25, 25, 20, True))

    def test_an_exception_on_both_sides_with_the_same_type_is_no_difference(self):
        g = Gen()
        _, r, _ = self.run_tool(self.corpus(g, [g.trig(ws=0, uval=0, boom=True)]))
        self.assertEqual(r["a_vs_b"]["head_path_off_default_call"]["records_differ"], 0)
        self.assertEqual(r["a_vs_c"]["unchanged"], 1)
        self.assertEqual({(t["a"], t["c"]) for t in r["a_vs_c"]["transitions"]}, {("raise:ValueError", "raise:ValueError")})

    def test_a_pass_that_becomes_a_refusal_is_found(self):
        g = Gen()
        rows = [g.trig(ws=0, uval=0, q=15), g.trig(ws=0, uval=0, q=15), g.trig(ws=0, uval=0, q=2)]
        _, r, _ = self.run_tool(self.corpus(g, rows), head=head_src("p2r"))
        c = r["a_vs_c"]
        self.assertEqual(c["passes_to_refusals"], {"total": 2, "by_c_reason": {"tipauth:oops": 2}})
        self.assertEqual(c["other_changes"]["total"], 0)
        self.assertTrue(r["verdict"]["a_vs_b_default_zero_differences"])
        self.assertFalse(r["verdict"]["passes_to_refusals_zero"])
        self.assertTrue(r["verdict"]["other_changes_zero"])

    def test_a_refusal_that_becomes_a_pass_from_another_reason_is_also_an_other_change(self):
        g = Gen()
        _, r, _ = self.run_tool(self.corpus(g, [g.trig(ws=0, uval=0, suppressed=True), g.trig()]), head=head_src("other_pass"))
        c = r["a_vs_c"]
        self.assertEqual(c["refusals_to_passes"]["by_a_reason"], {"bad_intent:suppressed": 1, WS: 1})
        self.assertEqual(c["refusals_to_passes"]["gate_path_tipauth"], 2)
        self.assertEqual(c["other_changes"]["total"], 1)
        self.assertEqual(c["other_changes"]["classes"], [{"a": "bad_intent:suppressed", "c": "PASS", "n": 1}])
        self.assertFalse(r["verdict"]["other_changes_zero"])

    def test_a_refusal_that_changes_reason_is_an_other_change(self):
        g = Gen()
        _, r, _ = self.run_tool(self.corpus(g, [dict(g.trig(ws=0, uval=0), q=None)]), head=head_src("other_reason"))
        self.assertEqual(r["a_vs_c"]["other_changes"]["classes"], [{"a": "bad_intent:q", "c": "bad_intent:q2", "n": 1}])
        self.assertTrue(r["verdict"]["a_vs_b_default_zero_differences"])
        self.assertFalse(r["verdict"]["other_changes_zero"])

    def test_a_websocket_refusal_that_becomes_an_exception_is_an_other_change(self):
        g = Gen()
        _, r, _ = self.run_tool(self.corpus(g, [g.trig(boom=True), g.trig()]))
        c = r["a_vs_c"]
        self.assertEqual(c["ws_refusals_that_become_exceptions"], 1)
        self.assertEqual(c["other_changes"]["classes"], [{"a": WS, "c": "raise:ValueError", "n": 1}])
        self.assertEqual(c["refusals_to_passes"]["total"], 1)
        self.assertFalse(r["verdict"]["other_changes_zero"])
        self.assertTrue(r["verdict"]["a_vs_b_default_zero_differences"])

    def test_a_gate_that_admits_what_the_item4_predicate_refuses_is_a_disagreement(self):
        g = Gen()
        rows = [g.trig(), g.trig(gap=True), g.trig(tval=1)]
        _, r, _ = self.run_tool(self.corpus(g, rows), head=head_src(gate="lenient"))
        w = r["would_pass"]
        self.assertEqual(w["disagreements"]["became_pass_item4_false"], 2)
        self.assertEqual(w["refusals_to_passes_item4_true"], 1)
        self.assertFalse(r["verdict"]["would_pass_disagreements_zero"])

    def test_a_gate_that_refuses_what_the_item4_predicate_passes_is_a_disagreement(self):
        g = Gen()
        _, r, _ = self.run_tool(self.corpus(g, [g.trig(), g.trig(), g.trig(gap=True)]), head=head_src(gate="strict"))
        d = r["would_pass"]["disagreements"]
        self.assertEqual(d["item4_true_gate_refused"], 2)
        self.assertEqual(d["gate_reason_differs"], 1)  # gap: item-4 says "gap", the gate says "nope"
        self.assertFalse(r["verdict"]["would_pass_disagreements_zero"])

    def test_a_gate_whose_reason_differs_is_a_disagreement(self):
        g = Gen()
        _, r, _ = self.run_tool(self.corpus(g, [g.trig(gap=True), g.trig()]), head=head_src(gate="reason"))
        self.assertEqual(r["would_pass"]["disagreements"], {"became_pass_item4_false": 0, "item4_true_gate_refused": 0, "gate_reason_differs": 1, "total": 1})


class AvailabilityTests(Base):
    def run_av(self, avail):
        g = Gen()
        return self.run_tool([start(t=T0 + 1000, sha=SHA), g.trig()], head=head_src(avail=avail))

    def test_a_head_without_an_availability_function_gets_the_documented_empty_string(self):
        code, r, _ = self.run_av("none")
        self.assertEqual(code, 0)
        self.assertEqual(r["availability"]["tipauth_argument_for_C"], "")
        self.assertIn("literal empty string", r["availability"]["source"])
        self.assertEqual(r["a_vs_c"]["refusals_to_passes"]["total"], 1)

    def test_a_head_that_says_unavailable_on_an_all_clear_state_is_an_input_error(self):
        code, r, text = self.run_av("latched")
        self.assertEqual(code, 7)
        self.assertNotIn("records", r)

    def test_an_availability_function_that_raises_is_reported(self):
        code, r, _ = self.run_av("raises")
        self.assertEqual(code, 0)
        self.assertIn("raised RuntimeError", r["availability"]["source"])


class InputErrorTests(Base):
    def test_expect_code_sha_mismatch_is_an_input_error_with_no_counts_and_no_file(self):
        g = Gen()
        out = self.tmp / "out.json"
        code, r, text = self.run_tool([start(t=T0 + 1000, sha=SHA), g.trig()], expect="b" * 40, argv_extra=["--out", str(out)])
        self.assertEqual(code, 5)
        self.assertEqual(set(r), {"tool", "error", "exit_code"})
        self.assertFalse(out.exists())
        for ident in g.ids:
            self.assertNotIn(ident, text)

    def test_a_malformed_expect_code_sha_is_a_usage_error(self):
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(io.StringIO()):
            self.run_tool([start(t=T0 + 1000, sha=SHA)], expect="abc")
        self.assertEqual(cm.exception.code, 2)

    def test_no_start_record_and_missing_directory(self):
        g = Gen()
        code, r, _ = self.run_tool([g.trig()])
        self.assertEqual(code, 4)
        self.assertNotIn("records", r)
        shutil.rmtree(self.shadow)
        code, r, _ = self.run_tool(None, write=False)
        self.assertEqual(code, 3)

    def test_base_arguments_are_exactly_one_of_file_or_git_sha(self):
        base_p = self.put("b.py", FAKE_BASE)
        head_p = self.put("h.py", head_src())
        common = ["--shadow-dir", str(self.shadow), "--since-ms", str(T0), "--until-ms", str(UNTIL), "--expect-code-sha", SHA, "--head-file", str(head_p)]
        for extra in ([], ["--base-file", str(base_p), "--base-git-sha", INSTALLED_SHA], ["--base-git-sha", "HEAD"]):
            with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(io.StringIO()):
                D.main(common + extra)
            self.assertEqual(cm.exception.code, 2, extra)

    def test_executor_file_problems_are_input_errors(self):
        g = Gen()
        items = [start(t=T0 + 1000, sha=SHA), g.trig()]
        write_hour(self.shadow, items)
        code, r, _ = self.run_tool(None, write=False, base_args=["--base-file", str(self.tmp / "missing.py")])
        self.assertEqual(code, 7)
        code, r, _ = self.run_tool(None, write=False, head=FAKE_BASE)  # a head without the tipauth parameter
        self.assertEqual(code, 7)
        self.assertIn("tipauth", r["error"])
        code, r, _ = self.run_tool(None, write=False, head="raise RuntimeError('import failed')\n")
        self.assertEqual(code, 7)
        code, r, _ = self.run_tool(None, write=False, head="x = 1\n")
        self.assertEqual(code, 7)
        code, r, _ = self.run_tool(None, write=False, base_args=["--base-git-sha", "0" * 40])  # no such object
        self.assertEqual(code, 7)
        self.assertNotIn("records", r)

    def test_out_is_created_exclusively_and_never_overwritten(self):
        g = Gen()
        items = [start(t=T0 + 1000, sha=SHA), g.trig()]
        out = self.tmp / "result.json"
        code, r, text = self.run_tool(items, argv_extra=["--out", str(out)])
        self.assertEqual(code, 0)
        self.assertEqual(out.read_text(), text)
        before = out.read_bytes()
        code, r, text2 = self.run_tool(None, write=False, argv_extra=["--out", str(out)])
        self.assertEqual(code, 6)
        self.assertEqual(out.read_bytes(), before)
        self.assertEqual(set(r), {"tool", "error", "exit_code"})
        pre = self.tmp / "pre.json"
        pre.write_text("keep")
        code, _, _ = self.run_tool(None, write=False, argv_extra=["--out", str(pre)])
        self.assertEqual((code, pre.read_text()), (6, "keep"))

    def test_the_head_file_defaults_to_this_checkouts_executor(self):
        self.assertEqual(D.HEAD_DEFAULT, D.ROOT / "tools" / "h5_executor.py")
        self.assertTrue(D.HEAD_DEFAULT.is_file())


# ---- the real executor files ---------------------------------------------------------------------------------------------------------------------

def real_row(g: Gen, ws: int = 0, mut=None, **over) -> dict:
    """A shadow trigger record with every key parse_shadow_trigger needs, that the head parses to a trigger with the path off when ws is 0, and that the
    gate passes (C1 to C7) when ws is 1."""
    i = g.nxt()
    S = 50_000 + i
    bt_ = {"status": "ok", "dropped_n": 1, "dropped_first_slot": S - 20, "dropped_last_slot": S - 20, "cover_lo": S - 50, "cover_hi": S - 4,
           "kept_ws_only_before_cover": 0, "kept_ws_only_above_cover": 0, "kept_ws_only_sig_on_tip": 0, "dropped_boost_n": 0, "dropped_sells_n": 0,
           "orphaned_by_drop_n": 0, "tipauth_nosettle": 0, "dropped": [[f"SIGx{i:05d}", 0, S - 20]], "margin_slots": 4}
    bu_ = {"status": "ok", "ws_only": 1, "tip_only": 0, "both": 5, "both_diff": 0, "tip_first_slot": S - 50, "tip_hw_slot_global": S + 3, "tip_lag_slots_global": 1}
    r = {"type": "trigger", "variant": "pv", "t_ms": MID + i, "mint": f"MINTx{i:05d}", "pool": f"POOLx{i:05d}", "s0": S - 1000, "s0_t_recv_ms": MID - 100_000,
         "slot": S, "sps": 0.4, "block_time": MID // 1000, "t_recv_ms": MID - 50, "t_detect_ms": MID, "q_trigger_sol": 25.0, "v_print": 17_580_000_000,
         "v_missing": False, "base_pre": 800_000_000_000_000, "sell_token_raw": 5_000_000_000, "boost_spent_sol": 1.0, "boost_src": "pda",
         "base_breaks_unresolved": 0, "base_breaks_unresolved_settled": ws, "base_breaks_unresolved_settled_union": ws,
         "base_breaks_unresolved_settled_tipauth": 0, "s0_minus_announced_slots": 0, "s0_reanchored_slots": 0, "sps_span_s": 200.0, "gap": False,
         "synthetic": False, "synthetic_src": "rpc", "base_union": bu_, "base_tipauth": bt_}
    r.update(over)
    if mut:
        mut(r)
    return r


MUTATIONS = [
    ("gap", lambda r: r.update(gap=True), "tipauth:gap"),
    ("union_status", lambda r: r["base_union"].update(status="tip_pending"), "tipauth:union_status"),
    ("tipauth_status", lambda r: r["base_tipauth"].update(status="tip_pending"), "tipauth:tipauth_status"),
    ("value_not_zero", lambda r: r.update(base_breaks_unresolved_settled_tipauth=2), "tipauth:value_not_zero"),
    ("tip_only", lambda r: r["base_union"].update(tip_only=1), "tipauth:tip_only"),
    ("both_diff", lambda r: r["base_union"].update(both_diff=1), "tipauth:both_diff"),
    ("dropped_boost", lambda r: r["base_tipauth"].update(dropped_boost_n=1), "tipauth:dropped_boost"),
    ("boost_src", lambda r: r.update(boost_src="none"), "tipauth:boost_src"),
    ("dropped_n", lambda r: r["base_tipauth"].update(dropped_n=6), "tipauth:dropped_n"),
    ("orphaned", lambda r: r["base_tipauth"].update(orphaned_by_drop_n=1), "tipauth:orphaned_by_drop"),
    ("nosettle", lambda r: r["base_tipauth"].update(tipauth_nosettle=1), "tipauth:nosettle"),
    ("margin", lambda r: r["base_tipauth"].update(margin_slots=5), "tipauth:margin"),
    ("union_ne_ws", lambda r: r.update(base_breaks_unresolved_settled_union=2), "tipauth:union_ne_ws"),
]


def real_corpus(g: Gen) -> tuple[list, dict]:
    """Records for the real parsers and what each class must come to. Returns (items, expected)."""
    items: list = [start(t=T0 + 1000, sha=SHA)]
    exp: dict = {}
    items += [real_row(g, 0) for _ in range(5)]  # websocket passes
    items += [real_row(g, 0, gap=True), real_row(g, 0, boost_src="behavioural")]  # still websocket passes: nothing in C1-C7 touches ws 0
    items += [real_row(g, 0, v_missing=True), real_row(g, 0, synthetic=True), real_row(g, 0, suppressed=True)]  # refused before the gate, both sides
    broken = real_row(g, 0)
    del broken["sps"]
    items += [broken]
    items += [real_row(g, 0, t_detect_ms=float("nan"))]  # the parser raises on both sides (ValueError): no difference
    items += [real_row(g, 1) for _ in range(6)]  # admitted by the gate
    items += [real_row(g, 1, sps_span_s=10.0), real_row(g, 1, boost_spent_sol=17.585)]  # gate passes, then a later check of the parser refuses
    items += [real_row(g, 1, v_missing=True)]  # refused before the gate in both
    for _name, mut, _why in MUTATIONS:
        items += [real_row(g, 1, mut=mut) for _ in range(2)]
    items += [real_row(g, 0, variant="fv"), real_row(g, 2, variant="fv")]
    items += [real_row(g, 1, sealed=True), real_row(g, 1, h5_look2_sealed=True), real_row(g, 1, sealed=True, h5_look2_sealed=True)]  # would pass, are excluded
    items += [{"type": "gap", "t_ms": MID, "kind": "reconnect"}, hb(MID), "{not json", "[1]"]
    exp.update(ws0_passes=7, admitted=6, later=2, mutations=2 * len(MUTATIONS))
    return items, exp


class RealExecutorTests(Base):
    """The real head file of this checkout against the installed build b7ddeb60 (git object) and against a copy of that file (--base-file)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.skip_reason = None
        try:
            D.load_file(HEAD_REAL, "_probe_head")
        except D.InputError as exc:
            cls.skip_reason = f"the head executor cannot be imported here ({exc.msg[:160]})"
            return
        p = subprocess.run(["git", "-C", str(D.ROOT), "cat-file", "-e", f"{INSTALLED_SHA}:tools/h5_executor.py"], capture_output=True)
        if p.returncode != 0:
            cls.skip_reason = f"git object {INSTALLED_SHA} is not in this clone"
        else:
            cls.installed_bytes = subprocess.run(["git", "-C", str(D.ROOT), "show", f"{INSTALLED_SHA}:tools/h5_executor.py"], capture_output=True, check=True).stdout

    def setUp(self) -> None:
        super().setUp()
        if self.skip_reason:
            print(f"SKIP {self.id()}: {self.skip_reason}")
            self.skipTest(self.skip_reason)
        self.g = Gen()
        self.items, self.exp = real_corpus(self.g)
        write_hour(self.shadow, self.items)

    def run_real(self, base_args, extra=()):
        argv = ["--shadow-dir", str(self.shadow), "--since-ms", str(T0), "--until-ms", str(UNTIL), "--expect-code-sha", SHA, *base_args, *extra]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = D.main(argv)
        return code, json.loads(buf.getvalue()), buf.getvalue()

    def test_installed_build_against_head_by_git_sha(self):
        code, r, text = self.run_real(["--base-git-sha", INSTALLED_SHA])
        self.assertEqual(code, 0)
        self.assertEqual(r["verdict"], {k: True for k in D.VERDICT_KEYS}, json.dumps(r["a_vs_c"]["other_changes"]))
        self.assertEqual(r["base"]["sha256"], hashlib.sha256(self.installed_bytes).hexdigest())
        self.assertEqual(r["head"]["sha256"], hashlib.sha256(HEAD_REAL.read_bytes()).hexdigest())
        self.assertFalse(r["base"]["parse_has_tipauth_parameter"])
        self.assertIn("gate_path", r["head"]["trigger_fields_only_in_head"])
        self.assertTrue(r["availability"]["source"].startswith("H5Executor._tipauth_availability on a stub"), r["availability"]["source"])
        for k in ("head_path_off_default_call", "head_path_off_tipauth_none"):
            self.assertEqual(r["a_vs_b"][k]["records_differ"], 0)
        for ident in self.g.ids:
            self.assertNotIn(ident, text)

    def test_the_transition_table_matches_the_construction(self):
        _, r, _ = self.run_real(["--base-git-sha", INSTALLED_SHA])
        c = r["a_vs_c"]
        e = self.exp
        self.assertEqual(c["refusals_to_passes"], {"total": e["admitted"], "by_a_reason": {WS: e["admitted"]}, "gate_path_tipauth": e["admitted"]})
        self.assertEqual(c["ws_refusals_still_refused"]["by_later_check_reason"], {"bad_intent:boost_spent": 1, "bad_intent:sps_span": 1})
        self.assertEqual(c["ws_refusals_still_refused"]["by_tipauth_reason_decision_unchanged"], {why: 2 for _n, _m, why in MUTATIONS})
        self.assertEqual(c["passes_to_refusals"]["total"], 0)
        self.assertEqual(c["other_changes"]["total"], 0)
        self.assertEqual(c["a_passes"], e["ws0_passes"])
        tr = {(t["a"], t["c"]): t["n"] for t in c["transitions"]}
        self.assertEqual(tr[("raise:ValueError", "raise:ValueError")], 1)  # the NaN t_detect_ms row: the same exception on both sides
        self.assertEqual(tr[("bad_intent:v_missing", "bad_intent:v_missing")], 2)  # refused before the gate, ws 0 and ws 1: no change
        self.assertEqual(tr[("synthetic_unconfirmed", "synthetic_unconfirmed")], 1)
        self.assertEqual(tr[("bad_intent:missing_sps", "bad_intent:missing_sps")], 1)
        self.assertEqual(tr[("IGNORED", "IGNORED")], 2)
        self.assertEqual(r["records"]["sealed_excluded"]["total"], 3)
        w = r["would_pass"]
        self.assertEqual(w["refusals_to_passes_item4_true"], e["admitted"])
        self.assertEqual(w["disagreements"]["total"], 0)
        self.assertEqual(w["item4_predicate_true_by_c_outcome"].get("PASS"), e["admitted"])

    def test_the_selection_equals_the_item4_tools(self):
        _, r, _ = self.run_real(["--base-git-sha", INSTALLED_SHA])
        r4 = I4.analyze(self.shadow, T0, UNTIL, expect_code_sha=SHA)
        self.assertEqual(r["records"]["in_item4_selection"], r4["records"]["triggers"])
        self.assertEqual(r["would_pass"]["item4_predicate_true_in_item4_selection"], r4["item6_would_pass"]["would_pass_n"])
        # C1-C7 hold on the admitted rows, on the two the parser refuses later, and on the ws 1 row refused before the gate (v_missing)
        self.assertEqual(r4["item6_would_pass"]["would_pass_n"], self.exp["admitted"] + self.exp["later"] + 1)

    def test_installed_file_by_path_gives_the_same_counts_as_by_git_sha(self):
        f = self.tmp / "installed_executor.py"
        f.write_bytes(self.installed_bytes)
        _, r1, _ = self.run_real(["--base-git-sha", INSTALLED_SHA])
        _, r2, _ = self.run_real(["--base-file", str(f)])
        for r in (r1, r2):
            r["base"].pop("source")
        self.assertEqual(r1["a_vs_c"], r2["a_vs_c"])
        self.assertEqual(r1["a_vs_b"], r2["a_vs_b"])
        self.assertEqual(r1["base"], r2["base"])

    def test_head_against_head_has_no_path_off_difference(self):
        _, r, _ = self.run_real(["--base-file", str(HEAD_REAL)])
        self.assertTrue(r["base"]["parse_has_tipauth_parameter"])
        self.assertEqual(r["base"]["sha256"], r["head"]["sha256"])
        self.assertEqual(r["verdict"], {k: True for k in D.VERDICT_KEYS})  # A is the head's own default call here: A against B is the head against itself
        self.assertEqual(r["a_vs_c"]["refusals_to_passes"]["total"], self.exp["admitted"])

    def test_a_gate_passing_record_whose_later_field_makes_the_parser_raise_is_an_other_change(self):
        g = Gen()
        write_hour(self.shadow, [real_row(g, 1, t_detect_ms=float("nan"))], hour_ms=T0)
        _, r, _ = self.run_real(["--base-git-sha", INSTALLED_SHA])
        c = r["a_vs_c"]
        self.assertEqual(c["ws_refusals_that_become_exceptions"], 1)
        self.assertFalse(r["verdict"]["other_changes_zero"])
        self.assertTrue(r["verdict"]["a_vs_b_default_zero_differences"])


if __name__ == "__main__":
    unittest.main()
