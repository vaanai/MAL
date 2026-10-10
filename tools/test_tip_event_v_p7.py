"""Tests for tools/tip_event_v_p7.py: the outcome-blind P7 line-1 check on the tip follower's event-V stamp.

Offline. Tape rows and getTransaction results are built from the repo's public decoder fixtures (tools/fixtures/walk2_event_v) and the real
decoder (observe.trade_decode, event_v=True); the RPC call is a stub or a patched urlopen. Nothing here opens /var/lib/mal, a host, a tape or a
sealed file, and no network call is made. unittest style; pytest runs it too.
"""

from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import io
import json
import random
import shutil
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import observe.trade_decode as td
from observe.trade_decode import records_from_logs
from tools import fast_tip_follower as ftf
from tools import pump_history_backfill as phb
from tools import tip_event_v_p7 as p7

try:  # the follower's own fixtures are pytest-style; only the key-evidence test needs them
    from tools import test_fast_tip_follower_event_v as follower_fixtures
except ImportError:  # pragma: no cover
    follower_fixtures = None

REPO = Path(__file__).resolve().parent.parent
FIX = REPO / "tools" / "fixtures" / "walk2_event_v"
START = "2026-10-10T13:17:18Z"
END = "2026-10-10T14:30:00Z"
T0 = p7.parse_utc(START)
FAKE_KEY = "TESTKEY0123456789abcdefXYZ"
RESERVE_KEYS = ("quote_reserve", "base_reserve", "token_raw", "sol_lamports", "virtual_quote_reserves")

SELL = "sell_v2_kept.json"                 # a sell on the canonical pool of create_pool_init_boost.json
BUY = "buy_v1_481_wsol.json"               # ix_name "buy": a comparable buy
EXACT_IN = "buy_exact_quote_in_496.json"   # ix_name "buy_exact_quote_in": not comparable
AMEND_SHA256 = "ed3005f083d80bba768292a8ff6adf4b4220370e01760a540034c6d1bcace31b"  # ARTIFACTS/exp025/p7_buy_amend.py (blob 24dc5ede...)


