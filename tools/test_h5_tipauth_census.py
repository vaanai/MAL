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

    return lambda pairs: R(pairs)


def ent(sig, slot=100):
    return [sig, 0, slot]


def item4(wp=(), closes=(), origin=1000, until=10**9):
    rows = [{"pool": p, "dropped": [ent(s) for s in ss], "dropped_n": n if n is not None else len(ss), "variant": "pv", "outcome": 9}
            for p, ss, n in wp]
    cl = [{"pool": p, "dropped": [ent(s) for s in ss], "dropped_n": len(ss), "t_ms": 5, "pnl": 1} for p, ss in closes]
    return {"tool": "h5_tipauth_item4", "window": {"origin_t_ms": origin, "elapsed_h": 3}, "until_ms": until,
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
        for bad in ("outcome", "pnl", "outcome_bx10", "verdict", "other", "variant"):
            self.assertNotIn(bad, seen4 | seenS)
        self.assertTrue({"pool", "dropped"} <= seen4 and "base_unmatched_union" in seenS)


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
        rep = C.cmd_report(self.out)
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


def plan_of(A=(), B=(), Cc=(), trig=()):
    sets = {"A": list(A), "B": list(B), "C": list(Cc)}
    return {"tool": C.TOOL, "selection_sha256": "h", "item4_sha256": "i", "sizes": {k: len(v) for k, v in sets.items()}, "sets": sets,
            "triggers": [{"sigs": list(s), "fully_enumerated": fe} for s, fe in trig]}


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

    def test_report_cli_writes_and_hashes(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            sel = C.sha256_bytes(C.dumps({"A": ["a1"], "B": [], "C": []}).encode())
            C.write_new(d / C.PLAN_NAME, C.dumps({**plan_of(A=["a1"]), "selection_sha256": sel}))
            self.assertEqual(C.main(["report", "--out-dir", str(d)]), 0)
            self.assertTrue((d / C.REPORT_NAME).exists())


if __name__ == "__main__":
    unittest.main()
