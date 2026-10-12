"""Tests for tools/h5_tipauth_census.py. Fake transport only: no network."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import h5_tipauth_census as C


def make_hook(seen: set):
    """object_hook producing dicts that record every key looked up."""
    class R(dict):
        def __getitem__(self, k):
            seen.add(k)
            return dict.__getitem__(self, k)

        def get(self, k, d=None):
            seen.add(k)
            return dict.get(self, k, d)

        def __contains__(self, k):
            seen.add(k)
            return dict.__contains__(self, k)

        def items(self):
            seen.add("<items>")
            return dict.items(self)

        def keys(self):
            seen.add("<keys>")
            return dict.keys(self)

        def values(self):
            seen.add("<values>")
            return dict.values(self)

        def __iter__(self):
            seen.add("<iter>")
            return dict.__iter__(self)

    return lambda pairs: R(pairs)


def ent(sig, slot=100):
    return [sig, 0, slot]


def item4(wp=(), closes=(), origin=1000, until=10**9, since=0, raw=None, variants=None):
    """wp rows: (pool, sigs, dropped_n); `raw` maps a pool to a raw `dropped` list; `variants` maps a pool to its variant."""
    raw, variants = raw or {}, variants or {}
    rows = [{"pool": p, "dropped": raw[p] if p in raw else [ent(s) for s in ss], "dropped_n": n if n is not None else len(ss),
             "variant": variants.get(p, "pv"), "outcome": 9} for p, ss, n in wp]
    cl = [{"pool": p, "dropped": [ent(s) for s in ss], "dropped_n": len(ss), "t_ms": 5, "pnl": 1, "partial": False} for p, ss in closes]
    return {"tool": "h5_tipauth_item4", "window": {"origin_t_ms": origin, "elapsed_h": 3}, "until_ms": until, "since_ms": since,
            "item7_census_input": {"would_pass_triggers": rows, "tipauth0_ws_ge1_nogap_closes": cl, "other": 1}, "verdict": {"x": 1}}


def write_shadow(d: Path, recs):
    (d / "h5-shadow-2026-10-13T10.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")


def pool_rec(pool, rows, t=2000, **kw):
    return {"type": "pool", "t_ms": t, "pool": pool, "base_unmatched_union": rows, "outcome_bx10": 5, **kw}


class Fake:
    def __init__(self, found=(), err=(), seq=None):
        self.found, self.err, self.calls, self.seq = set(found), set(err), [], seq or {}

    def __call__(self, url, payload, timeout):
        assert url == C.RPC_URL
        sig = payload["params"][0]
        assert payload["method"] == "getTransaction"
        o = payload["params"][1]
        assert o["commitment"] == "finalized" and o["maxSupportedTransactionVersion"] == 0
        self.calls.append(sig)
        if sig in self.seq and self.seq[sig]:
            r = self.seq[sig].pop(0)
            if r == "raise":
                raise TimeoutError("t")
            return r
        if sig in self.err:
            raise TimeoutError("t")
        return {"result": {"slot": 7} if sig in self.found else None}


class Clock:
    def __init__(self):
        self.t = 10**12

    def __call__(self):
        return self.t


def sigs(n, pre="s"):
    return [f"{pre}{i:03d}" for i in range(n)]


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.out = self.tmp / "out"
        self.out.mkdir()
        self.shadow = self.tmp / "shadow"
        self.shadow.mkdir()

    def plan(self, i4, shadow=()):
        write_shadow(self.shadow, list(shadow))
        raw = json.dumps(i4).encode()
        p = C.build_plan(json.loads(raw), self.shadow, C.sha256_bytes(raw))
        C.write_new(self.out / C.PLAN_NAME, C.dumps(p))
        return p


class TestSelection(Base):
    def test_sets_ordering_and_determinism(self):
        closes = [("P1", sigs(80, "b")), ("P2", ["a1"] + sigs(5, "c"))]
        ctrl = [[s, 0, 1, "both"] for s in sigs(40, "k")] + [["ws1", 0, 1, "ws"], ["a2", 0, 1, "tip"], ["b001", 0, 1, "tip"]]
        i4 = item4(wp=[("P0", ["a1", "a2"], None)], closes=closes)
        p1 = self.plan(i4, [pool_rec("P0", ctrl)])
        S = p1["sets"]
        self.assertEqual(S["A"], ["a1", "a2"])
        self.assertEqual(len(S["B"]), 50)
        self.assertFalse(set(S["B"]) & set(S["A"]))
        self.assertEqual(S["B"], sorted(S["B"], key=C.sig_key))
        self.assertEqual(len(S["C"]), 30)
        self.assertEqual(S["C"], sorted(S["C"], key=C.sig_key))
        self.assertFalse({"ws1", "a2", "b001"} & set(S["C"]))
        self.assertEqual(p1["call_cap"], 2 + 50 + 30)
        raw = json.dumps(i4).encode()
        p2 = C.build_plan(json.loads(raw), self.shadow, C.sha256_bytes(raw))
        self.assertEqual(p1["selection_sha256"], p2["selection_sha256"])
        self.assertEqual(C.dumps(p1), C.dumps(p2))

    def test_controls_filters(self):
        rows = [["c1", 0, 1, "both"], ["c2", 0, 1, "tip"], ["c3", 0, 1, "ws"]]
        sh = [pool_rec("P0", rows), pool_rec("P0", [["sealed", 0, 1, "both"]], sealed=True),
              pool_rec("P0", [["h5s", 0, 1, "both"]], h5_look2_sealed=1), pool_rec("OTHER", [["op", 0, 1, "both"]]),
              pool_rec("P0", [["early", 0, 1, "both"]], t=5), pool_rec("P0", [["late", 0, 1, "both"]], t=10**10),
              {"type": "trigger", "t_ms": 2000, "pool": "P0", "base_unmatched_union": [["trg", 0, 1, "both"]]}]
        p = self.plan(item4(wp=[("P0", ["a1"], None)]), sh)
        self.assertEqual(sorted(p["sets"]["C"]), ["c1", "c2"])

    def test_plan_refuses_overwrite_cli(self):
        i4 = self.tmp / "i4.json"
        i4.write_text(json.dumps(item4(wp=[("P0", ["a1"], None)])))
        write_shadow(self.shadow, [])
        args = ["plan", "--item4-json", str(i4), "--shadow-dir", str(self.shadow), "--out-dir", str(self.out)]
        self.assertEqual(C.main(args), 0)
        before = (self.out / C.PLAN_NAME).read_bytes()
        with self.assertRaises(SystemExit):
            C.main(args)
        self.assertEqual(before, (self.out / C.PLAN_NAME).read_bytes())

    def test_refuses_wrong_tool(self):
        with self.assertRaises(SystemExit):
            C.select_AB({"tool": "x"})

    def test_allow_list(self):
        seen4, seenS = set(), set()
        i4 = item4(wp=[("P0", ["a1"], None)], closes=[("P1", ["b1"])])
        write_shadow(self.shadow, [pool_rec("P0", [["c1", 0, 1, "both"]], sealed=False)])
        d = json.loads(json.dumps(i4), object_hook=make_hook(seen4))
        C.build_plan(d, self.shadow, "x", make_hook(seenS))
        self.assertTrue(seen4 <= C.ALLOWED_ITEM4_KEYS, seen4 - C.ALLOWED_ITEM4_KEYS)
        self.assertTrue(seenS <= C.ALLOWED_SHADOW_KEYS, seenS - C.ALLOWED_SHADOW_KEYS)
        for bad in ("outcome", "pnl", "outcome_bx10", "verdict", "other"):
            self.assertNotIn(bad, seen4 | seenS)
        self.assertTrue({"pool", "dropped", "variant", "since_ms", "partial"} <= seen4 and "base_unmatched_union" in seenS)
        self.assertFalse({"<items>", "<keys>", "<values>", "<iter>"} & (seen4 | seenS))  # no iteration over a record


class TestAsk(Base):
    def small(self):
        return self.plan(item4(wp=[("P0", ["a1", "a2"], None)], closes=[("P1", ["b1", "b2"])]),
                         [pool_rec("P0", [["c1", 0, 1, "both"]])])

    def test_ask_cap_once_and_log(self):
        p = self.small()
        f, ck, sl = Fake(found=["c1"]), Clock(), []
        n = C.cmd_ask(self.out, f, sl.append, ck)
        self.assertEqual(n, p["call_cap"])
        self.assertEqual(sorted(f.calls), ["a1", "a2", "b1", "b2", "c1"])
        self.assertEqual(len(sl), n - 1)
        self.assertEqual(len(C.read_jsonl(self.out / C.LOG_NAME)), n)
        with self.assertRaises(SystemExit):
            C.cmd_ask(self.out, f, sl.append, ck)
        self.assertEqual(len(f.calls), n)

    def test_cap_enforced(self):
        with self.assertRaises(SystemExit):
            C.run_queries(self.out, ["x", "y"], 1, Fake(), lambda s: None, Clock(), "ask")

    def test_error_never_not_found(self):
        self.small()
        f = Fake(err=["a1"], seq={"a2": [{"error": {"code": 429}}], "b1": [{"result": 5}], "b2": [{}]})
        C.cmd_ask(self.out, f, lambda s: None, Clock())
        by = C.states_by_sig(C.read_jsonl(self.out / C.STATE_NAME))
        for s in ("a1", "a2", "b1", "b2"):
            self.assertEqual(C.classify(by[s]), "error", s)
        self.assertEqual(C.classify(by["c1"]), "pending")
        rep, _ = C.cmd_report(self.out)
        self.assertEqual(rep["counts"]["A"]["not_found"], 0)
        self.assertIsNone(rep["thresholds"]["no_AB_signature_found"]["met"])

    def test_reask_ten_minute_refusal_and_not_found(self):
        self.small()
        ck, f = Clock(), Fake(err=["a1"])
        C.cmd_ask(self.out, f, lambda s: None, ck)
        ck.t += 599_999
        with self.assertRaises(SystemExit):
            C.cmd_reask(self.out, f, lambda s: None, ck)
        ck.t += 1
        f.err = set()
        n = C.cmd_reask(self.out, f, lambda s: None, ck)
        self.assertEqual(n, 5)
        by = C.states_by_sig(C.read_jsonl(self.out / C.STATE_NAME))
        self.assertEqual(C.classify(by["a1"]), "pending")
        self.assertEqual(C.classify(by["a2"]), "not_found")
        with self.assertRaises(SystemExit):
            C.cmd_reask(self.out, f, lambda s: None, ck)
        ck.t += 600_000
        self.assertEqual(C.cmd_reask(self.out, f, lambda s: None, ck), 1)

    def test_found_not_reasked(self):
        self.small()
        ck, f = Clock(), Fake(found=["a1"])
        C.cmd_ask(self.out, f, lambda s: None, ck)
        ck.t += 600_000
        f.calls.clear()
        C.cmd_reask(self.out, f, lambda s: None, ck)
        self.assertNotIn("a1", f.calls)

    def test_default_transport_is_only_a_default(self):
        self.assertEqual(C.RPC_URL, "https://api.mainnet-beta.solana.com")
        self.assertIs(C.main.__defaults__[1], C.http_transport)


def plan_of(A=(), B=(), Cc=(), trig=(), interim=False, slots=None):
    sets = {"A": list(A), "B": list(B), "C": list(Cc)}
    return {"tool": C.TOOL, "selection_sha256": "h", "item4_sha256": "i", "sizes": {k: len(v) for k, v in sets.items()}, "sets": sets,
            "triggers": [{"sigs": list(s), "fully_enumerated": fe, "variant": (t[2] if len(t) > 2 else "pv")} for t in trig for s, fe in [t[:2]]],
            "malformed_entries_n": 0, "interim": interim, "slots": slots or {}, "window": {"origin_t_ms": 0, "until_ms": 1, "since_ms": 0},
            "b_candidates_n": len(B), "b_closes_n": 0, "b_partial_closes_n": 0, "total_call_cap": 4 * sum(len(v) for v in sets.values())}


def answers(nf=(), found=(), err=(), pend=()):
    rows = []
    for s in nf:
        rows += [{"sig": s, "asked_ms": 0, "result": "null"}, {"sig": s, "asked_ms": C.REASK_MIN_MS, "result": "null"}]
    rows += [{"sig": s, "asked_ms": 0, "result": "found", "slot": 1} for s in found]
    rows += [{"sig": s, "asked_ms": 0, "result": "error"} for s in err]
    rows += [{"sig": s, "asked_ms": 0, "result": "null"} for s in pend]
    return rows


class TestReport(unittest.TestCase):
    def met(self, rep, k):
        return rep["thresholds"][k]["met"]

    def test_empty_classes_never_pass(self):
        rep = C.report(plan_of(), [])
        for k in rep["thresholds"]:
            if k != "no_malformed_dropped_entry":
                self.assertIsNone(self.met(rep, k), k)
        self.assertIsNone(rep["census_rules_clear"])

    def test_found_A_and_B_false(self):
        for k in ("A", "B"):
            rep = C.report(plan_of(**{k: ["x1"]}), answers(found=["x1"]))
            self.assertIs(self.met(rep, "no_AB_signature_found"), False)
            self.assertIs(rep["census_rules_clear"], False)

    def test_no_find_requires_all_definitive(self):
        p = plan_of(A=["a1"], B=["b1"])
        self.assertIs(self.met(C.report(p, answers(nf=["a1", "b1"])), "no_AB_signature_found"), True)
        self.assertIsNone(self.met(C.report(p, answers(nf=["a1"], err=["b1"])), "no_AB_signature_found"))
        self.assertIsNone(self.met(C.report(p, answers(nf=["a1"], pend=["b1"])), "no_AB_signature_found"))

    def test_triggers_80pct(self):
        A = sigs(5, "a")
        k = "answered_triggers_ge_80pct"
        trig = [([s], True) for s in A]
        self.assertIs(self.met(C.report(plan_of(A=A, trig=trig), answers(nf=A[:4], err=A[4:])), k), True)
        self.assertIs(self.met(C.report(plan_of(A=A, trig=trig), answers(nf=A[:3], err=A[3:])), k), False)
        t2 = [([s], i != 0) for i, s in enumerate(A)]
        self.assertIs(self.met(C.report(plan_of(A=A, trig=t2), answers(nf=A)), k), True)
        t3 = [([s], i > 1) for i, s in enumerate(A)]
        self.assertIs(self.met(C.report(plan_of(A=A, trig=t3), answers(nf=A)), k), False)
        self.assertIsNone(self.met(C.report(plan_of(A=A), answers(nf=A)), k))

    def test_fully_enumerated_rule(self):
        self.assertTrue(C.fully_enumerated({"dropped": [ent("a")], "dropped_n": 1}))
        self.assertFalse(C.fully_enumerated({"dropped": [ent("a")], "dropped_n": 2}))
        self.assertFalse(C.fully_enumerated({"dropped": [], "dropped_n": 0}))
        self.assertFalse(C.fully_enumerated({"dropped": None, "dropped_n": 1}))

    def test_B_definitive_50(self):
        B = sigs(50, "b")
        k = "B_definitive_ge_50"
        self.assertIs(self.met(C.report(plan_of(B=B), answers(nf=B)), k), True)
        r = C.report(plan_of(B=B), answers(nf=B[:49], err=B[49:]))
        self.assertIsNone(self.met(r, k))
        self.assertEqual(r["thresholds"][k]["label"], "short")
        self.assertIsNone(self.met(C.report(plan_of(B=B[:30]), answers(nf=B[:30])), k))

    def test_controls(self):
        Cc = sigs(40, "c")
        k = "controls_found_ge_95pct_of_answered"
        self.assertIs(self.met(C.report(plan_of(Cc=Cc), answers(found=Cc)), k), True)
        self.assertIs(self.met(C.report(plan_of(Cc=Cc), answers(found=Cc[:37], nf=Cc[37:])), k), False)
        self.assertIs(self.met(C.report(plan_of(Cc=Cc), answers(found=Cc[:38], nf=Cc[38:])), k), True)
        self.assertIsNone(self.met(C.report(plan_of(Cc=Cc), answers(found=Cc[:19], err=Cc[19:])), k))
        self.assertIs(self.met(C.report(plan_of(Cc=Cc), answers(found=Cc[:20], err=Cc[20:])), k), True)
        self.assertIsNone(self.met(C.report(plan_of(), []), k))

    def test_all_true_is_clear_and_name(self):
        A, B, Cc = ["a1"], sigs(50, "b"), sigs(30, "c")
        rep = C.report(plan_of(A=A, B=B, Cc=Cc, trig=[(A, True)]), answers(nf=A + B, found=Cc))
        self.assertIs(rep["census_rules_clear"], True)
        txt = C.dumps(rep) + C.table(rep)
        self.assertNotIn("proposal_allowed", txt)
        self.assertIn("(NOT a proposal verdict)", txt)
        self.assertEqual(C.dumps(rep), C.dumps(json.loads(C.dumps(rep))))

    def test_report_cli_writes_hashes_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            i4 = d / "i4.json"
            i4.write_text(json.dumps(item4(wp=[("P0", ["a1"], None)])))
            (d / "sh").mkdir()
            out = d / "out"
            self.assertEqual(C.main(["plan", "--item4-json", str(i4), "--shadow-dir", str(d / "sh"), "--out-dir", str(out)]), 0)
            self.assertEqual(C.main(["report", "--out-dir", str(out)]), 0)  # no state file: report with absent state, no traceback
            rep = json.loads((out / C.REPORT_NAME).read_text())
            self.assertEqual(rep["plan_sha256"], C.sha256_bytes((out / C.PLAN_NAME).read_bytes()))
            self.assertIsNone(rep["state_sha256"])
            with self.assertRaises(SystemExit):
                C.main(["report", "--out-dir", str(out)])


def mk_plan(base, i4, shadow=()):
    write_shadow(base.shadow, list(shadow))
    raw = json.dumps(i4).encode()
    p = C.build_plan(json.loads(raw), base.shadow, C.sha256_bytes(raw))
    C.write_new(base.out / C.PLAN_NAME, C.dumps(p))
    return p


class TestChange1Malformed(Base):
    def test_junk_entries_not_fully_enumerated(self):
        for i, bad in enumerate([[["s1", 0, 5], "junk"], [[123, 0, 5], ["s1", 0, 5]], [["s1", 0, 5], ["s2", 0]], [["s1", 0, 5], ["s2", "x", 5]]]):
            self.assertFalse(C.fully_enumerated({"dropped": bad, "dropped_n": len(bad)}), bad)
            i4 = item4(wp=[("P0", ["s1"], 2)], raw={"P0": bad})
            p = C.build_plan(i4, self.shadow, "x") if self.shadow.exists() else None
            self.assertFalse(p["triggers"][0]["fully_enumerated"])
            self.assertGreaterEqual(p["malformed_entries_n"], 1)
        self.assertFalse(C.fully_enumerated({"dropped": [], "dropped_n": 0}))

    def test_end_to_end_junk_trigger_does_not_count(self):
        p = C.build_plan(item4(wp=[("P0", ["s1"], 2)], raw={"P0": [["s1", 0, 5], "junk"]}), self.shadow, "x")
        rows = answers(nf=["s1"])
        rep = C.report(p, rows)
        self.assertIs(rep["thresholds"]["answered_triggers_ge_80pct"]["met"], False)
        self.assertIs(rep["thresholds"]["no_malformed_dropped_entry"]["met"], False)
        self.assertIs(rep["census_rules_clear"], False)

    def test_duplicate_signature_stays_fully_enumerated(self):
        d = [["s1", 0, 5], ["s1", 1, 5]]
        self.assertTrue(C.fully_enumerated({"dropped": d, "dropped_n": 2}))
        p = C.build_plan(item4(wp=[("P0", ["s1"], 2)], raw={"P0": d}), self.shadow, "x")
        self.assertEqual(p["malformed_entries_n"], 0)
        self.assertEqual(p["sets"]["A"], ["s1"])


class TestChange2Partial(Base):
    def test_partial_counts_copied_to_report(self):
        i4 = item4(wp=[("P0", ["a1"], None)], closes=[("P1", ["b1"]), ("P2", ["b2"]), ("P3", ["b3"])])
        cl = i4["item7_census_input"]["tipauth0_ws_ge1_nogap_closes"]
        cl[0]["partial"] = True
        del cl[1]["partial"]                 # missing partial: partial (fail closed)
        cl[2]["dropped_n"] = 9               # n != len(dropped): partial
        p = C.build_plan(i4, self.shadow, "x")
        self.assertEqual((p["b_closes_n"], p["b_partial_closes_n"], p["b_candidates_n"]), (3, 3, 3))
        rep = C.report(p, [])
        self.assertEqual((rep["b_closes_n"], rep["b_partial_closes_n"], rep["b_candidates_n"]), (3, 3, 3))
        cl[0]["partial"] = False
        cl[1]["partial"] = False
        cl[2]["dropped_n"] = 1
        self.assertEqual(C.build_plan(i4, self.shadow, "x")["b_partial_closes_n"], 0)


class TestChange3Reask(Base):
    def test_four_rounds_at_most_and_total_cap(self):
        p = mk_plan(self, item4(wp=[("P0", ["a1"], None)]))
        self.assertEqual(p["total_call_cap"], 4 * p["call_cap"])
        ck, f = Clock(), Fake(err=["a1"])
        C.cmd_ask(self.out, f, lambda s: None, ck)
        for r in range(1, 4):
            ck.t += 600_000
            self.assertEqual(C.cmd_reask(self.out, f, lambda s: None, ck), 1)
            self.assertEqual(C.read_jsonl(self.out / C.STATE_NAME)[-1]["round"], f"reask{r}")
        ck.t += 600_000
        with self.assertRaises(SystemExit):
            C.cmd_reask(self.out, f, lambda s: None, ck)
        self.assertEqual(len(f.calls), 4)
        self.assertLessEqual(len(f.calls), p["total_call_cap"])

    def test_total_cap_in_run_queries(self):
        mk_plan(self, item4(wp=[("P0", ["a1"], None)]))
        ck, f = Clock(), Fake()
        for _ in range(4):
            C.run_queries(self.out, ["a1"], 1, f, lambda s: None, ck, "x")
        with self.assertRaises(SystemExit):
            C.run_queries(self.out, ["a1"], 1, f, lambda s: None, ck, "x")


class TestChange4Empty(Base):
    def test_empty_plan_refused_not_traceback(self):
        mk_plan(self, item4())
        with self.assertRaises(SystemExit) as cm:
            C.cmd_ask(self.out, Fake(), lambda s: None, Clock())
        self.assertIn("empty plan", str(cm.exception))
        f = Fake()
        with self.assertRaises(SystemExit):
            C.main(["ask", "--out-dir", str(self.out)], f, lambda s: None)
        self.assertEqual(f.calls, [])

    def test_absent_files_print_absent(self):
        self.assertEqual(C.file_sha(self.tmp / "nope"), "absent")
        C.write_new(self.tmp / "x", "a")
        self.assertEqual(C.file_sha(self.tmp / "x"), C.sha256_bytes(b"a"))


class TestChange5Interim(Base):
    def test_interim_never_clear(self):
        A, B, Cc = ["a1"], sigs(50, "b"), sigs(30, "c")
        trig = [(A, True)]
        clean = answers(nf=A + B, found=Cc)
        self.assertIs(C.report(plan_of(A=A, B=B, Cc=Cc, trig=trig, interim=False), clean)["census_rules_clear"], True)
        rep = C.report(plan_of(A=A, B=B, Cc=Cc, trig=trig, interim=True), clean)
        self.assertIsNone(rep["census_rules_clear"])
        self.assertTrue(C.table(rep).splitlines()[0].startswith("INTERIM, not the deciding read"))
        self.assertIs(C.report(plan_of(A=A, B=B, Cc=Cc, trig=trig, interim=True), answers(found=["a1"], nf=B + Cc))["census_rules_clear"], False)

    def test_interim_computed_from_since_until(self):
        H = 3_600_000
        for h, want in ((6, True), (23, True), (24, False), (36, False)):
            p = C.build_plan(item4(wp=[("P0", ["a1"], None)], since=7, until=7 + h * H), self.shadow, "x")
            self.assertIs(p["interim"], want, h)
        i4 = item4(wp=[("P0", ["a1"], None)])
        del i4["since_ms"]
        with self.assertRaises(SystemExit):
            C.build_plan(i4, self.shadow, "x")


class TestChange6Pv(unittest.TestCase):
    def test_pv_share(self):
        A = sigs(10, "a")
        trig = [([A[i]], True, "pv") for i in range(4)] + [([A[4 + i]], True, "fv") for i in range(4)] + [([A[8]], False, "pv"), ([A[9]], False, "pv")]
        rep = C.report(plan_of(A=A, trig=trig), answers(nf=A))
        t = rep["thresholds"]["answered_triggers_ge_80pct"]
        self.assertEqual((t["answered"], t["n"], t["pv_answered"], t["pv_n"]), (8, 10, 4, 6))
        self.assertIs(t["met"], False)
        ok = [([a], True, "pv") for a in A]
        self.assertIs(C.report(plan_of(A=A, trig=ok), answers(nf=A))["thresholds"]["answered_triggers_ge_80pct"]["met"], True)

    def test_no_pv_rows_is_null(self):
        A = sigs(3, "a")
        t = C.report(plan_of(A=A, trig=[([a], True, "fv") for a in A]), answers(nf=A))["thresholds"]["answered_triggers_ge_80pct"]
        self.assertIsNone(t["met"])
        t = C.report(plan_of(A=A, trig=[([a], True, None) for a in A]), answers(nf=A))["thresholds"]["answered_triggers_ge_80pct"]
        self.assertIsNone(t["met"])


class TestChange7Provenance(Base):
    def test_age_and_provenance_keys(self):
        i4 = item4(wp=[("P0", ["a1"], None)], closes=[("P1", ["b1"])])
        i4["item7_census_input"]["would_pass_triggers"][0]["dropped"] = [["a1", 0, 50], ["a1", 1, 40]]
        i4["item7_census_input"]["would_pass_triggers"][0]["dropped_n"] = 2
        i4["item7_census_input"]["tipauth0_ws_ge1_nogap_closes"][0]["dropped"] = [["b1", 0, 90]]
        p = mk_plan(self, i4, [pool_rec("P0", [["c1", 0, 1, "both"], ["c2", 0, 1, "both"]])])
        self.assertEqual(p["slots"], {"a1": 40, "b1": 90})
        ck, f = Clock(), Fake(found=["c1"])
        C.cmd_ask(self.out, f, lambda s: None, ck)
        ck.t += 600_000
        C.cmd_reask(self.out, f, lambda s: None, ck)
        rep, _ = C.cmd_report(self.out)
        self.assertEqual((rep["ab_slot_min"], rep["ab_slot_max"]), (40, 90))
        self.assertEqual((rep["control_found_slot_min"], rep["control_found_slot_max"]), (7, 7))
        self.assertEqual(rep["ab_older_than_oldest_found_control_n"], 0)
        self.assertEqual(rep["reask_rounds_n"], 1)
        self.assertEqual(rep["state_rows_n"], len(C.read_jsonl(self.out / C.STATE_NAME)))
        self.assertEqual(rep["state_sha256"], C.sha256_bytes((self.out / C.STATE_NAME).read_bytes()))
        self.assertEqual(rep["plan_sha256"], C.sha256_bytes((self.out / C.PLAN_NAME).read_bytes()))
        self.assertLessEqual(rep["first_asked_ms"], rep["last_asked_ms"])
        self.assertEqual(rep["last_asked_ms"] - rep["first_asked_ms"], 600_000)

    def test_older_than_control(self):
        p = plan_of(A=["a1"], B=["b1"], Cc=["c1"], slots={"a1": 5, "b1": 500})
        rows = answers(nf=["a1", "b1"]) + [{"sig": "c1", "asked_ms": 0, "result": "found", "slot": 100}]
        self.assertEqual(C.report(p, rows)["ab_older_than_oldest_found_control_n"], 1)
        self.assertIsNone(C.report(p, answers(nf=["a1", "b1"]))["ab_older_than_oldest_found_control_n"])


class TestChange8Hash(Base):
    def edit(self, fn):
        mk_plan(self, item4(wp=[("P0", ["a1"], None)], closes=[("P1", ["b1"])]))
        pth = self.out / C.PLAN_NAME
        pl = json.loads(pth.read_text())
        fn(pl)
        pth.write_text(json.dumps(pl))

    def test_each_covered_field_is_checked(self):
        for name, fn in (("triggers", lambda p: p["triggers"].clear()), ("slots", lambda p: p["slots"].update(a1=1)),
                         ("window", lambda p: p["window"].update(until_ms=1)), ("item4", lambda p: p.update(item4_sha256="z")),
                         ("malformed", lambda p: p.update(malformed_entries_n=5)), ("interim", lambda p: p.update(interim=True)),
                         ("sets", lambda p: p["sets"]["A"].append("zz")), ("cap", lambda p: p.update(call_cap=0))):
            self.setUp()
            self.edit(fn)
            with self.assertRaises(SystemExit, msg=name):
                C.load_plan(self.out)


class TestReviewerNotes(Base):
    def test_torn_trailing_line_tolerated_counted_and_sealed(self):
        mk_plan(self, item4(wp=[("P0", ["a1"], None)]))
        ck, f = Clock(), Fake()
        C.cmd_ask(self.out, f, lambda s: None, ck)
        st = self.out / C.STATE_NAME
        with open(st, "a") as fh:
            fh.write('{"asked_ms": 5, "sig": "a1", "res')
        rows, torn = C.read_jsonl_ex(st)
        self.assertEqual((len(rows), torn), (1, 1))
        rep, _ = C.cmd_report(self.out)
        self.assertEqual(rep["state_torn_lines_n"], 1)
        ck.t += 600_000
        C.cmd_reask(self.out, f, lambda s: None, ck)  # appends after a torn line without gluing onto it
        rows, torn = C.read_jsonl_ex(st)
        self.assertEqual((len(rows), torn), (2, 1))
        with open(st, "a") as fh:
            fh.write("junk\n")
        with self.assertRaises(SystemExit):
            C.read_jsonl_ex(st)

    def test_append_fsyncs(self):
        import unittest.mock as m
        with m.patch("os.fsync") as fs:
            C.append_jsonl(self.tmp / "x.jsonl", {"a": 1})
        self.assertEqual(fs.call_count, 1)

    def test_redirect_is_not_followed(self):
        self.assertIsNone(C.NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://elsewhere.example"))

    def test_http_transport_uses_no_redirect_opener(self):
        import unittest.mock as m
        with m.patch("urllib.request.build_opener") as bo:
            bo.return_value.open.return_value.__enter__.return_value.read.return_value = b'{"result": null}'
            self.assertEqual(C.http_transport(C.RPC_URL, {}, 1), {"result": None})
        self.assertIs(bo.call_args[0][0], C.NoRedirect)


SAMPLE = Path(__file__).parent / "testdata" / "h5_tipauth_item4_71f7522_sample.json"


class TestRealItem4Sample(Base):
    """SAMPLE was produced by RUNNING tools/h5_tipauth_item4.py (`git show origin/claude/h5-tipauth-item4-count:...`, PR #613 head
    71f7522b5ac14df33a0f6b29f8a2279483a1c340) with `--since-ms 1759996800000 --until-ms 1760083200000 --expect-code-sha 5db78a66...` on a synthetic shadow dir
    (1 start, 1 would-pass pv trigger with 2 dropped entries, 2 tipauth-0 ws>=1 closes: one partial 25/20 entries, one with 1 entry, 1 hb)."""

    def load(self):
        return json.loads(SAMPLE.read_text())

    def test_mapping_matches_real_output(self):
        raw = SAMPLE.read_bytes()
        p = C.build_plan(json.loads(raw), self.shadow, C.sha256_bytes(raw))
        self.assertEqual(p["sets"]["A"], ["wpSigA", "wpSigB"])
        self.assertEqual(len(p["sets"]["B"]), 21)
        self.assertEqual((p["b_closes_n"], p["b_partial_closes_n"], p["b_candidates_n"]), (2, 1, 21))
        self.assertFalse(p["interim"])
        self.assertEqual(p["window"], {"origin_t_ms": 1759996801000, "until_ms": 1760083200000, "since_ms": 1759996800000})
        self.assertEqual(p["triggers"], [{"sigs": ["wpSigA", "wpSigB"], "fully_enumerated": True, "variant": "pv"}])
        self.assertEqual(p["slots"]["wpSigB"], 105)
        self.assertEqual(p["slots"]["cl19"], 120)
        self.assertEqual(p["malformed_entries_n"], 0)
        # every key the tool reads is in the real output
        self.assertEqual(self.load()["tool"], "h5_tipauth_item4")

    def test_missing_keys_fail_closed(self):
        d = self.load()
        del d["since_ms"]
        with self.assertRaises(SystemExit):
            C.build_plan(d, self.shadow, "x")
        d = self.load()
        for r in d["item7_census_input"]["would_pass_triggers"]:
            del r["variant"]
        p = C.build_plan(d, self.shadow, "x")
        t = C.report(p, answers(nf=p["sets"]["A"] + p["sets"]["B"]))["thresholds"]["answered_triggers_ge_80pct"]
        self.assertIsNone(t["met"])
        d = self.load()
        for r in d["item7_census_input"]["tipauth0_ws_ge1_nogap_closes"]:
            del r["partial"]
        self.assertEqual(C.build_plan(d, self.shadow, "x")["b_partial_closes_n"], 2)


if __name__ == "__main__":
    unittest.main()