def fixture(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def first_record(doc: dict) -> dict:
    recs = records_from_logs(doc["meta"]["logMessages"], slot=doc["slot"], signature=doc["signature"], t_recv_ms=0, commitment="confirmed",
                             feed="t", event_v=True)
    return recs[0]


def scaled(name: str, num: int = 103, den: int = 100) -> dict:
    """The fixture with the trade event's V rewritten to V * num // den in its Program data. The tape row decoded from it and the chain
    result decoded from it agree on every field (identity holds), but the integer law no longer does: V is off by about 3%."""
    doc = copy.deepcopy(fixture(name))
    v = first_record(doc)["virtual_quote_reserves"]
    old = v.to_bytes(16, "little", signed=True)
    new = (v * num // den).to_bytes(16, "little", signed=True)
    hit = 0
    for i, line in enumerate(doc["meta"]["logMessages"]):
        if "Program data: " not in line:
            continue
        raw = base64.b64decode(line.split("Program data: ", 1)[1].strip())
        if raw[:8] in (td._BUY_DISC, td._SELL_DISC) and raw.count(old) == 1:
            doc["meta"]["logMessages"][i] = "Program data: " + base64.b64encode(raw.replace(old, new, 1)).decode()
            hit += 1
    assert hit == 1, (name, hit)
    return doc


def tape_of(doc: dict, *, mint: str, t_recv_ms: int = T0 + 1000, signature: str | None = None, tx_index: int | None = 3, rec_index: int = 0,
            **over) -> dict:
    """What the follower writes for that print: backfill_trade_row (stored_trade + source) plus the tip's t_recv_ms and source."""
    recs = records_from_logs(doc["meta"]["logMessages"], slot=doc["slot"], signature=signature or doc["signature"], t_recv_ms=0,
                             commitment="confirmed", feed="helius_getblock_tip", event_v=True)
    rec = dict(recs[rec_index], mint=mint, quote_mint=td.WSOL_MINT, quote_is_wsol=True)
    row = phb.backfill_trade_row(rec, doc["blockTime"])
    row["source"], row["t_recv_ms"] = "tip", t_recv_ms
    if tx_index is not None:
        row["tx_index"] = tx_index
    row.update(over)
    return row


def tx_of(doc: dict, **over) -> dict:
    """A getTransaction result: the fixture's slot, meta and signature."""
    tx = {"slot": doc["slot"], "blockTime": doc["blockTime"], "meta": copy.deepcopy(doc["meta"]), "transaction": {"signatures": [doc["signature"]]}}
    tx.update(over)
    return tx


def write_hour(dirpath: Path, hour: str, rows: list, extra_lines: tuple = ()) -> None:
    lines = [json.dumps(r, separators=(",", ":")) for r in rows] + list(extra_lines)
    (dirpath / f"trades-{hour}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def create_pool_pair() -> tuple[str, str]:
    """(pool, base mint) of the CreatePool event in a public fixture: a canonical pump.fun pool."""
    doc = fixture("create_pool_init_boost.json")
    for line in doc["meta"]["logMessages"]:
        if "Program data: " in line:
            ev = td.decode_program_data(base64.b64decode(line.split("Program data: ", 1)[1].strip()))
            if ev and ev.get("kind") == "create_pool":
                return ev["pool"], ev["base_mint"]
    raise AssertionError("no CreatePool event in the fixture")


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.trades = self.tmp / "trades"
        self.trades.mkdir()
        self.sell_pool, self.sell_mint = create_pool_pair()
        self.sell_doc, self.buy_doc = fixture(SELL), fixture(BUY)
        self.assertEqual(first_record(self.sell_doc)["pool"], self.sell_pool)
        self.buy_pool = first_record(self.buy_doc)["pool"]
        self.exact_pool = first_record(fixture(EXACT_IN))["pool"]
        self.buy_mint = "BuyMint1111111111111111111111111111111111111"
        self.exact_mint = "ExactMint11111111111111111111111111111111111"
        # the mint -> canonical pool table the stub canonical function reads; the real PDA function has its own test
        self.table = {self.sell_mint: self.sell_pool, self.buy_mint: self.buy_pool, self.exact_mint: self.exact_pool}
        self.canon = self.table.get

    def frame_of(self, rows: list, hour: str = "2026-10-10T13") -> tuple[list, dict]:
        write_hour(self.trades, hour, rows)
        return p7.build_frame(self.trades, T0, p7.parse_utc(END), self.canon)

    def close_window(self) -> None:
        """A row after --end in the next hour's file: the follower's t_recv_ms is non-decreasing, so this proves the window is on disk."""
        write_hour(self.trades, "2026-10-10T14", [self.sell_tape(signature="After" + "1" * 83, t_recv_ms=p7.parse_utc(END) + 5000)])

    def sell_tape(self, **kw) -> dict:
        return tape_of(self.sell_doc, mint=self.sell_mint, **kw)

    def buy_tape(self, **kw) -> dict:
        return tape_of(self.buy_doc, mint=self.buy_mint, **kw)

    def exact_tape(self, **kw) -> dict:
        return tape_of(fixture(EXACT_IN), mint=self.exact_mint, **kw)


# ---------------------------------------------------------------------------------------------------------------------------------------
class Pins(unittest.TestCase):
    def test_decoder_blob_pin_is_the_followers_and_the_tree_carries_it(self):
        self.assertEqual(p7.DECODER_BLOB, ftf.PINNED_DECODER_BLOBS["observe/trade_decode.py"])
        self.assertEqual(p7.DECODER_BLOB, "238942a6b3c5425389eddfde4d11268c300acbec")
        self.assertEqual(p7.decoder_blob(), p7.DECODER_BLOB)

    def test_getTransaction_settings_are_the_walkers_getBlock_settings(self):
        walker = phb._getblock_params(1, full=True)[1]
        for k in ("encoding", "commitment", "maxSupportedTransactionVersion"):
            self.assertEqual(p7.TX_CONFIG[k], walker[k], k)
        self.assertEqual(p7.TX_CONFIG["maxSupportedTransactionVersion"], 1)
        self.assertEqual(set(p7.TX_CONFIG), {"encoding", "commitment", "maxSupportedTransactionVersion"})

    def test_event_v_map_is_the_sha_pinned_file(self):
        ev = p7.event_v_map()
        sums = (REPO / "ARTIFACTS" / "exp025" / "SHA256SUMS").read_text()
        self.assertIn(f"{ev.SHA256}  event_v_map.py", sums)
        self.assertEqual(ev.P7_RAW_TX_ATTEMPTS, 3)

    def test_a_changed_event_v_map_is_refused(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        art = root / "ARTIFACTS" / "exp025"
        art.mkdir(parents=True)
        for f in ("event_v_map.py", "SHA256SUMS"):
            shutil.copy(REPO / "ARTIFACTS" / "exp025" / f, art / f)
        with mock.patch.object(p7, "REPO", root), mock.patch.object(p7, "_EV", None):
            p7.event_v_map()  # untouched copy loads
        with open(art / "event_v_map.py", "a") as fh:
            fh.write("\n# edited\n")
        with mock.patch.object(p7, "REPO", root), mock.patch.object(p7, "_EV", None):
            with self.assertRaises(p7.Refused):
                p7.event_v_map()

    def test_canonical_pool_is_the_pda_of_a_real_pair(self):
        pool, mint = create_pool_pair()
        canon = p7.default_canonical()
        self.assertEqual(canon(mint), pool)
        self.assertNotEqual(canon("So11111111111111111111111111111111111111112"), pool)
        try:
            from solders.pubkey import Pubkey

            from tools import pumpswap_tx
        except ImportError:
            return
        self.assertEqual(canon(mint), str(pumpswap_tx.canonical_pool(Pubkey.from_string(mint))))


# ---------------------------------------------------------------------------------------------------------------------------------------
class FrameTests(Base):
    def test_unstamped_noncanonical_and_bonding_rows_are_out_and_unstamped_canonical_are_counted(self):
        sell = self.sell_tape(t_recv_ms=T0 + 1000)
        buy = self.buy_tape(t_recv_ms=T0 + 2000, signature="BuySig" + "1" * 80)
        unstamped = self.sell_tape(t_recv_ms=T0 + 3000, signature="Unstamped" + "1" * 79)
        for k in td.EVENT_V_KEYS:
            unstamped.pop(k, None)
        foreign = self.sell_tape(t_recv_ms=T0 + 4000, signature="Foreign" + "1" * 81, pool="SomeOtherPool11111111111111111111111111111")
        bonding = {"venue": "pump_bonding", "note": "pumpswap", "mint": self.sell_mint, "pool": self.sell_pool, "side": "buy", "slot": 5,
                   "signature": "Bonding" + "1" * 81, "event_index": 0, "t_recv_ms": T0 + 5000, "ix_name": "buy", "virtual_quote_reserves": 7}
        before = self.sell_tape(t_recv_ms=T0 - 1, signature="Before" + "1" * 82)
        at_end = self.sell_tape(t_recv_ms=p7.parse_utc(END), signature="AtEnd" + "1" * 83)
        no_mint = dict(self.sell_tape(t_recv_ms=T0 + 6000, signature="NoMint" + "1" * 82), mint=None)
        bool_v = self.sell_tape(t_recv_ms=T0 + 7000, signature="BoolV" + "1" * 83, virtual_quote_reserves=True)
        write_hour(self.trades, "2026-10-10T13", [sell, buy, unstamped, foreign, bonding, before, no_mint, bool_v],
                   extra_lines=('{"venue":"pumpswap","slot":',))  # the partial last line of an hour still being written
        write_hour(self.trades, "2026-10-10T14", [at_end])
        frame, info = p7.build_frame(self.trades, T0, p7.parse_utc(END), self.canon)
        self.assertEqual({r["signature"][:6] for r in frame}, {sell["signature"][:6], "BuySig"})
        self.assertEqual(info["frame_n"], 2)
        self.assertEqual(info["unstamped_canonical_n"], 2)  # the stripped row and the bool "V"
        self.assertEqual(info["non_canonical"], 1)
        self.assertEqual(info["no_mint_or_pool"], 1)
        self.assertEqual(info["bad_lines"], 1)
        self.assertEqual(info["files_read"], 2)
        self.assertEqual(info["canonical_pumpswap_n"], 4)  # sell, buy, stripped, bool V; `before` and `at_end` are outside [start, end)
        self.assertEqual(info["hours_missing"], [])
        self.assertIs(info["window_closed"], True)  # `at_end` has t_recv_ms == end

    def test_missing_hour_is_reported_not_hidden(self):
        write_hour(self.trades, "2026-10-10T13", [self.sell_tape()])
        _, info = p7.build_frame(self.trades, T0, p7.parse_utc(END), self.canon)
        self.assertEqual(info["hours_missing"], ["2026-10-10T14"])
        self.assertIs(info["window_closed"], False)

    def test_sorted_by_slot_signature_event_index_and_the_tx_index_field_is_the_signature(self):
        rows = [
            self.sell_tape(signature="Bbb" + "1" * 85, slot=20, tx_index=None),
            self.sell_tape(signature="Aaa" + "1" * 85, slot=20, tx_index=9),
            self.sell_tape(signature="Zzz" + "1" * 85, slot=10, tx_index=0),
            self.sell_tape(signature="Aaa" + "1" * 85, slot=20, tx_index=9, event_index=1),
        ]
        random.Random(1).shuffle(rows)
        frame, info = self.frame_of(rows)
        self.assertEqual([(r["slot"], r["signature"][:3], r["event_index"]) for r in frame],
                         [(10, "Zzz", 0), (20, "Aaa", 0), (20, "Aaa", 1), (20, "Bbb", 0)])
        self.assertTrue(all(r["tx_index"] == r["signature"] for r in frame))
        self.assertEqual(info["frame_with_tx_index_n"], 3)  # one row carries no tx_index; the key never needs it

    def test_duplicate_key_keeps_the_first_row_and_is_counted(self):
        a = self.sell_tape()
        b = dict(a, sol_lamports=a["sol_lamports"] + 1)
        frame, info = self.frame_of([a, b])
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame[0]["sol_lamports"], a["sol_lamports"])
        self.assertEqual(info["duplicate_keys_dropped"], 1)

    def test_row_ceiling_refuses_while_reading_before_the_sort_and_exactly_the_ceiling_passes(self):
        rows = [self.sell_tape(signature=f"C{i:03d}" + "1" * 80, t_recv_ms=T0 + 1000 + i) for i in range(10)]
        write_hour(self.trades, "2026-10-10T13", rows)
        parsed, real = [], json.loads

        def spy(raw, *a, **k):
            parsed.append(1)
            return real(raw, *a, **k)

        with mock.patch.object(p7.json, "loads", spy):
            with self.assertRaises(p7.Refused) as cm:
                p7.build_frame(self.trades, T0, p7.parse_utc(END), self.canon, max_rows=3)
        self.assertEqual(len(parsed), 4)  # stopped at the row that passed the ceiling, not after reading all 10
        self.assertIn("--max-frame-rows", str(cm.exception))
        frame, _ = p7.build_frame(self.trades, T0, p7.parse_utc(END), self.canon, max_rows=10)
        self.assertEqual(len(frame), 10)
        self.assertEqual(len(p7.build_frame(self.trades, T0, p7.parse_utc(END), self.canon)[0]), 10)  # None: no ceiling

    def test_default_ceiling_keeps_the_frame_near_1_1_gb(self):
        self.assertEqual(p7.DEFAULT_MAX_FRAME_ROWS, 1_200_000)
        self.assertLess(p7.DEFAULT_MAX_FRAME_ROWS * 923, 1.2e9)  # ~923 B per frame row (reviewer's measurement on #570)
        self.assertEqual(p7.build_parser().parse_args(["--start", START, "--end", END]).max_frame_rows, p7.DEFAULT_MAX_FRAME_ROWS)

    def test_frame_rows_keep_only_what_the_check_reads(self):
        (row,), _ = self.frame_of([self.sell_tape()])
        self.assertEqual(set(row), (set(p7._FRAME_FIELDS) - {"ix_name", "zero_sol"}) | {"tx_index"})
        for k in ("mint", "trader", "price_sol", "event_ts"):
            self.assertNotIn(k, row)

    @unittest.skipIf(follower_fixtures is None, "pytest not importable")
    def test_the_followers_own_output_has_rows_without_tx_index_so_the_key_is_the_signature(self):
        """Why the key is (slot, signature, event_index): on the follower's own fixtures, the rows that came back through
        resolve_unresolved carry no tx_index and the others do. The event_v_map helpers still run on every one of them."""
        out = self.tmp / "follower"
        follower_fixtures._run(ftf, out, trade_event_v=True)
        rows = follower_fixtures._trades(follower_fixtures._streams(out))
        swaps = [r for r in rows if r.get("venue") == "pumpswap"]
        with_tx = [r for r in swaps if "tx_index" in r]
        self.assertTrue(with_tx and len(with_tx) < len(swaps), (len(with_tx), len(swaps)))
        pool_of = {r["mint"]: r["pool"] for r in swaps}
        frame, info = p7.build_frame(out / "out", p7.parse_utc("2026-09-25T00:00:00Z"), p7.parse_utc("2026-09-26T00:00:00Z"), pool_of.get)
        self.assertEqual(info["frame_n"], len(swaps))
        self.assertEqual(info["frame_with_tx_index_n"], len(with_tx))
        self.assertEqual(info["unstamped_canonical_n"], 0)
        keys = [(r["slot"], r["signature"], r["event_index"]) for r in frame]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(len(set(keys)), len(keys))
        ev = p7.event_v_map()
        main = ev.p7_raw_main_draw(frame, 5)
        ev.p7_raw_buy_topup(frame, main, 3)  # reads row["tx_index"] on every row: the signature stands in


# ---------------------------------------------------------------------------------------------------------------------------------------
class DrawTests(Base):
    def _rows(self, n: int, buys: set) -> list:
        """n canonical stamped prints with distinct keys; the indices in `buys` are comparable buys, the rest sells."""
        rows = []
        for i in range(n):
            base = self.buy_tape if i in buys else self.sell_tape
            rows.append(base(signature=f"S{i:06d}" + "1" * 80, slot=1000 + i // 3, tx_index=None, t_recv_ms=T0 + 1000 + i))
        return rows

    def test_draw_is_deterministic_whatever_the_file_order_and_is_the_stride_formula(self):
        rows = self._rows(2503, set(range(0, 2503, 7)))
        out = []
        for seed in (1, 2):
            shuffled = rows[:]
            random.Random(seed).shuffle(shuffled)
            frame, _ = self.frame_of(shuffled)
            main, topup = p7.draw(frame)
            out.append(([(r["slot"], r["signature"], r["event_index"]) for r in main], [r["signature"] for r in topup]))
        self.assertEqual(out[0], out[1])
        frame, _ = self.frame_of(rows)
        main, _ = p7.draw(frame)
        self.assertEqual(len(main), 1000)
        self.assertEqual([r["signature"] for r in main], [frame[(k * 2503) // 1000]["signature"] for k in range(1000)])

    def test_small_frame_is_drawn_whole(self):
        frame, _ = self.frame_of(self._rows(40, set()))
        main, topup = p7.draw(frame)
        self.assertEqual((len(main), len(topup)), (40, 0))

    def test_buy_topup_brings_comparable_buys_to_100_from_the_frame_and_is_disjoint(self):
        ev = p7.event_v_map()
        frame, _ = self.frame_of(self._rows(2000, set(range(1, 2000, 10))))  # 200 comparable buys in all; the main draw takes about half
        main, topup = p7.draw(frame)
        have = sum(1 for r in main if ev.p7_raw_line(r) == "buy")
        self.assertLess(have, 100)
        self.assertEqual(len(topup), 100 - have)
        self.assertTrue(all(ev.p7_raw_line(r) == "buy" for r in topup))
        self.assertFalse({r["signature"] for r in main} & {r["signature"] for r in topup})

    def test_no_topup_when_the_main_draw_already_holds_100_comparable_buys(self):
        frame, _ = self.frame_of(self._rows(2000, set(range(0, 2000, 2))))
        main, topup = p7.draw(frame)
        self.assertEqual(topup, [])

    def test_the_draw_runs_before_any_fetch(self):
        write_hour(self.trades, "2026-10-10T13", self._rows(50, {1, 2, 3}))
        self.close_window()
        order = []
        real_draw = p7.draw

        def spy_draw(frame):
            order.append("draw")
            return real_draw(frame)

        def call(sig):
            order.append("fetch")
            raise p7.FetchError("http_500")

        argv = ["--trades-dir", str(self.trades), "--start", START, "--end", END, "--out-dir", str(self.tmp / "out")]
        with mock.patch.object(p7, "draw", spy_draw), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = p7.main(argv, _call=call, _canonical=self.canon, _sleep=lambda s: None)
        self.assertEqual(rc, 0)
        self.assertEqual(order[0], "draw")
        self.assertEqual(order.count("draw"), 1)
        self.assertGreater(order.count("fetch"), 0)


# ---------------------------------------------------------------------------------------------------------------------------------------
class JudgeTests(Base):
    def one(self, tape: dict, tx, edit: dict | None = None) -> tuple:
        """One sampled print through run_check against a stub fetch. Returns ((line, outcome, reason), number of fetches)."""
        calls = []

        def fetch(sig):
            calls.append(sig)
            return tx

        (frame_row,), _ = self.frame_of([tape])
        frame_row.update(edit or {})
        (result,), _ = p7.run_check([("main", frame_row)], fetch)
        return result, len(calls)

    def test_correct_v_hits_on_a_real_sell_and_a_real_buy(self):
        self.assertEqual(self.one(self.sell_tape(), tx_of(self.sell_doc)), (("sell", "hit", None), 1))
        self.assertEqual(self.one(self.buy_tape(), tx_of(self.buy_doc)), (("buy", "hit", None), 1))

    def test_v_off_by_three_percent_is_a_miss_on_both_lines(self):
        for name, mint, line in ((SELL, self.sell_mint, "sell"), (BUY, self.buy_mint, "buy")):
            doc = scaled(name)
            self.assertEqual(first_record(doc)["virtual_quote_reserves"], first_record(fixture(name))["virtual_quote_reserves"] * 103 // 100)
            self.assertEqual(self.one(tape_of(doc, mint=mint), tx_of(doc))[0], (line, "miss", None), name)

    def test_q_is_the_vault_plus_this_prints_own_v_and_v0_is_zero(self):
        tape = self.sell_tape()
        adapter = p7.adapter_row_from_tape(tape)
        self.assertEqual(adapter["quote_reserve"], tape["quote_reserve"] + tape["virtual_quote_reserves"])
        raw, ev = first_record(self.sell_doc), p7.event_v_map()
        self.assertTrue(ev.p7_raw_hit("sell", raw, q_mapped=tape["quote_reserve"] + tape["virtual_quote_reserves"], v0=0))
        self.assertFalse(ev.p7_raw_hit("sell", raw, q_mapped=tape["quote_reserve"], v0=0))  # the vault without V misses
        self.assertIsNone(p7.adapter_row_from_tape(dict(tape, virtual_quote_reserves=None))["quote_reserve"])
        # the same on a buy: with V it hits, the vault alone (no V) misses
        btape, braw = self.buy_tape(), first_record(self.buy_doc)
        self.assertTrue(ev.p7_raw_hit("buy", braw, q_mapped=btape["quote_reserve"] + btape["virtual_quote_reserves"], v0=0))
        self.assertFalse(ev.p7_raw_hit("buy", braw, q_mapped=btape["quote_reserve"], v0=0))
        # and under the amended inverse law, which is what the tool now runs for buys
        am = p7.buy_amend()
        self.assertTrue(am.p7_raw_hit("buy", braw, q_mapped=btape["quote_reserve"] + btape["virtual_quote_reserves"], v0=0))
        self.assertFalse(am.p7_raw_hit("buy", braw, q_mapped=btape["quote_reserve"], v0=0))

    def test_stamps_swapped_between_two_prints_with_different_v_are_identity_mismatches(self):
        sell, buy = self.sell_tape(), self.buy_tape()
        self.assertNotEqual(sell["virtual_quote_reserves"], buy["virtual_quote_reserves"])
        sell["virtual_quote_reserves"], buy["virtual_quote_reserves"] = buy["virtual_quote_reserves"], sell["virtual_quote_reserves"]
        frame, _ = self.frame_of([sell, buy])
        txs = {self.sell_doc["signature"]: tx_of(self.sell_doc), self.buy_doc["signature"]: tx_of(self.buy_doc)}
        results, _ = p7.run_check([("main", r) for r in frame], txs.get)
        self.assertEqual(sorted(results), sorted([("sell", "unresolved", "identity_mismatch"), ("buy", "unresolved", "identity_mismatch")]))
        # unswapped, the same two prints hit
        frame, _ = self.frame_of([self.sell_tape(), self.buy_tape()])
        results, _ = p7.run_check([("main", r) for r in frame], txs.get)
        self.assertEqual(sorted(results), sorted([("sell", "hit", None), ("buy", "hit", None)]))

    # -- unresolved: every reason, each a miss that stays in the denominator -------------------------------------------------------------
    def test_fetch_failed(self):
        self.assertEqual(self.one(self.sell_tape(), None)[0], ("sell", "unresolved", "fetch_failed"))

    def test_no_record_for_a_failed_transaction_an_empty_log_and_a_missing_event_index(self):
        failed = tx_of(self.sell_doc)
        failed["meta"]["err"] = {"InstructionError": [0, "Custom"]}
        empty = tx_of(self.sell_doc)
        empty["meta"]["logMessages"] = ["Program log: nothing"]
        no_logs = tx_of(self.sell_doc)
        del no_logs["meta"]["logMessages"]
        for tx in (failed, empty, no_logs, {"slot": self.sell_doc["slot"]}):
            self.assertEqual(self.one(self.sell_tape(), tx)[0], ("sell", "unresolved", "no_record"))
        # the tape says event_index 4, the chain has only index 0
        self.assertEqual(self.one(self.sell_tape(event_index=4), tx_of(self.sell_doc))[0], ("sell", "unresolved", "no_record"))

    def test_slot_mismatch(self):
        self.assertEqual(self.one(self.sell_tape(), tx_of(self.sell_doc, slot=self.sell_doc["slot"] + 1))[0], ("sell", "unresolved", "slot_mismatch"))
        self.assertEqual(self.one(self.sell_tape(), tx_of(self.sell_doc, slot=None))[0], ("sell", "unresolved", "slot_mismatch"))

    def test_identity_mismatch_when_the_tape_row_is_not_the_chain_event(self):
        # the follower stamped a V the chain event does not carry (about 3% off)
        tape = self.sell_tape()
        tape["virtual_quote_reserves"] = tape["virtual_quote_reserves"] * 103 // 100
        self.assertEqual(self.one(tape, tx_of(self.sell_doc))[0], ("sell", "unresolved", "identity_mismatch"))
        for field in ("token_raw", "base_reserve", "quote_reserve", "sol_lamports"):
            tape = self.sell_tape()
            res = self.one(tape, tx_of(self.sell_doc), edit={field: tape[field] + 1})[0]
            self.assertEqual(res, ("sell", "unresolved", "identity_mismatch"), field)
        res = self.one(self.sell_tape(), tx_of(self.sell_doc), edit={"pool": "SomeOtherPool11111111111111111111111111111"})[0]
        self.assertEqual(res, ("sell", "unresolved", "identity_mismatch"))
        # a zero_sol event on the chain is a different print from a comparable tape row
        zero = scaled(SELL, 100, 100)
        with mock.patch.object(p7, "records_from_logs", lambda *a, **k: [dict(r, zero_sol=True) for r in records_from_logs(*a, **k)]):
            self.assertEqual(self.one(self.sell_tape(), tx_of(zero))[0], ("sell", "unresolved", "identity_mismatch"))

    def test_field_missing_when_the_raw_event_lacks_the_gross_quote(self):
        real = records_from_logs

        def no_gross(*a, **kw):
            return [dict(r, pool_quote_amount=None) for r in real(*a, **kw)]

        with mock.patch.object(p7, "records_from_logs", no_gross):
            self.assertEqual(self.one(self.sell_tape(), tx_of(self.sell_doc))[0], ("sell", "unresolved", "field_missing"))

    def test_a_decoder_error_is_no_record_and_counted(self):
        def boom(*a, **kw):
            raise ValueError("bad blob")

        (frame_row,), _ = self.frame_of([self.sell_tape()])
        with mock.patch.object(p7, "records_from_logs", boom):
            results, extras = p7.run_check([("main", frame_row)], lambda sig: tx_of(self.sell_doc))
        self.assertEqual(results, [("sell", "unresolved", "no_record")])
        self.assertEqual(extras["decode_errors"], 1)

    # -- exclusions: decided from the tape row alone, never fetched ----------------------------------------------------------------------
    def test_excluded_prints_leave_both_denominators_and_are_never_fetched(self):
        exact = self.exact_tape(signature="Exact" + "1" * 83)
        self.assertEqual(exact["ix_name"], "buy_exact_quote_in")
        zero = self.sell_tape(signature="Zero" + "1" * 84, zero_sol=True)
        nameless = self.buy_tape(signature="NoName" + "1" * 82)
        nameless.pop("ix_name")
        weird = self.sell_tape(signature="Weird" + "1" * 83, side="swap")
        frame, _ = self.frame_of([exact, zero, nameless, weird])
        self.assertEqual(len(frame), 4)

        def never(sig):
            raise AssertionError("an excluded print must not be fetched")

        results, extras = p7.run_check([("main", r) for r in frame], never)
        by_sig = {r["signature"][:4]: res for r, res in zip(frame, results)}
        self.assertEqual(by_sig["Exac"], (None, "excluded", "buy_exact_quote_in"))
        self.assertEqual(by_sig["Zero"], (None, "excluded", "zero_sol"))
        self.assertEqual(by_sig["NoNa"], (None, "excluded", "no_ix_name"))
        self.assertEqual(by_sig["Weir"], (None, "excluded", "not_buy_or_sell"))
        self.assertEqual(extras["tx_n"], 0)
        tally = p7.event_v_map().p7_raw_tally(results)
        self.assertEqual((tally["excluded"], tally["sell_n"], tally["buy_n"]), (4, 0, 0))
        self.assertFalse(p7.event_v_map().p7_raw_pass(tally))  # a side with no comparable print fails

    def test_a_transaction_holding_two_sampled_prints_is_fetched_once(self):
        doc = copy.deepcopy(self.sell_doc)
        logs = doc["meta"]["logMessages"]
        i = next(i for i, l in enumerate(logs) if "Program data: " in l and base64.b64decode(l.split("Program data: ", 1)[1].strip())[:8] == td._SELL_DISC)
        logs.insert(i + 1, logs[i])  # the same event twice: event_index 0 and 1
        frame, _ = self.frame_of([tape_of(doc, mint=self.sell_mint, rec_index=0), tape_of(doc, mint=self.sell_mint, rec_index=1)])
        self.assertEqual([r["event_index"] for r in frame], [0, 1])
        fetched = []

        def fetch(sig):
            fetched.append(sig)
            return tx_of(doc)

        results, extras = p7.run_check([("main", r) for r in frame], fetch)
        self.assertEqual(results, [("sell", "hit", None), ("sell", "hit", None)])
        self.assertEqual((len(fetched), extras["tx_n"]), (1, 1))

    def test_unresolved_prints_stay_in_the_denominator_and_the_99_percent_bar_is_per_side(self):
        ev = p7.event_v_map()
        skewed = scaled(SELL)

        def run(sell_misses: int, buy_unfetched: int):
            rows, txs = [], {}
            for i in range(100):
                sig = f"Sell{i:04d}" + "1" * 80
                doc = skewed if i < sell_misses else self.sell_doc
                rows.append(tape_of(doc, mint=self.sell_mint, signature=sig))
                txs[sig] = tx_of(doc)
            for i in range(100):
                sig = f"Buy{i:05d}" + "1" * 80
                rows.append(tape_of(self.buy_doc, mint=self.buy_mint, signature=sig))
                if i >= buy_unfetched:
                    txs[sig] = tx_of(self.buy_doc)
            frame, _ = self.frame_of(rows)
            results, _ = p7.run_check([("main", r) for r in frame], txs.get)
            return ev.p7_raw_tally(results)

        t = run(sell_misses=1, buy_unfetched=1)  # 99 of 100 on each side: exactly the bar
        self.assertEqual((t["sell_n"], t["sell_ok"], t["buy_n"], t["buy_ok"]), (100, 99, 100, 99))
        self.assertEqual(t["unresolved"]["fetch_failed"], 1)  # the unfetched buy is a miss and still counted in the 100
        self.assertTrue(ev.p7_raw_pass(t))
        t = run(sell_misses=2, buy_unfetched=0)  # 98 of 100 sells
        self.assertEqual((t["sell_ok"], t["buy_ok"]), (98, 100))
        self.assertFalse(ev.p7_raw_pass(t))
        t = run(sell_misses=0, buy_unfetched=2)  # 98 of 100 buys, both unresolved
        self.assertEqual((t["buy_n"], t["buy_ok"], t["unresolved"]["fetch_failed"]), (100, 98, 2))
        self.assertFalse(ev.p7_raw_pass(t))


# ---------------------------------------------------------------------------------------------------------------------------------------
class FetchTests(unittest.TestCase):
    def test_up_to_three_attempts_one_credit_per_call(self):
        sleeps = []
        errs = [p7.FetchError("http_429"), p7.FetchError("transport_URLError")]
        tx = {"slot": 1}

        def call(sig):
            if errs:
                raise errs.pop(0)
            return tx

        f = p7.Fetcher(call, 3, sleeps.append)
        self.assertIs(f("s"), tx)
        self.assertEqual(f.calls, 3)
        self.assertEqual(sleeps, [1.0, 2.0])
        self.assertEqual(dict(f.errors), {"http_429": 1, "transport_URLError": 1})

    def test_first_success_is_one_call_and_no_sleep(self):
        f = p7.Fetcher(lambda sig: {"slot": 1}, 3, lambda s: self.fail("no sleep after a success"))
        f("s")
        self.assertEqual(f.calls, 1)

    def test_a_null_transaction_is_retried_and_then_fetch_failed(self):
        f = p7.Fetcher(lambda sig: None, 3, lambda s: None)
        self.assertIsNone(f("s"))
        self.assertEqual((f.calls, dict(f.errors)), (3, {"null_result": 3}))

    def test_every_attempt_failing_is_none_after_exactly_three_calls(self):
        def call(sig):
            raise p7.FetchError("rpc_-32000")

        f = p7.Fetcher(call, 3, lambda s: None)
        self.assertIsNone(f("s"))
        self.assertEqual(f.calls, 3)


class FakeResp:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


class MakeCallTests(unittest.TestCase):
    URL = f"https://rpc.example.invalid/?api-key={FAKE_KEY}"

    def call(self, sig="S"):
        return p7.make_call(self.URL, phb.RateLimiter(0))(sig)

    def test_request_is_getTransaction_with_the_pinned_settings(self):
        seen = {}

        def urlopen(req, timeout=None):
            seen["body"] = json.loads(req.data)
            seen["url"] = req.full_url
            return FakeResp(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"slot": 9}}).encode())

        with mock.patch.object(p7.urllib.request, "urlopen", urlopen):
            self.assertEqual(self.call("SIG"), {"slot": 9})
        self.assertEqual(seen["url"], self.URL)
        self.assertEqual(seen["body"]["method"], "getTransaction")
        self.assertEqual(seen["body"]["params"], ["SIG", {"encoding": "json", "commitment": "confirmed", "maxSupportedTransactionVersion": 1}])

    def test_null_result_is_none(self):
        with mock.patch.object(p7.urllib.request, "urlopen", lambda req, timeout=None: FakeResp(b'{"jsonrpc":"2.0","id":1,"result":null}')):
            self.assertIsNone(self.call())

    def test_errors_are_a_kind_and_never_carry_the_url_or_key(self):
        cases = {
            "transport_URLError": urllib.error.URLError(f"unreachable {self.URL}"),
            "http_429": urllib.error.HTTPError(self.URL, 429, f"Too Many {self.URL}", {}, None),
            "transport_TimeoutError": TimeoutError(self.URL),
        }
        for kind, exc in cases.items():
            def urlopen(req, timeout=None, exc=exc):
                raise exc

            with mock.patch.object(p7.urllib.request, "urlopen", urlopen):
                with self.assertRaises(p7.FetchError) as cm:
                    self.call()
            self.assertEqual(cm.exception.kind, kind)
            self.assertNotIn(FAKE_KEY, str(cm.exception) + repr(cm.exception))
            self.assertIsNone(cm.exception.__cause__)
        bodies = {
            "rpc_-32000": json.dumps({"error": {"code": -32000, "message": f"bad {FAKE_KEY}"}}).encode(),
            "rpc_error": json.dumps({"error": "boom"}).encode(),
            "bad_payload": b"not json",
        }
        for kind, body in bodies.items():
            with mock.patch.object(p7.urllib.request, "urlopen", lambda req, timeout=None, body=body: FakeResp(body)):
                with self.assertRaises(p7.FetchError) as cm:
                    self.call()
            self.assertEqual(cm.exception.kind, kind)
            self.assertNotIn(FAKE_KEY, str(cm.exception))


# ---------------------------------------------------------------------------------------------------------------------------------------
class CliTests(Base):
    def setUp(self):
        super().setUp()
        self.out = self.tmp / "out"
        self.env = self.tmp / "helius.env"
        self.env.write_text(f"# comment\nHELIUS_API_KEY={FAKE_KEY}\n")
        self.rows = [
            self.sell_tape(),
            self.buy_tape(t_recv_ms=T0 + 2000),
            self.exact_tape(signature="Exact" + "1" * 83, t_recv_ms=T0 + 3000),
            self.sell_tape(signature="Unstamped" + "1" * 79, t_recv_ms=T0 + 4000),
        ]
        for k in td.EVENT_V_KEYS:
            self.rows[3].pop(k, None)
        write_hour(self.trades, "2026-10-10T13", self.rows)
        self.close_window()
        self.txs = {self.sell_doc["signature"]: tx_of(self.sell_doc), self.buy_doc["signature"]: tx_of(self.buy_doc)}
        self.argv = ["--trades-dir", str(self.trades), "--start", START, "--end", END, "--helius-env", str(self.env)]

    def go(self, extra=(), **kw):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = p7.main(self.argv + list(extra), _canonical=self.canon, _sleep=lambda s: None, **kw)
        return rc, out.getvalue(), err.getvalue()

    def assert_no_leak(self, text: str, *, allow_signatures: bool = False):
        """No signature (unless allowed), pool, mint, reserve or amount, and no key or URL fragment."""
        for r in self.rows:
            for k in RESERVE_KEYS:
                if isinstance(r.get(k), int) and len(str(r[k])) >= 7:
                    self.assertNotIn(str(r[k]), text, k)
            for k in ("pool", "mint"):
                self.assertNotIn(r[k], text, k)
            if not allow_signatures:
                self.assertNotIn(r["signature"], text)
        self.assertNotIn(FAKE_KEY, text)
        self.assertNotIn("api-key", text)

    def test_dry_run_prints_the_counts_makes_no_call_and_writes_nothing(self):
        def never(sig):
            raise AssertionError("a dry run must not call the RPC")

        rc, out, err = self.go(["--dry-run"], _call=never)
        self.assertEqual(rc, 0, err)
        d = json.loads(out)
        self.assertTrue(d["dry_run"])
        self.assertEqual((d["frame_n"], d["unstamped_canonical_n"], d["sample_n"], d["topup_n"]), (3, 1, 3, 0))
        self.assertEqual(d["comparable"], {"sell_n": 1, "buy_n": 1})
        self.assertEqual((d["excluded_n"], d["excluded_by"]["buy_exact_quote_in"]), (1, 1))
        self.assertEqual((d["tx_n"], d["max_credits"]), (2, 6))
        self.assertEqual(d["comparable_by_ix_name"], {"sell": {p7.NO_NAME: 1}, "buy": {"buy": 1}})
        self.assertEqual(d["excluded_by_name"]["buy_exact_quote_in"], {"buy_exact_quote_in": 1})
        self.assertFalse(any(k.startswith("line2") for k in d))  # the dry run judges nothing, line 2 included
        self.assertEqual(d["key"], "(slot, signature, event_index)")
        self.assertFalse(self.out.exists())
        self.assert_no_leak(out + err)

    def test_dry_run_needs_neither_the_env_file_nor_an_out_dir(self):
        self.env.unlink()
        rc, out, err = self.go(["--dry-run"])
        self.assertEqual(rc, 0, err)

    def test_full_run_writes_counts_and_a_prints_file_with_its_sha256(self):
        calls = []

        def call(sig):
            calls.append(sig)
            return self.txs[sig]

        rc, out, err = self.go(["--out-dir", str(self.out)], _call=call)
        self.assertEqual(rc, 0, err)
        d = json.loads(out)
        self.assertEqual(d, json.loads((self.out / "p7-tip.json").read_text()))
        self.assertEqual(sorted(calls), sorted(self.txs))
        self.assertEqual(d["credits"], 2)
        self.assertEqual((d["frame_n"], d["unstamped_canonical_n"], d["sample_n"], d["topup_n"], d["tx_n"]), (3, 1, 3, 0, 2))
        self.assertEqual(d["comparable"], {"sell_n": 1, "buy_n": 1})
        self.assertEqual((d["hits"], d["shares"]), ({"sell": 1, "buy": 1}, {"sell": 1.0, "buy": 1.0}))
        self.assertEqual((d["excluded_n"], d["excluded_by"]["buy_exact_quote_in"]), (1, 1))
        self.assertEqual(d["unresolved"], {"fetch_failed": 0, "no_record": 0, "slot_mismatch": 0, "identity_mismatch": 0, "field_missing": 0})
        # line 1 holds on both prints, but one comparable buy is short of the ruling's 100: not a pass
        self.assertEqual(d["acceptance"]["line1_pass"], True)
        self.assertEqual(d["acceptance"]["buy_n_at_least_100"], False)
        self.assertIs(d["pass"], False)
        self.assertEqual(d["bars"], {"sell": 0.99, "buy": 0.99, "min_comparable_buys": 100})
        self.assertEqual(d["by_ix_name"], {"sell": {p7.NO_NAME: {"n": 1, "hits": 1, "share": 1.0}}, "buy": {"buy": {"n": 1, "hits": 1, "share": 1.0}}})
        self.assertEqual(d["excluded_by_name"], {"zero_sol": {}, "not_buy_or_sell": {}, "buy_exact_quote_in": {"buy_exact_quote_in": 1},
                                                 "no_ix_name": {}, "ix_not_listed": {}})
        self.assertEqual(set(d["excluded_by"]), set(p7.buy_amend().P7_RAW_EXCLUSIONS))
        self.assertEqual(d["p7_buy_amend_sha256"], AMEND_SHA256)
        for rule in ("line2_exp025", "line2_exp024"):  # reported on the 3-print main draw, never scored
            self.assertEqual((d[rule]["prints"], d[rule]["sell"]["n"], d[rule]["buy"]["n"], d[rule]["scored"]), (3, 1, 2, False), rule)
            self.assertEqual(set(d[rule]["by_ix_name"]), {p7.NO_NAME, "buy", "buy_exact_quote_in"}, rule)
        self.assertEqual(d["window"], {"start": START, "end": END, "field": "t_recv_ms"})
        self.assertEqual(d["decoder_blob"], p7.DECODER_BLOB)
        self.assertIs(d["decoder_blob_pinned"], True)
        self.assertEqual(d["getTransaction"], {"encoding": "json", "commitment": "confirmed", "maxSupportedTransactionVersion": 1, "attempts": 3})
        prints = (self.out / "p7-tip-prints.jsonl").read_bytes()
        self.assertEqual(d["prints_sha256"], hashlib.sha256(prints).hexdigest())
        lines = [json.loads(x) for x in prints.splitlines()]
        self.assertEqual(len(lines), 3)
        self.assertEqual({(x["line"], x["outcome"], x["reason"]) for x in lines},
                         {("sell", "hit", None), ("buy", "hit", None), (None, "excluded", "buy_exact_quote_in")})
        self.assertEqual(set(lines[0]), {"set", "slot", "signature", "event_index", "ix_name", "line", "outcome", "reason"})
        self.assertEqual(sorted(x["ix_name"] for x in lines), sorted([p7.NO_NAME, "buy", "buy_exact_quote_in"]))
        self.assertIs(d["acceptance"]["one_full_utc_hour"], False)  # 13:17:18 to 14:30:00 is not the declared [H, H+1h) form

    def test_a_raising_line2_helper_still_exits_0_writes_both_files_and_leaves_line1_and_pass_unchanged(self):
        """Line 2 runs after every credit is spent: a helper that raises is reported by type and the line-1 acceptance is still written."""
        def call(sig):
            return self.txs[sig]

        rc, out, err = self.go(["--out-dir", str(self.out)], _call=call)
        self.assertEqual(rc, 0, err)
        clean = json.loads(out)

        def tier_lines(sample, v_of):  # as tools.boostfloor_inputs.tier_fee does on a sampled sell with base_reserve 0
            raise ZeroDivisionError("float division by zero")

        out2 = self.tmp / "out2"
        with mock.patch.object(p7, "exp024_tier_lines", lambda: tier_lines):
            rc, out, err = self.go(["--out-dir", str(out2)], _call=call)
        self.assertEqual(rc, 0, err)
        self.assertTrue((out2 / "p7-tip.json").exists())
        self.assertTrue((out2 / "p7-tip-prints.jsonl").exists())
        d = json.loads(out)
        self.assertEqual(d, json.loads((out2 / "p7-tip.json").read_text()))
        self.assertEqual(d["line2_exp024"], {"error": "ZeroDivisionError", "scored": False})
        self.assertEqual((d["pass"], d["acceptance"]), (clean["pass"], clean["acceptance"]))
        self.assertEqual({k: v for k, v in d.items() if k != "line2_exp024"}, {k: v for k, v in clean.items() if k != "line2_exp024"})
        self.assertEqual((out2 / "p7-tip-prints.jsonl").read_bytes(), (self.out / "p7-tip-prints.jsonl").read_bytes())
        self.assertNotIn("float division", out + err)  # the type only

    def test_stdout_stderr_and_summary_carry_no_signature_pool_mint_amount_or_key(self):
        rc, out, err = self.go(["--out-dir", str(self.out)], _call=lambda sig: self.txs[sig])
        self.assertEqual(rc, 0, err)
        self.assertIn("fetched 2/2 transactions, credits=2", err)  # progress is counts only
        self.assert_no_leak(out)
        self.assert_no_leak(err)
        self.assert_no_leak((self.out / "p7-tip.json").read_text())
        # the prints file holds signatures and outcomes, and still no pool, mint, reserve, amount or key
        prints = (self.out / "p7-tip-prints.jsonl").read_text()
        self.assert_no_leak(prints, allow_signatures=True)
        self.assertIn(self.sell_doc["signature"], prints)

    def test_a_law_miss_fails_the_check(self):
        doc = scaled(SELL)
        write_hour(self.trades, "2026-10-10T13", [tape_of(doc, mint=self.sell_mint), self.rows[1]])
        txs = {doc["signature"]: tx_of(doc), self.buy_doc["signature"]: tx_of(self.buy_doc)}
        rc, out, err = self.go(["--out-dir", str(self.out)], _call=lambda sig: txs[sig])
        self.assertEqual(rc, 0, err)
        d = json.loads(out)
        self.assertEqual((d["hits"], d["comparable"]), ({"sell": 0, "buy": 1}, {"sell_n": 1, "buy_n": 1}))
        self.assertIs(d["pass"], False)

    def test_real_http_path_with_a_failing_transport_keeps_the_key_out_of_everything(self):
        """No _call: the URL is built from the env file's key. A transport error whose text names the URL must reach no output."""
        seen = []

        def urlopen(req, timeout=None):
            seen.append(req.full_url)
            raise urllib.error.URLError(f"failed to reach {req.full_url}")

        with mock.patch.object(p7.urllib.request, "urlopen", urlopen):
            rc, out, err = self.go(["--out-dir", str(self.out), "--rps", "1000"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(seen), 6)  # 2 transactions x 3 attempts
        self.assertTrue(all(FAKE_KEY in u for u in seen))  # the key really was in the URL that was used
        d = json.loads(out)
        self.assertEqual(d["credits"], 6)
        self.assertEqual(d["unresolved"]["fetch_failed"], 2)
        self.assertEqual(d["fetch_errors"], {"transport_URLError": 6})
        self.assertIs(d["pass"], False)
        self.assert_no_leak(out)
        self.assert_no_leak(err)
        for f in self.out.iterdir():
            self.assert_no_leak(f.read_text(), allow_signatures=True)

    def test_an_unexpected_exception_prints_only_its_type(self):
        def boom(sig):
            raise RuntimeError(f"https://x/?api-key={FAKE_KEY}")

        rc, out, err = self.go(["--out-dir", str(self.out)], _call=boom)
        self.assertEqual(rc, 1)
        self.assertEqual(err.strip(), "tip_event_v_p7: failed: RuntimeError")
        self.assertNotIn(FAKE_KEY, out + err)

    # -- refusals: exit 2, nothing fetched, nothing written ------------------------------------------------------------------------------------
    def refused(self, extra=()):
        def never(sig):
            raise AssertionError("must not fetch")

        rc, out, err = self.go(extra, _call=never)
        self.assertEqual(rc, 2, err)
        self.assertEqual(out, "")
        return err

    def test_refuses_on_another_decoder_blob_even_for_a_dry_run(self):
        with mock.patch.object(p7, "DECODER_BLOB", "0" * 40):
            self.assertIn("decoder blob", self.refused(["--out-dir", str(self.out)]))
            self.assertFalse(self.out.exists())
            self.assertIn("decoder blob", self.refused(["--dry-run"]))

    def test_refuses_to_overwrite_an_earlier_run(self):
        self.out.mkdir()
        (self.out / "p7-tip.json").write_text("{}")
        self.assertIn("already exist", self.refused(["--out-dir", str(self.out)]))
        self.assertEqual((self.out / "p7-tip.json").read_text(), "{}")

    def test_refuses_without_an_out_dir_or_on_a_bad_window(self):
        self.assertIn("--out-dir", self.refused([]))
        self.refused(["--out-dir", str(self.out), "--end", START])
        self.refused(["--out-dir", str(self.out), "--start", "yesterday"])
        self.assertFalse(self.out.exists())

    def test_refuses_an_open_window_and_a_row_at_or_after_end_closes_it(self):
        (self.trades / "trades-2026-10-10T14.jsonl").unlink()  # nothing at or after END on disk
        for extra in (["--dry-run"], ["--out-dir", str(self.out)]):
            self.assertIn("not closed", self.refused(extra))
        self.assertFalse(self.out.exists())
        before = self.sell_tape(signature="Before" + "1" * 82, t_recv_ms=p7.parse_utc(END) - 1)
        write_hour(self.trades, "2026-10-10T14", [before])  # a row just before END does not close it
        self.assertIn("not closed", self.refused(["--dry-run"]))
        at_end = self.sell_tape(signature="AtEnd" + "1" * 83, t_recv_ms=p7.parse_utc(END))
        write_hour(self.trades, "2026-10-10T14", [before, at_end])  # t_recv_ms == END closes it (>=)
        rc, out, err = self.go(["--dry-run"])
        self.assertEqual(rc, 0, err)
        self.assertIs(json.loads(out)["frame"]["window_closed"], True)

    def test_a_non_pumpswap_row_after_end_closes_the_window(self):
        (self.trades / "trades-2026-10-10T14.jsonl").unlink()
        bonding = {"venue": "pump_bonding", "slot": 9, "signature": "Bond" + "1" * 84, "event_index": 0, "t_recv_ms": p7.parse_utc(END) + 1}
        write_hour(self.trades, "2026-10-10T14", [bonding])
        self.assertNotIn(b"pumpswap", (self.trades / "trades-2026-10-10T14.jsonl").read_bytes())
        rc, out, err = self.go(["--dry-run"])
        self.assertEqual(rc, 0, err)

    def test_an_end_on_the_hour_is_closed_only_by_a_row_in_the_next_hours_file(self):
        (self.trades / "trades-2026-10-10T14.jsonl").unlink()
        hour_end = ["--end", "2026-10-10T14:00:00Z"]
        self.assertIn("not closed", self.refused(["--dry-run"] + hour_end))
        edge = p7.parse_utc("2026-10-10T14:00:00Z")
        write_hour(self.trades, "2026-10-10T14", [self.sell_tape(signature="Edge" + "1" * 84, t_recv_ms=edge + 500)])
        rc, out, err = self.go(["--dry-run"] + hour_end)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["frame_n"], 3)  # the row after the end is read to close the window and is not in the frame

    def test_refuses_when_the_frame_passes_max_frame_rows_even_for_a_dry_run(self):
        for extra in (["--dry-run"], ["--out-dir", str(self.out)]):
            self.assertIn("passed 2 rows", self.refused(extra + ["--max-frame-rows", "2"]))  # the frame holds 3
        self.assertFalse(self.out.exists())
        self.assertEqual(self.go(["--dry-run", "--max-frame-rows", "3"])[0], 0)

    def test_refuses_without_a_usable_key_before_any_call(self):
        self.env.write_text("OTHER=1\n")
        with mock.patch.object(p7.urllib.request, "urlopen", lambda *a, **k: self.fail("no call without a key")):
            rc, out, err = self.go(["--out-dir", str(self.out)])
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("HELIUS_API_KEY", err)
        self.assertFalse(self.out.exists())
        self.env.unlink()  # an unreadable env file is the same refusal
        with mock.patch.object(p7.urllib.request, "urlopen", lambda *a, **k: self.fail("no call without a key")):
            self.assertEqual(self.go(["--out-dir", str(self.out)])[0], 2)

    def test_a_missing_end_is_a_usage_error_and_the_trades_dir_must_exist(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                p7.main(["--dry-run"])
        with self.assertRaises(SystemExit):  # --start has no default: the acceptance run names its declared H
            with contextlib.redirect_stderr(io.StringIO()):
                p7.main(["--dry-run", "--end", END])
        shutil.rmtree(self.trades)
        self.assertIn("trades-dir", self.refused(["--dry-run"]))


# ---------------------------------------------------------------------------------------------------------------------------------------
# The amended buy side (quant-proof ruling 2026-10-10, item 2), through this tool.
def synth_buy(*, q_vault: int, v: int, base: int, token: int, pqa: int, ix_name: str | None = "buy", sig: str = "Syn" + "1" * 85,
              sol: int | None = None) -> tuple[dict, dict]:
    """(tape row, raw event) of one synthetic buy whose identity fields agree, so judge() reaches the law."""
    tape = {"slot": 7, "signature": sig, "event_index": 0, "tx_index": sig, "pool": "SynPool", "side": "buy",
            "sol_lamports": pqa if sol is None else sol, "token_raw": token, "quote_reserve": q_vault, "base_reserve": base,
            "virtual_quote_reserves": v}
    if ix_name is not None:
        tape["ix_name"] = ix_name
    raw = dict(tape, pool_quote_amount=pqa)
    raw.pop("tx_index")
    return tape, raw


class AmendPinTests(unittest.TestCase):
    def _copy(self, *files) -> Path:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        art = root / "ARTIFACTS" / "exp025"
        for f in files:
            (art / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / "ARTIFACTS" / "exp025" / f, art / f)
        return art

    def _fresh(self, root: Path):
        return mock.patch.multiple(p7, REPO=root, _EV=None, _AMEND=None, _FEE_FRAC=None)

    def test_the_tool_loads_the_sha_pinned_amendment(self):
        am = p7.buy_amend()
        self.assertEqual(am.SHA256, AMEND_SHA256)
        self.assertIn(f"{AMEND_SHA256}  p7_buy_amend.py", (REPO / "ARTIFACTS" / "exp025" / "SHA256SUMS").read_text())
        self.assertEqual(am.EVENT_V_MAP_SHA256, p7.event_v_map().SHA256)
        self.assertEqual(am.P7_BUY_IX_WHITELIST, ("buy", "buy_v2"))
        self.assertEqual(am.P7_RAW_EXCLUSIONS, ("zero_sol", "not_buy_or_sell", "buy_exact_quote_in", "no_ix_name", "ix_not_listed"))

    def test_a_changed_amendment_is_refused_and_the_cli_exits_2_before_reading_any_tape(self):
        art = self._copy("event_v_map.py", "p7_buy_amend.py", "SHA256SUMS")
        root = art.parent.parent
        with self._fresh(root):
            p7.buy_amend()  # untouched copies load
        with open(art / "p7_buy_amend.py", "a") as fh:
            fh.write("\n# edited\n")
        with self._fresh(root):
            with self.assertRaises(p7.Refused) as cm:
                p7.buy_amend()
        self.assertIn("p7_buy_amend.py", str(cm.exception))
        with self._fresh(root), mock.patch.object(p7, "build_frame", lambda *a, **k: self.fail("no tape is read after a refusal")):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = p7.main(["--start", START, "--end", END, "--dry-run", "--trades-dir", str(root)])
        self.assertEqual(rc, 2)
        self.assertIn("refused", err.getvalue())

    def test_an_amendment_pinning_another_event_v_map_is_refused(self):
        art = self._copy("event_v_map.py", "p7_buy_amend.py", "SHA256SUMS")
        with open(art / "event_v_map.py", "a") as fh:  # SHA256SUMS re-pinned to the edited file: the tool would load it, the amendment not
            fh.write("\n# edited\n")
        digest = hashlib.sha256((art / "event_v_map.py").read_bytes()).hexdigest()
        sums = art / "SHA256SUMS"
        sums.write_text("".join(f"{digest}  event_v_map.py\n" if l.endswith("  event_v_map.py") else l + "\n" for l in sums.read_text().splitlines()))
        with self._fresh(art.parent.parent):
            self.assertEqual(p7.event_v_map().SHA256, digest)
            with self.assertRaises(p7.Refused):
                p7.buy_amend()

    def test_a_changed_line2_tier_is_refused(self):
        art = self._copy("event_v_map.py", "p7_buy_amend.py", "SHA256SUMS", "scripts/common2.py")
        with self._fresh(art.parent.parent):
            self.assertAlmostEqual(p7.exp025_tier()(10**11, 6 * 10**14), 0.0125)
        with open(art / "scripts" / "common2.py", "a") as fh:
            fh.write("\n# edited\n")
        with self._fresh(art.parent.parent):
            with self.assertRaises(p7.Refused):
                p7.exp025_tier()


class AmendJudgeTests(Base):
    Q_VAULT, V, BASE = 70_000_000_000, 30_000_000_000, 600_000_000_000_000  # Q = 100 SOL incl. V; one base unit costs ~1/6000 lamport

    def test_whitelist_buy_and_buy_v2_only_every_other_buy_excluded_before_fetch_with_its_cause(self):
        names = {"buy": None, "buy_v2": None, "buy_exact_quote_in": "buy_exact_quote_in", "buy_exact_quote_in_v2": "buy_exact_quote_in",
                 "multi_hop_swap": "ix_not_listed", "Buy": "ix_not_listed", "buy_v3": "ix_not_listed", "": "no_ix_name"}
        rows = []
        for i, (name, cause) in enumerate(names.items()):
            rows.append((self.buy_tape(signature=f"N{i:03d}" + "1" * 84, ix_name=name), cause))
        nameless = self.buy_tape(signature="Nnull" + "1" * 83)
        nameless.pop("ix_name")
        rows += [(nameless, "no_ix_name"), (self.buy_tape(signature="Nnone" + "1" * 83, ix_name=None), "no_ix_name"),
                 (self.buy_tape(signature="Nzero" + "1" * 83, zero_sol=True), "zero_sol"),
                 (self.sell_tape(signature="Nsell" + "1" * 83), None)]
        am = p7.buy_amend()
        for row, cause in rows:
            self.assertEqual(am.p7_raw_exclusion(row), cause, row.get("ix_name"))
        frame, _ = self.frame_of([r for r, _ in rows])
        fetched = []

        def fetch(sig):
            fetched.append(sig)
            return None

        sampled = [("main", r) for r in frame]
        results, extras = p7.run_check(sampled, fetch)
        comparable = {r["signature"] for r in frame if am.p7_raw_line(r) is not None}
        self.assertEqual(sorted(fetched), sorted(comparable))  # buy, buy_v2 and the sell only
        self.assertEqual(extras["tx_n"], 3)
        excl = p7.excluded_by_name(sampled, results)
        self.assertEqual(excl["ix_not_listed"], {"Buy": 1, "buy_v3": 1, "multi_hop_swap": 1})
        self.assertEqual(excl["buy_exact_quote_in"], {"buy_exact_quote_in": 1, "buy_exact_quote_in_v2": 1})
        self.assertEqual(excl["no_ix_name"], {p7.NO_NAME: 3})
        self.assertEqual(excl["zero_sol"], {"buy": 1})
        self.assertEqual(p7.by_ix_name(sampled, results)["buy"], {"buy": {"n": 1, "hits": 0, "share": 0.0}, "buy_v2": {"n": 1, "hits": 0, "share": 0.0}})
        # the plan (dry run) sees the same split before any fetch
        plan = p7._plan(sampled)
        self.assertEqual(plan["comparable_by_ix_name"]["buy"], {"buy": 1, "buy_v2": 1})
        self.assertEqual(plan["excluded_by_name"], excl)
        self.assertEqual(plan["excluded_by"], {c: sum(v.values()) for c, v in excl.items()})

    def test_topup_candidates_are_whitelisted_buys_only(self):
        # 2,000 prints: 1 in 4 a multi_hop_swap buy (comparable under the old rule), 1 in 40 a whitelisted buy (50 in all), the rest sells
        rows = []
        for i in range(2000):
            sig = f"T{i:05d}" + "1" * 82
            if i % 4 == 0:
                r = self.buy_tape(signature=sig, ix_name="multi_hop_swap")
            elif i % 40 == 1:
                r = self.buy_tape(signature=sig, ix_name="buy_v2" if i % 80 == 1 else "buy")
            else:
                r = self.sell_tape(signature=sig)
            rows.append(dict(r, slot=1000 + i, tx_index=None, t_recv_ms=T0 + 1000 + i))
        frame, _ = self.frame_of(rows)
        main, topup = p7.draw(frame)
        am, ev = p7.buy_amend(), p7.event_v_map()
        have = sum(1 for r in main if am.p7_raw_line(r) == "buy")
        self.assertLess(have, 50)
        self.assertGreater(sum(1 for r in main if ev.p7_raw_line(r) == "buy"), 100)  # the old rule would count multi_hop_swap and skip the top-up
        self.assertEqual(len(topup), 50 - have)  # fewer candidates than needed: every whitelisted buy not in the main draw
        self.assertTrue(all(r["ix_name"] in ("buy", "buy_v2") for r in topup))
        self.assertFalse({r["signature"] for r in main} & {r["signature"] for r in topup})

    def judge_synth(self, **kw) -> tuple:
        tape, raw = synth_buy(**kw)
        return p7.judge(tape, raw)

    def test_dust_exact_out_buy_hits_under_the_inverse_law_where_the_forward_law_misses(self):
        ev, q = p7.event_v_map(), self.Q_VAULT + self.V
        found = None
        for token in range(9_960_000, 9_980_000, 37):  # qin about 1,660 lamports, as the ruling's 4.767 bp print
            qin = -((-q * token) // (self.BASE - token))  # the program's exact-out quote in
            if not ev.within_tolerance(token, ev.cp_buy_token_out(q, 0, self.BASE, qin)):
                found = (token, qin)
                break
        self.assertIsNotNone(found, "no dust exact-out print where the forward law misses")
        token, qin = found
        self.assertLess(qin, 2_000)
        self.assertEqual(self.judge_synth(q_vault=self.Q_VAULT, v=self.V, base=self.BASE, token=token, pqa=qin), ("buy", "hit", None))

    def test_exact_in_buy_hits(self):
        q = self.Q_VAULT + self.V
        for qin in (1_000_000_000, 50_000_000, 123_457):
            token = self.BASE * qin // (q + qin)  # the program's exact-in token out
            self.assertEqual(self.judge_synth(q_vault=self.Q_VAULT, v=self.V, base=self.BASE, token=token, pqa=qin), ("buy", "hit", None), qin)

    def test_misses_fee_inside_the_quote_gross_vault_lp_offset_and_a_reserve_at_or_below_the_token_amount(self):
        q, token = self.Q_VAULT + self.V, 6_000_000_000  # about 1 SOL
        law = -((-q * token) // (self.BASE - token))
        hit = dict(q_vault=self.Q_VAULT, v=self.V, base=self.BASE, token=token)
        self.assertEqual(self.judge_synth(**hit, pqa=law), ("buy", "hit", None))
        self.assertEqual(self.judge_synth(**hit, pqa=law + law * 5 // 10_000), ("buy", "miss", None))  # 5 bp of fee inside pool_quote_amount
        gross = -((-(q + q * 3 // 100) * token) // (self.BASE - token))  # a gross vault (pending fees in Q, ~3%)
        self.assertEqual(self.judge_synth(**hit, pqa=gross), ("buy", "miss", None))
        lp = -((-q * token) // (self.BASE + self.BASE // 100 - token))  # an LP-sized base offset (1% of the reserve)
        self.assertEqual(self.judge_synth(**hit, pqa=lp), ("buy", "miss", None))
        self.assertEqual(self.judge_synth(q_vault=self.Q_VAULT, v=self.V, base=token, token=token, pqa=law), ("buy", "miss", None))
        self.assertEqual(self.judge_synth(q_vault=self.Q_VAULT, v=self.V, base=token - 1, token=token, pqa=law), ("buy", "miss", None))
        # the tip check prices on vault + V, V0 = 0: the vault alone misses the same print
        self.assertEqual(self.judge_synth(q_vault=self.Q_VAULT, v=self.V, base=self.BASE, token=token,
                                          pqa=-((-self.Q_VAULT * token) // (self.BASE - token))), ("buy", "miss", None))

    def test_per_ix_name_counts_agree_with_the_amendments_tally(self):
        am, q = p7.buy_amend(), self.Q_VAULT + self.V
        sampled, results = [], []
        for i, (name, good) in enumerate([("buy", True), ("buy", False), ("buy_v2", True), ("buy_v2", True), ("multi_hop_swap", True)]):
            token = 6_000_000 * (i + 1)
            law = -((-q * token) // (self.BASE - token))
            tape, raw = synth_buy(q_vault=self.Q_VAULT, v=self.V, base=self.BASE, token=token, pqa=law if good else law * 2, ix_name=name,
                                  sig=f"Per{i}" + "1" * 84)
            sampled.append(("main", tape))
            results.append(p7.judge(tape, raw))
        per = p7.by_ix_name(sampled, results)["buy"]
        self.assertEqual(per, {"buy": {"n": 2, "hits": 1, "share": 0.5}, "buy_v2": {"n": 2, "hits": 2, "share": 1.0}})
        tally = am.p7_raw_tally([(row, res) for (_, row), res in zip(sampled, results)])
        self.assertEqual({k: {"n": v["n"], "hits": v["ok"]} for k, v in tally["buy_by_ix"].items()}, {k: {"n": v["n"], "hits": v["hits"]} for k, v in per.items()})
        self.assertEqual((tally["buy_n"], tally["buy_ok"], tally["excluded_by"]["ix_not_listed"], tally["ix_not_listed_by"]), (4, 3, 1, {"multi_hop_swap": 1}))
        summary = p7.summarize(tally, window=(T0, T0 + 3_600_000), frame_info={"frame_n": 5, "unstamped_canonical_n": 0}, main_n=5, topup_n=0,
                               extras={"tx_n": 4, "decode_errors": 0}, credits=4, errors={}, prints_sha256=None, blob=p7.DECODER_BLOB,
                               per_name=p7.by_ix_name(sampled, results), excl_names=p7.excluded_by_name(sampled, results))
        self.assertEqual(summary["by_ix_name"]["buy"], per)
        self.assertEqual(summary["excluded_by_name"]["ix_not_listed"], {"multi_hop_swap": 1})
        self.assertIs(summary["pass"], False)  # 3 of 4 buys, and fewer than 100


# ---------------------------------------------------------------------------------------------------------------------------------------
class Line2Tests(unittest.TestCase):
    Q_VAULT, V, BASE = 70_000_000_000, 30_000_000_000, 600_000_000_000_000  # mcap ~167 SOL: tier 12,500 ppm

    def row(self, side: str, sol: int, tok: int, ix_name: str | None = None, **over) -> dict:
        r = {"slot": 1, "pool": "SynPool", "side": side, "sol_lamports": sol, "token_raw": tok, "quote_reserve": self.Q_VAULT,
             "base_reserve": self.BASE, "virtual_quote_reserves": self.V}
        if ix_name is not None:
            r["ix_name"] = ix_name
        r.update(over)
        return r

    def rows(self) -> list:
        q, b, f, tok = self.Q_VAULT + self.V, self.BASE, 0.0125, 6_000_000_000
        sell_exact = round(q * tok / (b + tok) * (1 - f))
        net = tok * q / (b - tok)
        return [
            self.row("sell", sell_exact, tok),                                    # on the tier: match
            self.row("sell", sell_exact + sell_exact * 3 // 10_000, tok),         # 3 bp off: miss
            self.row("buy", round(net / (1 - f)), tok, "buy"),                    # implied fee = f: match
            self.row("buy", round(net * (1 + f)), tok, "buy_exact_quote_in"),     # implied f / (1 + f): about 1.5 bp under the tier, a miss
            self.row("buy", 0, tok, "buy"),                                       # zero buy: skipped
            self.row("buy", round(net / (1 - f)), tok, "multi_hop_swap"),         # match
            self.row("buy", 5, 5, "buy", base_reserve=None),                      # a field missing: counted apart, not judged
        ]

    def test_the_exp025_tier_is_the_exp024_tier(self):
        from tools import boostfloor_score as bf

        tier = p7.exp025_tier()
        for mcap_sol in (1, 419.9, 420, 420.1, 1469, 1470, 9820, 50_000, 98_239, 98_240, 200_000):
            q, b = int(mcap_sol * 1_000_000), 1_000_000_000_000
            self.assertAlmostEqual(tier(q, b), bf.tier_fee(q, b), places=12, msg=mcap_sol)

    def test_both_rules_count_per_side_and_per_ix_name_and_are_not_scored(self):
        for rule in ("exp025", "exp024"):
            r = p7.line2_report(self.rows(), rule)
            self.assertEqual((r["prints"], r["fields_missing"], r["scored"]), (6, 1, False), rule)
            self.assertEqual((r["sell"]["n"], r["sell"]["match"], r["sell"]["need"]), (2, 1, 0.75), rule)
            self.assertEqual((r["buy"]["n"], r["buy"]["match"], r["buy"]["skipped"], r["buy"]["need"]), (3, 2, 1, 0.90), rule)
            self.assertIs(r["line_pass"], False)  # sells 1/2 < 75%, buys 2/3 < 90%
            per = r["by_ix_name"]
            self.assertEqual(set(per), {p7.NO_NAME, "buy", "buy_exact_quote_in", "multi_hop_swap"}, rule)
            self.assertEqual((per[p7.NO_NAME]["sell"]["n"], per[p7.NO_NAME]["sell"]["match"]), (2, 1))
            self.assertEqual((per["buy"]["buy"]["n"], per["buy"]["buy"]["match"], per["buy"]["buy"]["skipped"]), (1, 1, 1))
            self.assertEqual((per["buy_exact_quote_in"]["buy"]["n"], per["buy_exact_quote_in"]["buy"]["match"]), (1, 0))
            self.assertEqual((per["multi_hop_swap"]["buy"]["n"], per["multi_hop_swap"]["buy"]["match"]), (1, 1))

    def test_line2_passes_on_the_tier_and_uses_the_prints_own_v(self):
        rows = self.rows()
        ok = [rows[0], rows[2], rows[5]]  # the on-tier sell and the two on-tier buys
        for rule in ("exp025", "exp024"):
            self.assertIs(p7.line2_report(ok, rule)["line_pass"], True, rule)
            no_v = [dict(r, virtual_quote_reserves=0) for r in ok]  # the vault alone: the price level is 30% off
            r = p7.line2_report(no_v, rule)
            self.assertEqual((r["sell"]["match"], r["buy"]["match"]), (0, 0), rule)

    def test_a_sell_with_base_reserve_zero_is_a_reported_line2_error_never_a_crash(self):
        rows = self.rows() + [self.row("sell", 1_000, 1_000, base_reserve=0)]
        with self.assertRaises(ZeroDivisionError):  # the real trigger: tools.boostfloor_inputs.tier_fee divides by base_reserve
            p7.line2_report(rows, "exp024")
        self.assertEqual(p7._line2_or_error(rows, "exp024"), {"error": "ZeroDivisionError", "scored": False})

    def test_line2_does_not_change_pass(self):
        tally = {"sell_n": 100, "sell_ok": 100, "buy_n": 100, "buy_ok": 100, "excluded": 0,
                 "excluded_by": {c: 0 for c in p7.buy_amend().P7_RAW_EXCLUSIONS}, "unresolved": {r: 0 for r in p7.event_v_map().P7_RAW_REASONS}}
        h = p7.parse_utc("2026-10-10T16:00:00Z")
        kw = dict(window=(h, h + 3_600_000), frame_info={"frame_n": 200, "unstamped_canonical_n": 0}, main_n=200, topup_n=0,
                  extras={"tx_n": 200, "decode_errors": 0}, credits=200, errors={}, prints_sha256=None, blob=p7.DECODER_BLOB)
        failing = {"exp025": p7.line2_report(self.rows(), "exp025"), "exp024": p7.line2_report(self.rows(), "exp024")}
        self.assertIs(failing["exp025"]["line_pass"], False)
        for line2 in (None, failing):
            s = p7.summarize(tally, **kw, line2=line2)
            self.assertIs(s["pass"], True)
        s = p7.summarize(dict(tally, buy_n=99, buy_ok=99), **kw, line2=failing)
        self.assertIs(s["pass"], False)  # 99 comparable buys is short of 100 even at 100%
        self.assertIs(s["acceptance"]["line1_pass"], True)
        for window in ((h + 60_000, h + 3_660_000), (h, h + 1_800_000), (h, h + 7_200_000)):  # not one full UTC hour
            s = p7.summarize(tally, **dict(kw, window=window), line2=failing)
            self.assertEqual((s["pass"], s["acceptance"]["one_full_utc_hour"], s["acceptance"]["line1_pass"]), (False, False, True), window)


if __name__ == "__main__":
    unittest.main()
