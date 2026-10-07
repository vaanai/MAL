"""Tests for tools/exp016_constancy.py. Fixtures only; nothing here opens a real data path (no /data/mal read, no network)."""

from __future__ import annotations

import base64
import contextlib
import io
import json
import struct
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

from solders.pubkey import Pubkey

import tools.exp016_constancy as c
import tools.exp016_screen as x
from tools import pumpswap_decompose as dec
from tools import pumpswap_virtual_history as pvh

warnings.filterwarnings("ignore", category=FutureWarning)
SOL = 1_000_000_000
POOL_A, POOL_B = str(Pubkey.new_unique()), str(Pubkey.new_unique())
POOL_C = str(Pubkey.new_unique())


def event_bytes(pool: str, *, out=1_000_000_000, pq=10 * SOL, pb=80_000_000_000_000, net_extra=0) -> bytes:
    vals = {n: 0 for n in dec.EVENT_FIELDS_U64}
    lp = 2_000_000
    vals.update(base_amount_out=out, pool_base_token_reserves=pb, pool_quote_token_reserves=pq, quote_amount_in_with_lp_fee=SOL // 10 + lp + net_extra, lp_fee=lp)
    raw = dec.BUY_EVENT_DISC
    for n in dec.EVENT_FIELDS_U64:
        raw += struct.pack("<q" if n == "timestamp" else "<Q", vals[n])
    for n in dec.EVENT_PUBKEYS:
        raw += bytes(Pubkey.from_string(pool)) if n == "pool" else bytes(Pubkey.new_unique())
    return raw + struct.pack("<QQ", 1, 2)


def tx_for(pool: str, **kw) -> dict:
    return {"meta": {"err": None, "logMessages": ["Program log: x", "Program data: " + base64.b64encode(event_bytes(pool, **kw)).decode()]}}


def row(pool, sig, slot, side="buy", venue="pumpswap", **kw):
    return {"venue": venue, "side": side, "pool": pool, "signature": sig, "slot": slot, **kw}


def setUpModule():
    patcher = mock.patch.object(c, "BACKOFF_S", (0.0, 0.0, 0.0))
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


class Fake:
    def __init__(self, table):
        self.table, self.seen = table, []

    def __call__(self, sig):
        self.seen.append(sig)
        return self.table.get(sig)


class CandidateTests(unittest.TestCase):
    def test_earliest_buy_ignores_sells_other_pools_and_venues_and_ties_by_signature(self):
        rows = [
            row(POOL_A, "zzz", 105), row(POOL_A, "bbb", 100), row(POOL_A, "aaa", 100),
            row(POOL_A, "sell0", 90, side="sell"), row(POOL_B, "other", 80), row(POOL_A, "bond", 70, venue="pump_bonding"),
            row(POOL_A, "failed", 60, err={"x": 1}), {**row(POOL_A, "nosig", 50), "signature": None},
            row(POOL_A, "bbb", 100),  # duplicate print
        ]
        got = c.collect_candidates(rows, [POOL_A, POOL_B], 5)
        self.assertEqual(got[POOL_A], [(100, "aaa"), (100, "bbb"), (105, "zzz")])
        self.assertEqual(got[POOL_B], [(80, "other")])
        self.assertEqual(c.collect_candidates(rows, [POOL_A], 2)[POOL_A], [(100, "aaa"), (100, "bbb")])

    def test_earliest_is_the_one_fetched(self):
        rows = [row(POOL_A, "late", 200), row(POOL_A, "early", 100)]
        f = Fake({"early": tx_for(POOL_A), "late": tx_for(POOL_A)})
        out = c.build([POOL_A], rows, f, 5)
        self.assertEqual((out[0]["sig"], out[0]["slot"], f.seen), ("early", 100, ["early"]))


class ResolveTests(unittest.TestCase):
    def test_value_matches_implied_virtual(self):
        ev = dec.find_buy_event(tx_for(POOL_A)["meta"]["logMessages"])
        r = c.resolve_pool(POOL_A, [(1, "s")], Fake({"s": tx_for(POOL_A)}), 5)
        self.assertEqual(r["v_implied"], round(pvh.implied_virtual(ev)))
        self.assertEqual(r["quote_reserve"], 10 * SOL)
        self.assertIsInstance(r["v_implied"], int)

    def test_fallback_to_next_print_on_decode_reasons_only(self):
        f = Fake({"a": tx_for(POOL_B), "b": tx_for(POOL_A)})  # "a" decodes to another pool
        r = c.resolve_pool(POOL_A, [(1, "a"), (2, "b")], f, 5)
        self.assertEqual((r["sig"], r["slot"], f.seen), ("b", 2, ["a", "b"]))

    def test_rpc_failure_retries_same_signature_then_stops_without_fallback(self):
        calls, naps = [], []

        def flaky(sig):
            calls.append(sig)
            return tx_for(POOL_A) if len(calls) == 3 else None  # fails twice, then reads

        r = c.resolve_pool(POOL_A, [(1, "a"), (2, "b")], flaky, 5, naps.append)
        self.assertEqual((r["sig"], calls, naps), ("a", ["a", "a", "a"], [0.0, 0.0]))
        f = Fake({"b": tx_for(POOL_A)})  # "a" never reads
        naps.clear()
        r = c.resolve_pool(POOL_A, [(1, "a"), (2, "b")], f, 5, naps.append)
        self.assertEqual((r["v_implied"], r["reason"], f.seen, naps), (None, "rpc_error", ["a"] * 4, [0.0, 0.0, 0.0]))

    def test_dust_is_skipped_and_the_next_print_tried(self):
        self.assertEqual(c.decode_sample(tx_for(POOL_A, out=999_999), POOL_A)[1], "dust")
        self.assertIsNotNone(c.decode_sample(tx_for(POOL_A, out=1_000_000), POOL_A)[0])
        tiny = tx_for(POOL_A, net_extra=-(SOL // 10 - 4_999_999))  # net 4,999,999 lamports
        self.assertEqual(c.decode_sample(tiny, POOL_A)[1], "dust")
        ok = tx_for(POOL_A, net_extra=-(SOL // 10 - 5_000_000))
        self.assertIsNotNone(c.decode_sample(ok, POOL_A)[0])
        f = Fake({"a": tiny, "b": tx_for(POOL_A)})
        self.assertEqual(c.resolve_pool(POOL_A, [(1, "a"), (2, "b")], f, 5)["sig"], "b")

    def test_null_with_reason_and_try_cap(self):
        f = Fake({s: tx_for(POOL_B) for s in (f"s{i}" for i in range(9))})
        r = c.resolve_pool(POOL_A, [(i, f"s{i}") for i in range(9)], f, 3)
        self.assertIsNone(r["v_implied"])
        self.assertEqual((r["reason"], len(f.seen)), ("event_pool_mismatch", 3))
        self.assertEqual(c.resolve_pool(POOL_A, [], f, 3)["reason"], "no_buy_print_on_tape")

    def test_event_pool_mismatch_is_not_used(self):
        r = c.resolve_pool(POOL_A, [(1, "s")], Fake({"s": tx_for(POOL_B)}), 5)
        self.assertEqual((r["v_implied"], r["reason"]), (None, "event_pool_mismatch"))

    def test_zero_base_out_failed_tx_and_no_event(self):
        self.assertEqual(c.decode_sample(tx_for(POOL_A, out=0), POOL_A)[1], "event_base_out_zero")
        bad = tx_for(POOL_A)
        bad["meta"]["err"] = {"InstructionError": [0, "x"]}
        self.assertEqual(c.decode_sample(bad, POOL_A)[1], "tx_failed")
        self.assertEqual(c.decode_sample({"meta": {"err": None, "logMessages": []}}, POOL_A)[1], "event_missing")


class OutputTests(unittest.TestCase):
    def test_output_feeds_check_v_constancy(self):
        pools = [str(Pubkey.new_unique()) for _ in range(210)]
        rows = [row(p, f"sig-{p}", 10) for p in pools]
        out = c.build(pools[:200] + [pools[200]], rows, Fake({f"sig-{p}": tx_for(p) for p in pools[:200]}), 5)
        self.assertEqual(sum(1 for r in out if r["v_implied"] is None), 1)
        self.assertEqual(set(out[0]), {"pool", "v_implied", "quote_reserve", "sig", "slot", "reason"})
        v = round(pvh.implied_virtual(dec.find_buy_event(tx_for(pools[0])["meta"]["logMessages"])))
        rec = x.check_v_constancy([r for r in out if r["v_implied"] is not None], {p: v for p in pools})
        self.assertEqual((rec["n_checked"], rec["n_disagree"]), (200, 0))
        # null rows carry no v_implied, so they are filtered here as the screen only counts readable ones
        self.assertEqual(rec["n_sample"], 200)


class ReserveTests(unittest.TestCase):
    def test_nulls_replaced_from_reserve_in_order(self):
        prim = [str(Pubkey.new_unique()) for _ in range(4)]
        res = [str(Pubkey.new_unique()) for _ in range(3)]
        rows = [row(p, f"sig-{p}", 1) for p in prim + res]
        table = {f"sig-{p}": tx_for(p) for p in prim + res}
        del table[f"sig-{prim[0]}"], table[f"sig-{prim[2]}"]  # two primary pools unreadable
        out = c.build(prim, rows, Fake(table), 5, res, lambda s: None)
        self.assertEqual([r["pool"] for r in out], sorted(prim) + res[:2])
        self.assertEqual(sum(1 for r in out if r["v_implied"] is None), 2)
        self.assertTrue(all(r["v_implied"] is not None for r in out[-2:]))
        none = c.build(prim, rows, Fake({f"sig-{p}": tx_for(p) for p in prim}), 5, res, lambda s: None)
        self.assertEqual(len(none), 4)

    def test_build_walks_past_a_null_reserve_row(self):
        prim = [str(Pubkey.new_unique()) for _ in range(2)]
        res = [str(Pubkey.new_unique()) for _ in range(5)]
        rows = [row(p, f"sig-{p}", 1) for p in prim + res]
        table = {f"sig-{p}": tx_for(p) for p in prim + res}
        del table[f"sig-{prim[0]}"], table[f"sig-{res[0]}"]  # one primary null, then the first reserve is null too
        out = c.build(prim, rows, Fake(table), 5, res, lambda s: None)
        self.assertEqual([r["pool"] for r in out], sorted(prim) + res[:2])

    def test_extend_keeps_old_rows_and_fetches_only_missing_reserve(self):
        prim = [str(Pubkey.new_unique()) for _ in range(2)]
        res = [str(Pubkey.new_unique()) for _ in range(5)]
        rows = [row(p, f"sig-{p}", 1) for p in prim + res]
        table = {f"sig-{p}": tx_for(p) for p in prim + res}
        del table[f"sig-{prim[0]}"], table[f"sig-{res[0]}"], table[f"sig-{res[1]}"]
        old = c.build(prim, rows, Fake({k: v for k, v in table.items() if k != f"sig-{res[1]}"}), 5, res, lambda s: None)  # an old walk that stopped one reserve short
        old = old[:len(prim) + 1]  # primary + res[0] (null): needs res[1] (null) and res[2]
        f = Fake(table)
        out = c.extend(prim, res, old, rows, f, 5, lambda s: None)
        self.assertEqual(out[:len(old)], old)
        self.assertEqual([r["pool"] for r in out[len(old):]], res[1:3])
        self.assertNotIn(f"sig-{res[0]}", getattr(f, "seen", []))
        # nothing missing: unchanged and no fetch
        self.assertEqual(c.extend(prim, res, out, rows, Fake({}), 5, lambda s: None), out)
        with self.assertRaises(x.Refused):
            c.extend(prim, res, list(reversed(old)), rows, f, 5, lambda s: None)

    def test_load_sample_reads_both_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp, "a.json"), Path(tmp, "b.json")
            a.write_text(json.dumps(["x", "y"]))
            b.write_text(json.dumps({"primary": ["x"], "reserve": ["y"]}))
            self.assertEqual(c.load_sample(a), (["x", "y"], []))
            self.assertEqual(c.load_sample(b), (["x"], ["y"]))
            a.write_text(json.dumps({"primary": "x"}))
            with self.assertRaises(x.Refused):
                c.load_sample(a)

    def test_v_counts(self):
        out = [{"pool": "a", "v_implied": 1}, {"pool": "b", "v_implied": 2}, {"pool": "c", "v_implied": None}, {"pool": "d", "v_implied": 3}]
        self.assertEqual(c.v_counts(out, {"a": 17, "b": 0, "c": 5, "d": None}), {"n_checked": 2, "n_checked_v_gt0": 1, "n_checked_v_le0": 1})
        self.assertEqual(c.v_counts(out, None), {})


class ConstancyNullTests(unittest.TestCase):
    def rows(self, n=200, nulls=0):
        r = [{"pool": f"p{i}", "v_implied": 17_584_000_000, "quote_reserve": 10 * SOL, "sig": "s", "slot": 1, "reason": ""} for i in range(n)]
        for q in r[:nulls]:
            q.update(v_implied=None, quote_reserve=None, sig=None, slot=None, reason="tx_missing")
        return r

    def test_nulls_never_raise_and_one_null_refuses_on_the_floor(self):
        vmap = {f"p{i}": 17_584_000_000 for i in range(200)}
        self.assertEqual(x.check_v_constancy(self.rows(), vmap)["n_unreadable_implied"], 0)
        with self.assertRaises(x.Refused) as cm:
            x.check_v_constancy(self.rows(nulls=1), vmap)
        self.assertIn("only 199", str(cm.exception))
        self.assertIn("1 unreadable implied", str(cm.exception))
        rec = x.check_v_constancy(self.rows(201, nulls=1), {**vmap, "p200": 17_584_000_000})  # a larger file with a null still passes the floor
        self.assertEqual((rec["n_checked"], rec["n_unreadable_implied"], rec["n_sample"]), (200, 1, 201))
        x.check_v_constancy([{"pool": "p0"}] + self.rows(201)[1:], {**vmap, "p200": 1})  # a missing v_implied key is not indexed

    def test_full_file_with_nulls_still_matches_the_sample(self):
        pools = [f"p{i}" for i in range(200)]
        x.check_constancy_sample(self.rows(nulls=3), pools)


class MainTests(unittest.TestCase):
    def run_main(self, tmp, view_dir, fetch, extra=()):
        sample = Path(tmp) / "sample.json"
        sample.write_text(json.dumps([POOL_A, POOL_B]))
        out = Path(tmp) / "out.json"
        buf, err = io.StringIO(), io.StringIO()
        g2 = {"roots": {}, "pool": []}
        rows = [row(POOL_A, "s1", 5), row(POOL_B, "s2", 6)]
        with mock.patch.object(c, "guard_views", return_value=g2) if view_dir is None else contextlib.nullcontext(), \
                mock.patch.object(c, "iter_view_rows", return_value=rows), contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = c.main(["--sample", str(sample), "--p2-view-dir", str(view_dir or "/x/view"), "--out", str(out), *extra], fetch=fetch)
        return rc, buf.getvalue(), err.getvalue(), out

    def test_writes_out_meta_and_prints_counts_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Fake({"s1": tx_for(POOL_A)})
            f.calls = 0
            rc, so, se, out = self.run_main(tmp, None, f)
            self.assertEqual(rc, 0)
            rows = json.loads(out.read_text())
            self.assertEqual([r["pool"] for r in rows], sorted([POOL_A, POOL_B]))
            meta = json.loads(Path(str(out) + ".meta.json").read_text())
            import hashlib
            self.assertEqual(meta["out_sha256"], hashlib.sha256(out.read_bytes()).hexdigest())
            self.assertEqual((meta["n"], meta["n_null"]), (2, 1))
            self.assertEqual(meta["null_reasons"], {"rpc_error": 1})
            self.assertIn("git_head", meta)
            self.assertIn("sample_sha256", meta)
            self.assertEqual(so.strip(), "pools=2 readable=1 null=1 rpc_calls=0")
            for p in (POOL_A, POOL_B):
                self.assertNotIn(p, so + se)

    def test_extend_writes_a_new_file_with_meta_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "old.json"
            sample = Path(tmp) / "sample.json"
            sample.write_text(json.dumps({"primary": [POOL_A, POOL_B], "reserve": [POOL_C]}))
            oldrows = [{"pool": p, "v_implied": None, "quote_reserve": None, "sig": None, "slot": None, "reason": "dust"} for p in sorted([POOL_A, POOL_B])]
            old.write_text(json.dumps(oldrows))
            out = Path(tmp) / "new.json"
            rows = [row(POOL_C, "s3", 7)]
            args = ["--sample", str(sample), "--p2-view-dir", "/x/view", "--out", str(out), "--extend", str(old)]
            with mock.patch.object(c, "guard_views", return_value={"roots": {}, "pool": []}), mock.patch.object(c, "iter_view_rows", return_value=rows), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(c.main(args, fetch=Fake({"s3": tx_for(POOL_C)})), 0)
                new = json.loads(out.read_text())
                self.assertEqual(new[:2], oldrows)
                self.assertEqual(new[2]["pool"], POOL_C)
                meta = json.loads(Path(str(out) + ".meta.json").read_text())
                import hashlib
                self.assertEqual(meta["extended_from_sha256"], hashlib.sha256(old.read_bytes()).hexdigest())
                self.assertEqual(c.main(args, fetch=Fake({})), 2)  # --out exists: refused

    def test_rps_above_five_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Fake({})
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = c.main(["--sample", "s", "--p2-view-dir", "/x", "--out", str(Path(tmp) / "o"), "--rps", "5.5"], fetch=f)
            self.assertEqual(rc, 2)
            self.assertIn("refusing", err.getvalue())
            rc2, *_ = self.run_main(tmp, None, Fake({}), ("--rps", "5"))
            self.assertEqual(rc2, 0)

    def test_url_never_printed(self):
        secret = "SECRETKEY123"
        url = f"https://mainnet.helius-rpc.com/?api-key={secret}"
        h = c.HttpFetch(url, 1000.0)
        with mock.patch("urllib.request.urlopen", side_effect=OSError(f"boom {url}")):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()) as so:
                self.assertIsNone(h("sig"))
        self.assertNotIn(secret, err.getvalue() + so.getvalue())
        self.assertEqual(h.calls, 1)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(c.sim, "load_rpc_url", return_value=url):
                _rc, so2, se2, _o = self.run_main(tmp, None, Fake({}))
            self.assertNotIn(secret, so2 + se2)

    def test_reserved_view_dir_refused_before_any_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Fake({})
            with mock.patch.object(c, "iter_view_rows") as it:
                for bad in ("/data/mal/explore-0814/fresh-0802/view", "/data/mal/raw/view"):
                    sample = Path(tmp) / "s.json"
                    sample.write_text("[]")
                    err = io.StringIO()
                    with contextlib.redirect_stderr(err):
                        rc = c.main(["--sample", str(sample), "--p2-view-dir", bad, "--out", str(Path(tmp) / "o.json")], fetch=f)
                    self.assertEqual(rc, 2, bad)
                    self.assertIn("refusing", err.getvalue())
                it.assert_not_called()
            self.assertEqual(f.seen, [])
            self.assertFalse((Path(tmp) / "o.json").exists())


if __name__ == "__main__":
    unittest.main()
