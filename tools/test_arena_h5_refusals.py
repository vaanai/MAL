"""DEC-031 Amendment 1 in tools/arena_h5_score.py: a pv trigger record that the live executor's own decision-time parser refuses (`tools.h5_executor.parse_shadow_trigger(row, "pv", None)`
returns a `bad_intent` refusal) is the frozen bot's pre-send refusal. It is not an attempt, it is never priced, and its outcome is never looked up. Synthetic records only: nothing
here opens an arena, shadow, canary or sealed directory (the shadow dirs are temp dirs made from the fresh-forward engine on 2027 synthetic pool tapes)."""
from __future__ import annotations

import collections
import contextlib
import copy
import hashlib
import io
import json
import random
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import arena_h5_score as sc
from tools import h5_executor as ex
from tools import h5_shadow as h5
from tools.test_arena_h5_score import SPS_SPAN_S, START_MS, T0, V1, V2, Base, entry, live_like, of, records
from tools.test_h5_shadow_fresh_forward import SHA, fresh_engine, pool_tape, run_pools

ROOT = Path(__file__).resolve().parent.parent
D0S = (T0 // 86400) * 86400  # 2027-01-15T00:00Z, seconds
DAY_MS = sc.DAY_MS


def set_triggers(recs, pools_to_fields):
    """Set fields on the pv trigger record of each named pool: {pool: {field: value}}; a value of `DROP` removes the key."""
    for r in recs:
        if r.get("type") == "trigger" and r.get("variant") == "pv" and r.get("pool") in pools_to_fields:
            for k, v in pools_to_fields[r["pool"]].items():
                if v is DROP:
                    r.pop(k, None)
                else:
                    r[k] = v
    return recs


DROP = object()


def bulk_records(per_day, refuse=lambda day, k: None, start_ms=START_MS):
    """per_day[d] pools on day d, one a minute from 09:00Z. refuse(day, k) -> a {field: value} dict that makes that pool's trigger a refusal, or None."""
    eng, out = fresh_engine(start_ms)
    tapes, meta, i = [], {}, 0
    for day, n in enumerate(per_day):
        for k in range(n):
            tapes.append(pool_tape(f"Pool{i:04d}", 1000 + 5000 * i, D0S + day * 86400 + 9 * 3600 + k * 60, filler=True))
            meta[f"Pool{i:04d}"] = (day, k)
            i += 1
    run_pools(eng, *tapes)
    live_like(out)
    fields = {}
    for pool, (day, k) in meta.items():
        f = refuse(day, k)
        if f:
            fields[pool] = f
    for r in out:
        if r["type"] == "trigger" and r["variant"] == "pv":
            r["t_detect_ms"] = r["block_time"] * 1000 + 1_000
    set_triggers(out, fields)
    return [{"type": "start", "t_ms": start_ms, "start_ms": start_ms, "code_sha": SHA, "fresh_forward": "ARENA", "dirty": False}] + out, set(fields)


class Guard(dict):
    """A record store that raises when a REFUSED pool's record is looked up: the proof that a refused row's outcome (or bx10, strip, pool record) is never read."""

    def __init__(self, base, forbidden):
        super().__init__(base)
        self.forbidden, self.hits = set(forbidden), []

    def _check(self, key):
        if key in self.forbidden:
            self.hits.append(key)
            raise AssertionError(f"the record of refused pool {key!r} was looked up")

    def get(self, key, default=None):
        self._check(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self._check(key)
        return super().__getitem__(key)

    def __contains__(self, key):
        self._check(key)
        return super().__contains__(key)


def direct_parse(trigger_record):
    """The executor's parser called directly, on a copy of the record as the shadow wrote it."""
    return ex.parse_shadow_trigger(copy.deepcopy({k: v for k, v in trigger_record.items() if k != "_start"}), "pv", None)


# ----------------------------------------------------------------------------------------------------------------------------- the parser is the executor's own
class ParserIdentityTests(Base):
    def test_the_scorer_calls_the_executors_own_function_object_with_the_amendments_arguments(self):
        self.assertIs(sc.executor_parser(), ex.parse_shadow_trigger)
        self.assertFalse(hasattr(sc, "parse_shadow_trigger"))  # no copy and no alias in the scorer's namespace
        spy = mock.Mock(wraps=ex.parse_shadow_trigger)
        with mock.patch.object(ex, "parse_shadow_trigger", spy):
            self.assertIs(sc.executor_parser(), spy)  # looked up at call time on the executor's module
            res = self.book(records(3), name="h5-shadow-spy")
        self.assertEqual(spy.call_count, 3)
        for call in spy.call_args_list:
            row, variant, tipauth = call.args
            self.assertEqual((variant, tipauth, call.kwargs), ("pv", None, {}))
            self.assertEqual((row["type"], row["variant"]), ("trigger", "pv"))
            self.assertNotIn("_start", row)  # the record as the shadow wrote it
        self.assertEqual(res.counts["counted"], 3)

    def test_the_pinned_executor_blob(self):
        self.assertEqual(sc.EXECUTOR_BLOB, "03995837317f")
        self.assertTrue(sc.git_blob_sha1(Path(ex.__file__).read_bytes()).startswith("03995837317f"))
        self.assertEqual(sc.git_blob_sha1(b""), "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391")  # git's empty blob
        self.assertEqual(sc.git_blob_sha1(b"hello\n"), "ce013625030ba8dba906f756967f9e9ca394464a")

    def test_another_executor_blob_is_refused(self):
        other = self.root / "h5_executor.py"
        other.write_text("def parse_shadow_trigger(row, variant='pv', tipauth=None):\n    return None, None\n")
        fake = mock.Mock(__file__=str(other))
        with mock.patch.object(sc, "_EXECUTOR", None), mock.patch.object(sc.importlib, "import_module", return_value=fake):
            with self.assertRaises(sc.ParserFault) as cm:
                sc.executor_module()
        self.assertIn("03995837317f", str(cm.exception))

    def test_a_python_without_the_executors_requirements_stops_the_scorer_and_never_falls_back_to_a_copy(self):
        with mock.patch.object(sc, "_EXECUTOR", None), mock.patch.object(sc.importlib, "import_module", side_effect=ModuleNotFoundError("No module named 'solders'")):
            with self.assertRaises(sc.ParserFault) as cm:
                sc.executor_refusal({"type": "trigger", "variant": "pv", "pool": "P"})
        self.assertIn("solders", str(cm.exception))
        self.assertIn("does not carry a copy", str(cm.exception))
        # register / halt / refit do not import the executor
        path = self.arena / "bots.json"
        with mock.patch.object(sc, "_EXECUTOR", None), mock.patch.object(sc.importlib, "import_module", side_effect=ModuleNotFoundError("solders")):
            sc.register_books(path, frozen_sha=SHA, sockets=3, endpoints=["e"], host="h", now_ms=1_000, clock_start_ms=5_000)
            sc.set_window_end(path, 9_000)


class ExecutorImportTests(unittest.TestCase):
    """Importing the executor from the scorer has no side effects: no socket, no subprocess, no file read but python sources (so no key read), checked with an audit hook in a fresh
    interpreter. It also pulls in no r4b, l3 or laya module (DEC-031 s3.1, EXP-027 isolation)."""

    SCRIPT = """
import json, sys
sys.path.insert(0, sys.argv[1])
ev = []
def hook(event, args):
    if event in ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "socket.bind", "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.fork", "os.forkpty", "ctypes.dlsym"):
        ev.append([event, repr(args)[:80]])
    elif event == "ctypes.dlopen" and args != (None,):
        ev.append([event, repr(args)[:80]])
    elif event == "open" and isinstance(args[0], (str, bytes)):
        p = args[0].decode(errors="replace") if isinstance(args[0], bytes) else args[0]
        if not p.endswith((".py", ".pyc")):
            ev.append(["open", p])
sys.addaudithook(hook)
from tools import arena_h5_score as sc
mod = sc.executor_module()
print(json.dumps({"events": ev, "parser": mod.__name__, "bad_modules": sorted(m for m in sys.modules if any(w in m.lower() for w in ("r4b", "l3", "laya")))}))
"""

    def test_no_network_no_subprocess_no_key_or_data_file_read_and_no_r4b_l3_laya(self):
        out = subprocess.run([sys.executable, "-I", "-c", self.SCRIPT, str(ROOT)], capture_output=True, text=True, timeout=120, check=True).stdout
        got = json.loads(out)
        self.assertEqual(got["events"], [])
        self.assertEqual(got["parser"], "tools.h5_executor")
        self.assertEqual(got["bad_modules"], [])


# ----------------------------------------------------------------------------------------------------------------------------- a refusal is not an attempt
REFUSALS = {  # field -> (value, the parser's reason): what the scorer used to price, plus base_breaks
    "s0_minus_announced_slots": (3, "bad_intent:s0_minus_announced_slots"),
    "sps_span_s": (60.0, "bad_intent:sps_span"),
    "boost_spent_sol": (17.58, "bad_intent:boost_spent"),
    "v_missing": (True, "bad_intent:v_missing"),
    "base_breaks_unresolved_settled": (2, "bad_intent:base_breaks_unresolved_settled"),
}


class RefusalTests(Base):
    def test_each_parser_refusal_is_a_refusal_with_its_own_reason_never_priced_never_an_attempt_never_open(self):
        for field, (value, why) in REFUSALS.items():
            recs = records(2, mutate=lambda rs, f=field, v=value: set_triggers(rs, {"Pool0000": {f: v}}))
            res = self.book(recs, name=f"h5-shadow-{field}")
            self.assertEqual([(r["pool"], r["reason"]) for r in res.refusals], [("Pool0000", why)], field)
            self.assertEqual(direct_parse(of(recs, "trigger")[0])[1], why, field)  # the executor says the same
            self.assertEqual((len(res.decisions), res.counts["counted"], res.counts["open"], len(res.exclusions)), (1, 1, 0, 0), field)
            self.assertEqual({r["pool"] for r in res.rows}, {"Pool0001"}, field)
            self.assertEqual(len(res.ages), 1, field)  # no feed-delay sample from a refused trigger
            self.assertEqual(res.triggers_seen, 2, field)
            s = sc.summarize(V1, res, 0)
            self.assertEqual((s["attempts"], s["refused"], s["triggers_seen"], s["refused_by_reason"]), (1, 1, 2, {why: 1}), field)

    def test_a_refused_trigger_with_no_outcome_is_refused_not_open(self):
        def cut(recs):
            set_triggers(recs, {"Pool0000": {"v_missing": True}})
            recs[:] = [r for r in recs if not (r.get("pool") == "Pool0000" and r["type"] in ("outcome", "outcome_bx10", "strip", "pool"))]
        res = self.book(records(2, mutate=cut))
        self.assertEqual((res.counts["open"], res.open_decisions, len(res.refusals)), (0, [], 1))
        self.assertEqual(len(res.decisions), 1)  # only the other pool is an attempt
        # a NON-refused trigger with no outcome is still open
        def cut_ok(recs):
            recs[:] = [r for r in recs if not (r.get("pool") == "Pool0000" and r["type"] in ("outcome", "outcome_bx10", "strip", "pool"))]
        res = self.book(records(2, mutate=cut_ok), name="h5-shadow-open")
        self.assertEqual((res.counts["open"], len(res.open_decisions), len(res.refusals)), (1, 1, 0))

    def test_the_outcome_bx10_strip_and_pool_records_of_a_refused_pool_are_never_looked_up(self):
        recs = records(6, mutate=lambda rs: set_triggers(rs, {f"Pool000{i}": {f: v} for i, (f, (v, _)) in enumerate(REFUSALS.items())}))
        d = sc.ShadowDir(self.shadow_dir(recs), collections.Counter())
        refused = {pool for pool, trig in d.triggers.items() if direct_parse(trig)[1]}
        self.assertEqual(refused, {f"Pool000{i}" for i in range(5)})
        guards = []
        for bot in (V1, V2):
            for name in ("outcomes", "bx", "strips", "pools"):
                g = Guard(getattr(d, name), refused)
                setattr(d, name, g)
                guards.append(g)
            res = sc.score_book(bot, entry(), [d])
            self.assertEqual((len(res.refusals), res.counts["counted"]), (5, 1))
        self.assertEqual([g.hits for g in guards], [[]] * len(guards))
        with self.assertRaises(AssertionError):  # the guard works: it does raise on a refused pool
            d.outcomes.get("Pool0000")

    def test_price_trade_is_not_called_for_a_refused_trigger(self):
        recs = records(3, mutate=lambda rs: set_triggers(rs, {"Pool0001": {"sps_span_s": 5.0}}))
        with mock.patch.object(sc, "price_trade", wraps=sc.price_trade) as pt:
            res = self.book(recs)
        self.assertEqual([c.args[2]["pool"] for c in pt.call_args_list], ["Pool0000", "Pool0002"])
        self.assertEqual(len(res.rows), 6)

    def test_refusals_leave_the_read_instant_and_its_attempt_count_alone(self):
        # 5 dates x 22 triggers, 3 refused a day: 95 non-refused attempts before the day-5 midnight, so the read waits for day 5's (22 more) -> the boundary after day 5, not day 4.
        recs, refused = bulk_records([22] * 5 + [22], refuse=lambda day, k: {"sps_span_s": 10.0} if day < 5 and k < 3 else None)
        self.assertEqual(len(refused), 15)
        res = self.book(recs)
        s = sc.summarize(V1, res, D0S * 1000 + 10 * DAY_MS)
        self.assertEqual((s["attempts"], s["refused"], s["triggers_seen"]), (117, 15, 132))
        self.assertEqual(s["read_instant_ms"], D0S * 1000 + 6 * DAY_MS)
        every_trigger = res.decisions + [r["decision_ms"] for r in res.refusals]
        self.assertEqual(sc.read_instant(every_trigger, D0S * 1000 + 10 * DAY_MS), D0S * 1000 + 5 * DAY_MS)  # what counting refusals as attempts would have given

    def test_the_old_base_breaks_exclusion_is_gone_and_the_parser_refuses_exactly_what_it_did_and_more(self):
        for v in (1, 5, None, "0", 0.0, True, False, []):
            row = {**of(records(1), "trigger")[0], "base_breaks_unresolved_settled": v}
            self.assertEqual(direct_parse(row)[1], "bad_intent:base_breaks_unresolved_settled", v)  # the executor refuses everything but the integer 0
        row = {**of(records(1), "trigger")[0], "base_breaks_unresolved_settled": 0}
        self.assertIsNone(direct_parse(row)[1])
        self.assertIsNone(sc.executor_refusal(row))


# ----------------------------------------------------------------------------------------------------------------------------- differential: the scorer decides as the parser
def random_record(rng, base, keep=()):
    """A trigger record with 0 to 3 random VALUE mutations (the parser answers bad_intent:<value> or accepts; a junk value can also reach another parser answer). `keep`: fields not mutated."""
    row = copy.deepcopy(base)
    junk = [None, True, False, 0, -1, 1, 2, 3, 0.0, 0.5, 1e12, 17.58, "x", "0", [], {}, float(10**18)]
    value_fields = ["mint", "pool", "s0", "slot", "sps", "q_trigger_sol", "v_print", "v_missing", "base_pre", "sell_token_raw", "boost_spent_sol", "base_breaks_unresolved_settled",
                    "s0_minus_announced_slots", "sps_span_s", "gap", "synthetic_src", "block_time", "t_recv_ms", "s0_t_recv_ms", "base_breaks", "slot_regress", "suppressed"]
    value_fields = [k for k in value_fields if k not in keep]
    for _ in range(rng.choice([0, 0, 1, 1, 2, 3])):
        k = rng.choice(value_fields)
        row[k] = rng.choice(junk) if rng.random() < 0.6 else rng.choice([row.get(k), 100, 150.0, 300.0, 120.0, 0.4, 0.2])
    return row


def classify(row):
    """('ok'|'refused'|'fault', reason) from the executor's parser alone."""
    try:
        t, why = ex.parse_shadow_trigger(copy.deepcopy(row), "pv", None)
    except Exception:  # noqa: BLE001
        return "fault", None
    if t is not None:
        return "ok", None
    if why is None:
        return "fault", None
    if why.startswith("bad_intent:missing_"):
        return "fault", why
    if why.startswith("bad_intent:"):
        return "refused", why
    return "fault", why


class DifferentialTests(Base):
    def test_the_scorers_decision_equals_the_parsers_on_randomized_records(self):
        rng = random.Random(20261019)
        base = {k: v for k, v in of(records(1), "trigger")[0].items()}
        seen = collections.Counter()
        reasons = set()
        for _ in range(3000):
            row = random_record(rng, base)
            if rng.random() < 0.15:  # schema mutations: a dropped key, a synthetic flag that is not the literal false
                if rng.random() < 0.6:
                    row.pop(rng.choice(ex.SHADOW_REQUIRED_KEYS), None)
                else:
                    row["synthetic"] = rng.choice([True, None, 0, "false"])
            kind, why = classify(row)
            seen[kind] += 1
            if kind == "ok":
                self.assertIsNone(sc.executor_refusal(row))
            elif kind == "refused":
                reasons.add(why)
                self.assertEqual(sc.executor_refusal(row), why)
            else:
                with self.assertRaises(sc.ParserFault):
                    sc.executor_refusal(row)
        self.assertEqual({"ok", "refused", "fault"}, set(seen))  # the run was not vacuous
        self.assertTrue(all(seen[k] >= 100 for k in seen), seen)
        self.assertGreaterEqual(len(reasons), 10, sorted(reasons))

    def test_score_book_refuses_exactly_the_triggers_the_parser_refuses(self):
        rng = random.Random(1019)
        n = 80
        recs = records(n)
        base = {r["pool"]: r for r in of(recs, "trigger")}
        expect = {}
        for pool, trig in base.items():
            row = random_record(rng, trig, keep=("pool",))  # `pool` is the join key to the pool's other records
            row["t_detect_ms"] = trig["t_detect_ms"]  # keep the record datable (a junk t_detect_ms stops: SchemaFaultTests)
            kind, why = classify(row)
            if kind == "fault":  # not a value mutation: put the original back
                row = copy.deepcopy(trig)
                kind, why = "ok", None
            trig.clear()
            trig.update(row)
            if kind == "refused":
                expect[pool] = why
        self.assertGreater(len(expect), 8)
        self.assertLess(len(expect), n - 8)
        res = self.book(recs)
        self.assertEqual({r["pool"]: r["reason"] for r in res.refusals}, expect)
        self.assertEqual(len(res.decisions), n - len(expect))
        self.assertEqual(res.triggers_seen, n)
        priced_pools = {r["pool"] for r in res.rows}
        self.assertFalse(priced_pools & set(expect))
        self.assertEqual(res.counts["counted"] + len(res.exclusions) + res.counts["open"], n - len(expect))


# ----------------------------------------------------------------------------------------------------------------------------- schema faults stop the scorer
class SchemaFaultTests(Base):
    def test_a_missing_key_in_any_pv_trigger_record_stops_the_scorer(self):
        for key in ex.SHADOW_REQUIRED_KEYS:
            recs = records(2, mutate=lambda rs, k=key: set_triggers(rs, {"Pool0001": {k: DROP}}))
            with self.assertRaises(sc.ParserFault) as cm:
                self.book(recs, name=f"h5-shadow-drop-{key}")
            self.assertIn("schema fault", str(cm.exception), key)
            self.assertIn(f"bad_intent:missing_{key}", str(cm.exception), key)

    def test_a_pv_trigger_record_that_cannot_be_keyed_by_pool_is_not_skipped_silently(self):
        for v in (None, "", ["x"], 7):
            recs = records(2, mutate=lambda rs, v=v: set_triggers(rs, {"Pool0001": {"pool": v}}))
            with self.assertRaises(sc.ParserFault) as cm:
                self.book(recs, name=f"h5-shadow-pool-{v!r}")
            self.assertIn("pool", str(cm.exception))
        # the fv variant is not scored, and a record of another type without a pool is not a pv trigger
        fv = records(2, mutate=lambda rs: rs.append({"type": "trigger", "variant": "fv", "slot": 1}))
        fv.append({"type": "hb", "t_ms": START_MS})
        self.assertEqual(self.book(fv, name="h5-shadow-fv").counts["counted"], 2)

    def test_a_pre_clock_record_with_a_schema_fault_stops_too(self):
        recs = records(2, mutate=lambda rs: set_triggers(rs, {"Pool0000": {"block_time": DROP}}))
        with self.assertRaises(sc.ParserFault):
            sc.score_book(V1, entry(clock_start_ms=of(recs, "trigger")[0]["t_detect_ms"] + 10**9), [sc.ShadowDir(self.shadow_dir(recs), collections.Counter())])

    def test_an_answer_the_amendment_does_not_name_stops_the_scorer(self):
        for field, value in (("synthetic", True), ("synthetic", None), ("synthetic_src", "ws"), ("synthetic", "false")):
            recs = records(2, mutate=lambda rs, f=field, v=value: set_triggers(rs, {"Pool0000": {f: v}}))
            self.assertEqual(direct_parse(of(recs, "trigger")[0])[1], "synthetic_unconfirmed")
            with self.assertRaises(sc.ParserFault) as cm:
                self.book(recs, name=f"h5-shadow-syn-{field}-{value}")
            self.assertIn("does not name", str(cm.exception))

    def test_a_parser_exception_or_a_record_it_does_not_take_for_a_pv_trigger_stops_the_scorer(self):
        with mock.patch.object(ex, "parse_shadow_trigger", side_effect=RuntimeError("boom")):
            with self.assertRaises(sc.ParserFault) as cm:
                self.book(records(1))
        self.assertIn("RuntimeError", str(cm.exception))
        with mock.patch.object(ex, "parse_shadow_trigger", return_value=(None, None)):
            with self.assertRaises(sc.ParserFault):
                self.book(records(1), name="h5-shadow-none")

    def test_a_trigger_without_a_usable_decision_instant_stops_the_scorer(self):
        for v in ("abc", True, None, []):  # (NaN and Infinity are not strict JSON: the line is a counted bad_shadow_lines)
            recs = records(2, mutate=lambda rs, v=v: set_triggers(rs, {"Pool0000": {"t_detect_ms": v}}))
            with self.assertRaises(sc.ParserFault):
                self.book(recs, name=f"h5-shadow-t-{v}")

    def test_the_cli_stops_with_exit_2_a_clear_error_and_writes_nothing(self):
        d = self.shadow_dir(records(3, mutate=lambda rs: set_triggers(rs, {"Pool0002": {"v_missing": DROP}})), "h5-shadow-2027-01-15")
        sc.register_books(self.arena / "bots.json", frozen_sha=SHA, sockets=3, endpoints=["e"], host="h", now_ms=START_MS - 1_000, clock_start_ms=START_MS)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = sc.main(["score", "--arena-root", str(self.arena), "--shadow-dir", str(d)])
        self.assertEqual(rc, 2)
        self.assertIn("REFUSED: schema fault", err.getvalue())
        self.assertIn("bad_intent:missing_v_missing", err.getvalue())
        self.assertEqual(out.getvalue(), "")
        self.assertFalse(self.ledger.exists())  # both books are scored before anything is written


# ----------------------------------------------------------------------------------------------------------------------------- reconciliation and the 5% cap
def mixed(n, refused=(), gap=(), open_=()):
    def m(recs):
        set_triggers(recs, {f"Pool{i:04d}": {"sps_span_s": 10.0} for i in refused})
        set_triggers(recs, {f"Pool{i:04d}": {"gap": True, "gaps": []} for i in gap})
        recs[:] = [r for r in recs if not (r.get("pool") in {f"Pool{i:04d}" for i in open_} and r["type"] in ("outcome", "outcome_bx10", "strip", "pool"))]
    return records(n, mutate=m)


class ReconciliationTests(Base):
    def test_counted_plus_excluded_plus_refused_plus_open_equals_triggers_seen(self):
        res = self.book(mixed(12, refused=(0, 1, 2), gap=(3,), open_=(4, 5)))
        self.assertEqual((res.counts["counted"], len(res.exclusions), len(res.refusals), res.counts["open"], res.triggers_seen), (6, 1, 3, 2, 12))
        res.reconcile()
        s = sc.summarize(V1, res, 0)
        self.assertEqual((s["attempts"], s["refused"], s["triggers_seen"], s["open"], s["counted"], s["excluded"]), (9, 3, 12, 2, 6, 1))

    def test_a_trigger_lost_or_counted_twice_is_an_error(self):
        res = self.book(mixed(6, refused=(0,), gap=(1,)))
        res.reconcile()
        for attr, delta in (("triggers_seen", 1), ("triggers_seen", -1)):
            bad = copy.copy(res)
            bad.triggers_seen += delta
            with self.assertRaises(sc.ReconciliationError):
                bad.reconcile()
        bad = copy.copy(res)
        bad.refusals = res.refusals + [dict(res.refusals[0])]
        with self.assertRaises(sc.ReconciliationError):
            bad.reconcile()
        bad = copy.copy(res)
        bad.counts = collections.Counter(res.counts)
        bad.counts["counted"] += 1
        with self.assertRaises(sc.ReconciliationError):
            bad.reconcile()

    def test_duplicates_and_out_of_window_triggers_are_not_triggers_seen(self):
        recs = mixed(4, refused=(0,))
        t = sorted(of(recs, "trigger"), key=lambda r: r["pool"])
        res = sc.score_book(V1, entry(clock_start_ms=t[1]["t_detect_ms"]), [sc.ShadowDir(self.shadow_dir(recs), collections.Counter())])
        self.assertEqual(res.triggers_seen, len(res.decisions) + len(res.refusals))
        self.assertEqual(res.triggers_seen + res.counts["skipped_before_clock"], 4)


class CapTests(Base):
    def share(self, n, refused, gap):
        s = sc.summarize(V1, self.book(mixed(n, refused=refused, gap=gap), name=f"h5-shadow-cap-{n}-{len(refused)}-{len(gap)}"), 0)
        return s

    def test_refusals_are_outside_the_cap_the_arena_case_nine_refused_four_counted(self):
        s = self.share(13, refused=range(9), gap=())
        self.assertEqual((s["attempts"], s["counted"], s["excluded"], s["refused"], s["excluded_share"], s["not_decidable"]), (4, 4, 0, 9, 0.0, False))

    def test_the_other_exclusion_classes_stay_under_five_percent_of_the_non_refused_attempts(self):
        # 29 pools, 9 refused -> 20 non-refused attempts. 1 gap_flag = 5.0%: allowed. 2 = 10%: NOT_DECIDABLE.
        s = self.share(29, refused=range(9), gap=(9,))
        self.assertEqual((s["attempts"], s["excluded"], s["excluded_share"], s["not_decidable"], s["excluded_by_reason"]), (20, 1, 0.05, False, {"gap_flag": 1}))
        s = self.share(29, refused=range(9), gap=(9, 10))
        self.assertEqual((s["attempts"], s["excluded"], s["excluded_share"], s["not_decidable"]), (20, 2, 0.1, True))
        # the same two exclusions among 20 attempts WITHOUT refusals, and the base is the same: refusals neither help nor hurt the share
        s = self.share(20, refused=(), gap=(9, 10))
        self.assertEqual((s["attempts"], s["excluded_share"], s["not_decidable"]), (20, 0.1, True))

    def test_open_attempts_are_not_in_the_cap_base_and_refusals_are_not_open(self):
        s = sc.summarize(V1, self.book(mixed(10, refused=(0, 1), gap=(2,), open_=(3,)), name="h5-shadow-cap-open"), 0)
        self.assertEqual((s["attempts"], s["refused"], s["open"], s["counted"], s["excluded"]), (8, 2, 1, 6, 1))
        self.assertAlmostEqual(s["excluded_share"], 1 / 7)


# ----------------------------------------------------------------------------------------------------------------------------- the ledger and the read
class RefusalLedgerTests(Base):
    def test_refusals_have_their_own_write_once_day_file_with_decision_time_fields_only(self):
        res = self.book(records(3, mutate=lambda rs: set_triggers(rs, {"Pool0001": {"v_missing": True}})))
        counts = collections.Counter()
        sc.write_ledger(V1, res.rows, res.exclusions, self.ledger, counts, refusals=res.refusals)
        self.assertEqual((counts["written_trades"], counts["written_exclusions"], counts["written_refusals"]), (6, 0, 1))
        files = list((self.ledger / V1).glob("refusals-????-??-??.jsonl"))
        self.assertEqual(len(files), 1)
        row = json.loads(files[0].read_text())
        self.assertEqual(set(row), {"refusal_id", "bot_id", "pool", "mint", "reason", "decision_ms", "frozen_sha"})
        self.assertEqual((row["refusal_id"], row["reason"]), (f"{V1}:Pool0001", "bad_intent:v_missing"))
        self.assertNotIn("Pool0001", "".join(f.read_text() for f in (self.ledger / V1).glob("trades-*.jsonl")))
        before = files[0].read_bytes()
        c2 = collections.Counter()
        sc.write_ledger(V1, res.rows, res.exclusions, self.ledger, c2, refusals=res.refusals)
        self.assertEqual((c2["duplicate_refusals"], c2["written_refusals"]), (1, 0))
        self.assertEqual(files[0].read_bytes(), before)

    def test_dry_run_writes_no_refusal_file(self):
        res = self.book(records(2, mutate=lambda rs: set_triggers(rs, {"Pool0000": {"v_missing": True}})))
        counts = collections.Counter()
        sc.write_ledger(V1, res.rows, res.exclusions, self.ledger, counts, dry_run=True, refusals=res.refusals)
        self.assertEqual(counts["written_refusals"], 1)
        self.assertFalse(self.ledger.exists())


class CliAndReadTests(Base):
    BOOK_KEYS = {"registered", "attempts", "counted", "excluded", "excluded_share", "not_decidable", "excluded_by_reason", "refused", "refused_by_reason", "triggers_seen", "attempt_definition",
                 "open", "age_clamped_to_zero", "trigger_age_exclusions", "no_block_time_exclusions", "feed_delay_ms_p50", "feed_delay_ms_p90", "feed_delay_n", "derived_last_deciding_leg",
                 "bx10_error_priced_v1", "skipped_before_clock", "skipped_after_window", "duplicate_pool", "sealed_decision_only", "refused_dirs", "restarts", "downtime_ms", "read_instant_ms",
                 "read_reached", "boost_before_exit_share", "boost_known", "boost_not_proposed_for_live", "rows_scored", "ledger_superseded"}

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = sc.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def setup_book(self, recs, name="h5-shadow-2027-01-15"):
        d = self.shadow_dir(recs, name)
        sc.register_books(self.arena / "bots.json", frozen_sha=SHA, sockets=3, endpoints=["e"], host="h", now_ms=START_MS - 1_000, clock_start_ms=START_MS)
        return d

    def test_the_dry_run_prints_refusals_by_reason_beside_every_count_and_only_counts_and_timing(self):
        d = self.setup_book(records(8, mutate=lambda rs: set_triggers(rs, {"Pool0000": {"sps_span_s": 1.0}, "Pool0001": {"sps_span_s": 1.0}, "Pool0002": {"base_breaks_unresolved_settled": 4},
                                                                         "Pool0003": {"v_missing": True}, "Pool0004": {"gap": True, "gaps": []}})))
        rc, out, _ = self.run_main("score", "--arena-root", str(self.arena), "--shadow-dir", str(d), "--dry-run")
        self.assertEqual(rc, 0)
        rep = json.loads(out)
        for bot in (V1, V2):
            b = rep["books"][bot]
            self.assertEqual(set(b), self.BOOK_KEYS)  # an allowlist: no price, fill, exit, stake or P&L field can appear
            self.assertEqual((b["attempts"], b["refused"], b["triggers_seen"], b["counted"], b["excluded"], b["open"]), (4, 4, 8, 3, 1, 0))
            self.assertEqual(b["refused_by_reason"], {"bad_intent:base_breaks_unresolved_settled": 1, "bad_intent:sps_span": 2, "bad_intent:v_missing": 1})
            self.assertEqual(b["ledger_superseded"], {"trade_pools": 0, "exclusion_pools": 0})
            self.assertRegex(b["attempt_definition"], r"refused 4 of 8 triggers\.$")
        for word in ("lamports", "pnl", "net_", "nofail", "legs", "qe", "spot"):
            self.assertNotIn(word, out)
        self.assertFalse(self.ledger.exists())

    def test_the_default_command_form_without_dry_run_writes_trades_exclusions_and_refusals(self):
        d = self.setup_book(records(4, mutate=lambda rs: set_triggers(rs, {"Pool0000": {"v_missing": True}, "Pool0001": {"gap": True, "gaps": []}})))
        rc, out, _ = self.run_main("--arena-root", str(self.arena), "--shadow-dir", str(d))
        self.assertEqual(rc, 0)
        c = json.loads(out)["counts"]
        self.assertEqual((c["written_trades"], c["written_exclusions"], c["written_refusals"]), (12, 2, 2))
        self.assertEqual(len(list(self.ledger.glob("*/refusals-*.jsonl"))), 2)

    def test_the_disclosure_sentence(self):
        s = sc.attempt_disclosure(9, 13)
        self.assertRegex(s, r"^Attempt definition amended after the clock \(DEC-031 Amendment 1, [0-9a-f]{64}, 20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ\), with counts only in view; refused 9 of 13 triggers\.$")
        self.assertIn(hashlib.sha256(Path(sc.__file__).read_bytes()).hexdigest(), s)
        self.assertEqual(sc.scorer_sha256(), hashlib.sha256((ROOT / "tools" / "arena_h5_score.py").read_bytes()).hexdigest())
        self.assertIn(sc.AMENDMENT_1_INSTANT, s)

    def test_the_read_output_carries_refusals_by_reason_and_the_disclosure(self):
        recs, refused = bulk_records([22] * 5 + [22], refuse=lambda day, k: ({"sps_span_s": 10.0} if k == 0 else {"base_breaks_unresolved_settled": 1}) if day < 5 and k < 3 else None)
        self.assertEqual(len(refused), 15)
        self.setup_book(recs, "h5-shadow-bulk")
        boundary = D0S * 1000 + 6 * DAY_MS
        rc, out, _ = self.run_main("score", "--arena-root", str(self.arena), "--shadow-dir", str(self.arena / "h5-shadow-bulk"), "--now-ms", str(boundary + 3_600_000))
        book = json.loads(out)["books"][V1]
        self.assertEqual((book["read_instant_ms"], book["attempts"], book["refused"]), (boundary, 117, 15))
        rec = json.loads((self.ledger / V1 / "read-instant.json").read_text())
        self.assertEqual((rec["attempts_before"], rec["refused_before"], rec["triggers_before"], rec["open_before_read"]), (117, 15, 132, 0))
        self.assertEqual(rec["refused_by_reason_before"], {"bad_intent:base_breaks_unresolved_settled": 10, "bad_intent:sps_span": 5})
        self.assertRegex(rec["attempt_definition"], r"refused 15 of 132 triggers\.$")
        rc, out, _ = self.run_main("refit", "--arena-root", str(self.arena), "--now-ms", str(boundary + 1))
        got = json.loads(out)[V1]
        self.assertEqual((got["attempts_before"], got["refused_before"], got["triggers_before"], got["refused_by_reason"]), (117, 15, 132, rec["refused_by_reason_before"]))
        self.assertRegex(got["attempt_definition"], r"refused 15 of 132 triggers\.$")
        doc = json.loads(next((self.ledger / V1).glob("refit-*.json")).read_text())
        self.assertEqual((doc["refused_before"], doc["triggers_before"], doc["trades_pools"], doc["ledger_superseded_exclusion_pools"]), (15, 132, 117, 0))
        self.assertEqual(doc["attempt_definition"], got["attempt_definition"])
        self.assertNotIn("net_lamports", out)

    def refit_setup(self):
        recs, _ = bulk_records([22] * 5 + [22], refuse=lambda day, k: {"sps_span_s": 10.0} if day < 5 and k < 3 else None)
        self.setup_book(recs, "h5-shadow-bulk")
        boundary = D0S * 1000 + 6 * DAY_MS
        self.run_main("score", "--arena-root", str(self.arena), "--shadow-dir", str(self.arena / "h5-shadow-bulk"), "--now-ms", str(boundary + 3_600_000))
        return boundary

    def refit(self, boundary):
        return json.loads(self.run_main("refit", "--arena-root", str(self.arena), "--now-ms", str(boundary + 1))[1])[V1]

    def test_the_refit_reconciles_trades_exclusions_and_refusals_against_the_recorded_triggers(self):
        boundary = self.refit_setup()
        ref = next((self.ledger / V1).glob("refusals-*.jsonl"))
        lines = ref.read_text().splitlines()
        ref.write_text("\n".join(lines[1:]) + "\n")  # a refusal vanishes from the ledger
        self.assertIn("does not hold every attempt and every refusal", self.refit(boundary)["refused"])
        self.assertEqual(list((self.ledger / V1).glob("refit-*.json")), [])
        ref.write_text("\n".join(lines) + "\n")
        rec_path = self.ledger / V1 / "read-instant.json"
        good = json.loads(rec_path.read_text())
        rec_path.write_text(json.dumps({**good, "triggers_before": good["triggers_before"] + 1}))
        self.assertIn("!= triggers seen", self.refit(boundary)["refused"])
        legacy = {k: v for k, v in good.items() if k not in ("refused_before", "triggers_before")}
        rec_path.write_text(json.dumps(legacy))
        self.assertIn("predates DEC-031 Amendment 1", self.refit(boundary)["refused"])
        rec_path.write_text(json.dumps(good))
        self.assertEqual(self.refit(boundary)["refused_before"], 15)

    def test_a_ledger_row_from_before_the_amendment_for_a_pool_that_is_a_refusal_now(self):
        boundary = self.refit_setup()
        pool = json.loads(next((self.ledger / V1).glob("refusals-*.jsonl")).read_text().splitlines()[0])
        # an earlier (pre-amendment) run excluded it: the exclusion row is ignored by the refit and counted
        day = sc.utc_day(pool["decision_ms"])
        with open(self.ledger / V1 / f"exclusions-{day}.jsonl", "a") as fh:
            fh.write(json.dumps({"exclusion_id": f"{V1}:{pool['pool']}", "bot_id": V1, "pool": pool["pool"], "mint": pool["mint"], "reason": "base_breaks", "decision_ms": pool["decision_ms"],
                                 "frozen_sha": SHA}) + "\n")
        rc, out, _ = self.run_main("score", "--arena-root", str(self.arena), "--shadow-dir", str(self.arena / "h5-shadow-bulk"), "--dry-run", "--now-ms", str(boundary + 3_600_000))
        self.assertEqual(json.loads(out)["books"][V1]["ledger_superseded"], {"trade_pools": 0, "exclusion_pools": 1})
        self.assertEqual(self.refit(boundary)["attempts_before"], 117)
        self.assertEqual(json.loads(next((self.ledger / V1).glob("refit-*.json")).read_text())["ledger_superseded_exclusion_pools"], 1)
        next((self.ledger / V1).glob("refit-*.json")).unlink()
        # a PRICED row for it is not ignored: the refit stops until someone rules on it
        with open(next((self.ledger / V1).glob("trades-*.jsonl")), "a") as fh:
            fh.write(json.dumps({"trade_id": f"{V1}:{pool['pool']}:0.30", "bot_id": V1, "pool": pool["pool"], "decision_ms": pool["decision_ms"], "stake_sol": 0.3, "legs": {}}) + "\n")
        rc, out, _ = self.run_main("score", "--arena-root", str(self.arena), "--shadow-dir", str(self.arena / "h5-shadow-bulk"), "--dry-run", "--now-ms", str(boundary + 3_600_000))
        self.assertEqual(json.loads(out)["books"][V1]["ledger_superseded"], {"trade_pools": 1, "exclusion_pools": 1})
        self.assertIn("need a ruling", self.refit(boundary)["refused"])


class AmendmentTextTests(unittest.TestCase):
    OLD_SHA = "a3dd6b31c38081b535cb965223623ae5797341b7374b0d3b3196799e79e65c27"

    def test_the_dec_text_names_this_scorer_file(self):
        text = (ROOT / "DEC" / "DEC-031-forward-paper-arena.md").read_text()
        want = ('DEC-031 Amendment 1 (before any H5 arena read instant; outcome-blind). For h5v1-boostfloor-a1 and h5v2-boostclock-a1, a pv trigger record for which '
                '`tools.h5_executor.parse_shadow_trigger(row, "pv", None)` at blob 03995837317f returns a `bad_intent` refusal is the frozen bot\'s own pre-send refusal. It is not an entry decision. '
                'It is not an attempt for §2\'s count or read instant, and it is never priced. Its count by reason is reported beside every count and the read. A `bad_intent:missing_*` result is a '
                'schema fault and stops the scorer. Every other exclusion stays under the 5% cap, computed over non-refused attempts. Only the counts of jobs #1175 and #1176 were in view. '
                'No arena H5 outcome, fill, exit, strip, ledger row or P&L was opened. Code, frozen sha, clock, window, stake, deciding cell and gate are unchanged. '
                f'`tools/arena_h5_score.py` changes from sha256 {self.OLD_SHA} to {sc.scorer_sha256()}.')
        self.assertIn(want, text)
        self.assertIn(sc.AMENDMENT_1_INSTANT, text)
        self.assertEqual(sc.EXECUTOR_BLOB, "03995837317f")


if __name__ == "__main__":
    unittest.main()
