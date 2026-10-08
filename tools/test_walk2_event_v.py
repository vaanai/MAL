"""Walk-2 event-V decoder (audit A8, decoder half). Offline: repo fixtures only, no network.

Fixtures in tools/fixtures/walk2_event_v/ are public-RPC getTransaction results (2026-10-08, plus one
2026-09-29 boost tx), trimmed to signature, accountKeys and the meta fields the decoder reads. They
are decoder fixtures; no price, return or P&L is derived from them. Where no recorded transaction
exists (PostCompleteBuyEvent, SweepPoolFeeEvent, SweepBondingCurveFeeEvent: 0 sightings so far) the
test builds the blob from the published IDL layout and says so.
"""

from __future__ import annotations

import base64
import copy
import json
import tempfile
import unittest
from pathlib import Path

import tools.pump_history_backfill as bf
from observe.trade_decode import (
    EVENT_V_KEYS,
    WSOL_MINT,
    b58decode,
    b58encode,
    decode_extra_event,
    decode_program_data,
    event_v_fields,
    records_from_logs,
)
from observe.trade_store import stored_trade
from tools.pump_history_backfill import (
    CreditBudget,
    RateLimiter,
    iter_jsonl,
    rows_from_block,
    run_hour,
)
from tools.walk2_decoder_replay import NEW_ROW_KEYS, fixture_blocks

FIX = Path(__file__).resolve().parent / "fixtures" / "walk2_event_v"
SEPT = Path(__file__).resolve().parent / "fixtures" / "pump_structure_monitor"


def _load(name: str, base: Path = FIX) -> dict:
    return json.loads((base / name).read_text(encoding="utf-8"))


def _block(doc: dict) -> dict:
    tx = {
        "transaction": {"signatures": [doc["signature"]], "message": {"accountKeys": doc["accountKeys"], "instructions": []}},
        "meta": doc["meta"],
    }
    return {"slot": doc["slot"], "blockTime": doc["blockTime"], "transactions": [tx]}


def _decode(name: str, *, event_v: bool, base: Path = FIX) -> dict:
    doc = _load(name, base)
    block = _block(doc)
    first = rows_from_block(block, {}, "t", **({"event_v": True} if event_v else {}))
    pools = {r["pool"]: ("MINT" + r["pool"][:6], WSOL_MINT) for r in first["unresolved"] if r.get("pool")}
    return rows_from_block(block, dict(pools), "t", **({"event_v": True} if event_v else {}))


def _trade(name: str) -> dict:
    rows = _decode(name, event_v=True)["trades"]
    assert len(rows) == 1, (name, len(rows))
    return rows[0]


def _vault(doc: dict, pool: str, key: str) -> int:
    hits = [b for b in doc["meta"][key] if b.get("owner") == pool and b["mint"] == WSOL_MINT]
    assert len(hits) == 1, (pool, len(hits))
    return int(hits[0]["uiTokenAmount"]["amount"])


def _event_blob(doc: dict, disc_hex: str, nth: int = 0) -> bytes:
    found = []
    for line in doc["meta"]["logMessages"]:
        if "Program data: " in line:
            raw = base64.b64decode(line.split("Program data: ", 1)[1].strip())
            if raw[:8].hex() == disc_hex:
                found.append(raw)
    return found[nth]


def _program_data_line(raw: bytes) -> str:
    return "Program data: " + base64.b64encode(raw).decode()


class DefaultOffTests(unittest.TestCase):
    """event_v off: no new key, no events bucket, nothing different from the legacy decoder."""

    def test_no_new_keys_and_no_event_buckets(self) -> None:
        for path in sorted(FIX.glob("*.json")):
            res = _decode(path.name, event_v=False)
            self.assertEqual(set(res), {"trades", "creates", "migrations", "unresolved"}, path.name)
            for bucket in res.values():
                for row in bucket:
                    self.assertFalse(set(row) & set(NEW_ROW_KEYS), path.name)

    def test_event_v_adds_only_new_keys_to_every_fixture_row(self) -> None:
        """Old fields are byte-identical: stripping the new keys gives the default-mode row exactly."""
        for path in sorted(FIX.glob("*.json")):
            off = _decode(path.name, event_v=False)
            on = _decode(path.name, event_v=True)
            for bucket in ("trades", "creates", "migrations", "unresolved"):
                stripped = [{k: v for k, v in r.items() if k not in NEW_ROW_KEYS} for r in on[bucket]]
                self.assertEqual(
                    [json.dumps(r, sort_keys=True) for r in stripped],
                    [json.dumps(r, sort_keys=True) for r in off[bucket]],
                    (path.name, bucket),
                )

    def test_live_path_default_unchanged(self) -> None:
        """records_from_logs / decode_program_data without the flag produce no event-V key."""
        doc = _load("sell_v2_kept.json")
        recs = records_from_logs(doc["meta"]["logMessages"], slot=1, signature="s", t_recv_ms=1, commitment="confirmed", feed="f", pool_mints={})
        self.assertTrue(recs)
        for rec in recs:
            self.assertFalse(set(rec) & set(EVENT_V_KEYS))
        blob = _event_blob(doc, "3e2f370aa503dc2a")
        self.assertFalse(set(decode_program_data(blob)) & set(EVENT_V_KEYS))

    def test_stored_trade_writes_new_keys_only_when_present(self) -> None:
        doc = _load("sell_v2_kept.json")
        rec = records_from_logs(doc["meta"]["logMessages"], slot=1, signature="s", t_recv_ms=1, commitment="confirmed", feed="f", pool_mints={})[0]
        rec["quote_mint"] = WSOL_MINT
        self.assertFalse(set(stored_trade(rec)) & set(EVENT_V_KEYS))
        rec2 = records_from_logs(doc["meta"]["logMessages"], slot=1, signature="s", t_recv_ms=1, commitment="confirmed", feed="f", pool_mints={}, event_v=True)[0]
        rec2["quote_mint"] = WSOL_MINT
        # a SellEvent has no ix_name; every other event-V key is written
        self.assertTrue(set(EVENT_V_KEYS) - {"ix_name"} <= set(stored_trade(rec2)))
        self.assertNotIn("ix_name", stored_trade(rec2))


class TradeFieldTests(unittest.TestCase):
    def test_v2_buy_via_router(self) -> None:
        row = _trade("buy_exact_quote_in_v2_499_kept.json")
        self.assertEqual(row["ix_name"], "buy_exact_quote_in_v2")
        self.assertTrue(row["fee_recipient_zero"])
        self.assertGreater(row["virtual_quote_reserves"], 0)
        self.assertGreater(row["creator_fee_unclaimed"], 0)
        self.assertEqual(row["buyback_fee"], 1270544)

    def test_v1_buy_pays_fees(self) -> None:
        row = _trade("buy_v1_481_wsol.json")
        self.assertEqual(row["ix_name"], "buy")
        self.assertFalse(row["fee_recipient_zero"])
        self.assertEqual(len(_event_blob(_load("buy_v1_481_wsol.json"), "67f4521f2cf57777")), 489)

    def test_buy_exact_quote_in_v1(self) -> None:
        row = _trade("buy_exact_quote_in_496.json")
        self.assertEqual(row["ix_name"], "buy_exact_quote_in")
        self.assertFalse(row["fee_recipient_zero"])
        self.assertEqual(row["virtual_quote_reserves"], 17584317180)
        self.assertEqual(row["creator_fee_unclaimed"], 182733)

    def test_sell_v1_and_sell_v2(self) -> None:
        v1 = _trade("sell_433.json")
        self.assertNotIn("ix_name", v1)  # SellEvent has no ix_name
        self.assertFalse(v1["fee_recipient_zero"])
        self.assertEqual(v1["virtual_quote_reserves"], 554841812)
        v2 = _trade("sell_v2_kept.json")
        self.assertTrue(v2["fee_recipient_zero"])
        self.assertEqual(v2["virtual_quote_reserves"], 17584418739)
        self.assertEqual(v2["creator_fee_unclaimed"], 168884)
        self.assertEqual(len(_event_blob(_load("sell_v2_kept.json"), "3e2f370aa503dc2a")), 441)

    def test_boost_buy_is_fee_free_and_matches_boost_event_v(self) -> None:
        for name in ("boost_buy_and_burn.json", "boost_buy_and_burn_oct.json"):
            res = _decode(name, event_v=True)
            buy = res["trades"][0]
            self.assertTrue(buy["fee_recipient_zero"])
            self.assertEqual(buy["buyback_fee"], 0)
            blob = _event_blob(_load(name), "67f4521f2cf57777")
            self.assertEqual(int.from_bytes(blob[96:104], "little"), 0)  # protocol_fee
            (ev,) = [e for e in res["events"] if e["type"] == "boost_buy_and_burn"]
            # R7 of the audit: a boost buy does not move stored V (event V == the buy's pre-trade V)
            self.assertEqual(ev["virtual_quote_reserves"], buy["virtual_quote_reserves"])
            self.assertEqual(ev["pool"], buy["pool"])
            self.assertGreater(ev["boost_vault_remaining"], 0)
            self.assertEqual(ev["quote_amount_in_used"], buy["sol_lamports"])

    def test_bonding_trade_gets_ix_name_only(self) -> None:
        doc = _load("completing_tx_sep20.json", SEPT)
        blob = _event_blob(doc, "bddb7fd34ee661ee")
        self.assertEqual(event_v_fields(blob), {"ix_name": "buy"})

    def test_short_or_garbage_tail_is_empty_not_wrong(self) -> None:
        blob = _event_blob(_load("sell_v2_kept.json"), "3e2f370aa503dc2a")
        self.assertEqual(event_v_fields(blob[:300]), {})
        self.assertEqual(event_v_fields(b"\x00" * 40), {})
        # an older sell that stops at the V field still yields V but no creator_fee_unclaimed
        partial = event_v_fields(blob[:408])
        self.assertIn("virtual_quote_reserves", partial)
        self.assertNotIn("creator_fee_unclaimed", partial)

    def test_negative_v_is_signed(self) -> None:
        blob = bytearray(_event_blob(_load("sell_v2_kept.json"), "3e2f370aa503dc2a"))
        blob[392:408] = (-123456789).to_bytes(16, "little", signed=True)
        self.assertEqual(event_v_fields(bytes(blob))["virtual_quote_reserves"], -123456789)


class UnknownDiscTests(unittest.TestCase):
    def test_unknown_discriminators_are_counted(self) -> None:
        res = _decode("sell_433.json", event_v=True)
        self.assertEqual(res["unknown_disc"], {"3ae6f2034b7104a9": 1, "bd619feb8805018d": 1, "5698644759c77785": 1})
        res = _decode("buy_exact_quote_in_v2_499_kept.json", event_v=True)
        self.assertEqual(sum(res["unknown_disc"].values()), 4)
        self.assertEqual(res["bad_disc"], {})

    def test_rejected_known_trade_events_are_counted_as_bad(self) -> None:
        """A recorded non-SOL-quote pool: legacy sanity window drops both events. Counted, not silent."""
        res = _decode("buy_v1_nonwsol_rejected.json", event_v=True)
        self.assertEqual(res["trades"] + res["unresolved"], [])
        self.assertEqual(res["bad_disc"], {"3e2f370aa503dc2a": 1, "67f4521f2cf57777": 1})
        # the legacy output is the same silence
        off = _decode("buy_v1_nonwsol_rejected.json", event_v=False)
        self.assertEqual(off["trades"] + off["unresolved"], [])

    def test_no_unknown_when_all_known(self) -> None:
        res = _decode("boost_buy_and_burn_oct.json", event_v=True)
        self.assertEqual(res["unknown_disc"], {})


def _synthetic_post_complete_buy() -> bytes:
    keys = [bytes([i]) * 32 for i in (1, 2, 3, 4)]
    nums = [1_700_000_000 + 5, 111, 222, 100, 33, 30, 44, 55, 1000, 2000, 3000, 4000]
    out = bytes.fromhex("6fb06d8b316cd5fb") + b"".join(keys)
    out += nums[0].to_bytes(8, "little", signed=True)
    for n in nums[1:]:
        out += n.to_bytes(8, "little")
    assert len(out) == 232
    return out


class ExtraEventTests(unittest.TestCase):
    """Layouts from the 2026-10-07 IDL; PostCompleteBuy/Sweep blobs are BUILT here (no recorded sighting)."""

    def test_post_complete_buy_synthetic(self) -> None:
        ev = decode_extra_event(_synthetic_post_complete_buy())
        assert ev is not None
        self.assertEqual(ev["type"], "post_complete_buy")
        self.assertEqual(ev["trader"], b58encode(bytes([1]) * 32))
        self.assertEqual(ev["mint"], b58encode(bytes([2]) * 32))
        self.assertEqual(ev["event_ts"], 1_700_000_005)
        self.assertEqual((ev["base_out"], ev["quote_in"], ev["fee"], ev["creator_fee"], ev["buyback_fee"]), (111, 222, 33, 44, 55))
        self.assertEqual(
            (ev["pool_base_reserves_before"], ev["pool_quote_reserves_before"], ev["pool_base_reserves_after"], ev["pool_quote_reserves_after"]),
            (1000, 2000, 3000, 4000),
        )
        self.assertIsNone(decode_extra_event(_synthetic_post_complete_buy()[:231]))

    def test_sweeps_synthetic(self) -> None:
        pool = bytes.fromhex("82a42461e48287a5") + (1_700_000_009).to_bytes(8, "little", signed=True) + b"".join(bytes([i]) * 32 for i in range(5, 10))
        pool += (777).to_bytes(8, "little") + bytes([1])
        ev = decode_extra_event(pool)
        assert ev is not None
        self.assertEqual((ev["type"], ev["amount"], ev["bucket"]), ("sweep_pool_fee", 777, 1))
        self.assertEqual(ev["pool"], b58encode(bytes([5]) * 32))
        self.assertEqual(ev["payer"], b58encode(bytes([9]) * 32))
        curve = bytes.fromhex("742b4dbd117a482b") + (1_700_000_010).to_bytes(8, "little", signed=True) + b"".join(bytes([i]) * 32 for i in range(1, 5))
        curve += (888).to_bytes(8, "little") + bytes([0])
        ev2 = decode_extra_event(curve)
        assert ev2 is not None
        self.assertEqual((ev2["type"], ev2["amount"], ev2["bucket"]), ("sweep_curve_fee", 888, 0))
        self.assertIsNone(decode_extra_event(pool[:-1]))

    def test_boost_event_recorded(self) -> None:
        ev = decode_extra_event(_event_blob(_load("boost_buy_and_burn.json"), "3f451c16305cc2b9"))
        assert ev is not None
        self.assertEqual(ev["quote_amount_in_requested"], 768970592)
        self.assertEqual(ev["virtual_quote_reserves"], 17584505288)
        self.assertEqual(ev["boost_vault_remaining"], 16433950932)  # matches the audit's R7 dump of the same tx

    def test_synthetic_events_flow_through_rows_from_block(self) -> None:
        doc = _load("sell_v2_kept.json")
        doc["meta"]["logMessages"] = [_program_data_line(_synthetic_post_complete_buy()), _program_data_line(bytes.fromhex("6fb06d8b316cd5fb") + b"\x00" * 10)] + doc["meta"]["logMessages"]
        res = rows_from_block(_block(doc), {}, "t", event_v=True)
        types = [e["type"] for e in res["events"]]
        self.assertEqual(types, ["post_complete_buy"])
        self.assertEqual(res["bad_disc"], {"6fb06d8b316cd5fb": 1})
        self.assertEqual(res["events"][0]["signature"], doc["signature"])
        self.assertEqual(res["events"][0]["venue"], "pump_bonding")


class InitBoostTests(unittest.TestCase):
    def test_recorded_migrate_txs_carry_init_boost(self) -> None:
        res = _decode("create_pool_init_boost.json", event_v=True)
        (ev,) = res["events"]
        self.assertEqual((ev["type"], ev["source"]), ("init_boost", "inner_event"))
        self.assertEqual(ev["virtual_quote_reserves"], 17584505288)
        # the 09-20 migrate tx (pre-redeploy, full getTransaction shape) has the same self-CPI InitBoostEvent
        sept = _load("migrate_tx_sep20.json", SEPT)
        block = {"slot": sept["slot"], "blockTime": sept["blockTime"], "transactions": [{"transaction": sept["transaction"], "meta": sept["meta"]}]}
        (ev2,) = rows_from_block(block, {}, "t", event_v=True)["events"]
        self.assertEqual((ev2["type"], ev2["source"]), ("init_boost", "inner_event"))

    def test_logs_of_the_recorded_migrate_tx_are_truncated(self) -> None:
        """Why InitBoost is read from instructions: the log is cut before the event."""
        doc = _load("create_pool_init_boost.json")
        self.assertTrue(any("truncated" in line for line in doc["meta"]["logMessages"]))

    def test_instruction_fallback_when_event_cpi_missing(self) -> None:
        doc = _load("create_pool_init_boost.json")
        keys = doc["accountKeys"]
        for group in doc["meta"]["innerInstructions"]:
            group["instructions"] = [i for i in group["instructions"] if not (b58decode(i["data"]) or b"")[:8].hex() == "e445a52e51cb9a1d"]
        res = rows_from_block(_block(doc), {}, "t", event_v=True)
        (ev,) = res["events"]
        self.assertEqual(ev["source"], "instruction")
        self.assertEqual(ev["pool"], _decode("create_pool_init_boost.json", event_v=True)["events"][0]["pool"])
        self.assertIn(ev["pool"], keys)

    def test_log_only_fallback_and_none(self) -> None:
        doc = _load("create_pool_init_boost.json")
        doc["meta"]["innerInstructions"] = []
        doc["meta"]["logMessages"] = [line for line in doc["meta"]["logMessages"] if "InitBoost" not in line]
        self.assertEqual(rows_from_block(_block(doc), {}, "t", event_v=True)["events"], [])
        doc["meta"]["logMessages"] = ["Program log: Instruction: InitBoost"] + doc["meta"]["logMessages"]
        (ev,) = rows_from_block(_block(doc), {}, "t", event_v=True)["events"]
        self.assertEqual((ev["source"], ev["pool"]), ("log", None))

    def test_migration_and_complete_rows_carry_the_flag(self) -> None:
        """A migration row exists only when the logs keep CompletePumpAmmMigrationEvent. Built here (synthetic)."""
        doc = _load("create_pool_init_boost.json")
        pool = _decode("create_pool_init_boost.json", event_v=True)["events"][0]
        mig = bytearray(bytes.fromhex("bde95db95c94ea94") + b"\x00" * 192)
        mig[128:136] = (1_700_000_100).to_bytes(8, "little", signed=True)
        mig[136:168] = b58decode(pool["pool"]) or b""
        doc["meta"]["logMessages"] = [_program_data_line(bytes(mig))] + doc["meta"]["logMessages"]
        off = rows_from_block(_block(doc), {}, "t")
        on = rows_from_block(_block(doc), {}, "t", event_v=True)
        self.assertEqual([m["type"] for m in on["migrations"]], ["migration"])
        self.assertTrue(on["migrations"][0]["init_boost"])
        self.assertNotIn("init_boost", off["migrations"][0])
        stripped = {k: v for k, v in on["migrations"][0].items() if k != "init_boost"}
        self.assertEqual(stripped, off["migrations"][0])
        # a completing-buy tx has no InitBoost
        comp = _load("completing_tx_sep20.json", SEPT)
        res = rows_from_block({"slot": comp["slot"], "blockTime": comp["blockTime"], "transactions": [{"transaction": comp["transaction"], "meta": comp["meta"]}]}, {}, "t", event_v=True)
        (row,) = res["migrations"]
        self.assertEqual((row["type"], row["init_boost"]), ("complete", False))
        self.assertEqual(res["trades"][0]["ix_name"], "buy")  # bonding row


class VLawFixtureTests(unittest.TestCase):
    """The V check on recorded transactions, in lamports (integers). No price or return is computed.

    Established by the audit (R5): event V is the stored V BEFORE the trade; event pool_quote_token_reserves
    is the raw vault BEFORE the trade; a fee-keeping trade leaves protocol_fee - buyback_fee + creator_fee in the
    vault and subtracts the same amount from stored V, so vault + V changes only by the constant-product input.
    These tests re-derive that from each fixture's own pre/post token balances.
    """

    def _pairs(self):
        for name in ("boost_buy_and_burn.json", "boost_buy_and_burn_oct.json", "buy_exact_quote_in_496.json", "buy_v1_481_wsol.json", "sell_v2_kept.json"):
            doc = _load(name)
            row = _trade(name)
            yield name, doc, row, _event_blob(doc, "67f4521f2cf57777" if row["side"] == "buy" else "3e2f370aa503dc2a")

    def test_event_quote_reserve_equals_vault_prebalance(self) -> None:
        n = 0
        for name, doc, row, _blob in self._pairs():
            self.assertEqual(row["quote_reserve"], _vault(doc, row["pool"], "preTokenBalances"), name)
            n += 1
        self.assertEqual(n, 5)

    def test_fee_keeping_leaves_vault_plus_v_changed_by_cp_input_only(self) -> None:
        """delta_vault - cp_input == kept, kept = protocol - buyback + creator if the fee recipient is zero else 0.

        Covered: fee-free boost buys, a paid v1 buy, a kept v2 sell (all exact, deviation 0 lamports).
        Not covered: the buy_exact_quote_in v1 fixture, whose quote_amount_in is net of protocol and creator fee
        (field semantics differ); it is checked for Q == vault above and for V decode in the CP test below.
        """
        covered = {}
        for name, doc, row, blob in self._pairs():
            if name == "buy_exact_quote_in_496.json":
                continue
            dv = _vault(doc, row["pool"], "postTokenBalances") - _vault(doc, row["pool"], "preTokenBalances")
            u64 = lambda off: int.from_bytes(blob[off : off + 8], "little")  # noqa: E731
            qty, lp_fee, protocol_fee, creator_fee = u64(64), u64(80), u64(96), u64(352)
            cp_input = (qty + lp_fee) if row["side"] == "buy" else -(qty - lp_fee)
            kept = protocol_fee - row["buyback_fee"] + creator_fee if row["fee_recipient_zero"] else 0
            covered[name] = (dv - cp_input) - kept
            self.assertEqual(dv - cp_input, kept, name)
            if name == "sell_v2_kept.json":
                self.assertEqual(kept, 87303)
        self.assertEqual(covered, {"boost_buy_and_burn.json": 0, "boost_buy_and_burn_oct.json": 0, "buy_v1_481_wsol.json": 0, "sell_v2_kept.json": 0})

    def test_constant_product_uses_vault_plus_event_v(self) -> None:
        """The decoded V sits at the right offset: sell and v1-buy amounts are exact with Q+V and wrong with Q alone."""
        sell = _trade("sell_v2_kept.json")
        blob = _event_blob(_load("sell_v2_kept.json"), "3e2f370aa503dc2a")
        gross = int.from_bytes(blob[64:72], "little")
        q, b, v, base_in = sell["quote_reserve"], sell["base_reserve"], sell["virtual_quote_reserves"], sell["token_raw"]
        self.assertEqual(gross - (q + v) * base_in // (b + base_in), 0)
        self.assertNotEqual(gross - q * base_in // (b + base_in), 0)
        buy = _trade("buy_v1_481_wsol.json")
        blob = _event_blob(_load("buy_v1_481_wsol.json"), "67f4521f2cf57777")
        qin = int.from_bytes(blob[64:72], "little")
        q, b, v = buy["quote_reserve"], buy["base_reserve"], buy["virtual_quote_reserves"]
        self.assertEqual(buy["token_raw"] - b * qin // (q + v + qin), 0)
        self.assertNotEqual(buy["token_raw"] - b * qin // (q + qin), 0)

    def test_buy_exact_quote_in_residuals_stay_tiny_with_v(self) -> None:
        """Boost buys (buy_exact_quote_in): base_out vs b*qin//(q+v+qin) is within 315 base units, ~1.5e-9 of the output."""
        worst = 0.0
        for name in ("boost_buy_and_burn.json", "boost_buy_and_burn_oct.json"):
            row = _trade(name)
            blob = _event_blob(_load(name), "67f4521f2cf57777")
            qin = int.from_bytes(blob[64:72], "little")
            model = row["base_reserve"] * qin // (row["quote_reserve"] + row["virtual_quote_reserves"] + qin)
            resid = row["token_raw"] - model
            self.assertLessEqual(abs(resid), 315, name)
            worst = max(worst, abs(resid) / row["token_raw"])
        self.assertLess(worst, 2e-9)


class RunHourEventVTests(unittest.TestCase):
    """run_hour end to end with --event-v: events stream sealed, legacy streams identical, counts recorded."""

    KEY = "2026-09-11T08"

    def setUp(self) -> None:
        self._saved = (bf.slots_between, bf.fetch_block, bf.fetch_pool_mints)
        self.addCleanup(lambda: (setattr(bf, "slots_between", self._saved[0]), setattr(bf, "fetch_block", self._saved[1]), setattr(bf, "fetch_pool_mints", self._saved[2])))
        self.blocks = [b for b in fixture_blocks()]
        n = len(self.blocks)
        from tools.test_pump_history_backfill import _hour_bounds

        self.start_ts, self.end_ts = _hour_bounds(self.KEY)
        bf.slots_between = lambda *a, **k: list(range(n))
        bf.fetch_pool_mints = lambda url, pools: {p: ("BaseMint", WSOL_MINT) for p in pools}

        def fake_fetch(url, slot, limiter, budget=None, header_out=None, *, full=True):
            block = copy.deepcopy(self.blocks[slot])
            block["slot"] = 1000 + slot
            block["blockTime"] = self.start_ts + slot
            return block, 1, None

        bf.fetch_block = fake_fetch

    def _run(self, out: Path, event_v: bool) -> dict:
        lim = RateLimiter(1000)
        return run_hour(
            url="http://x", start_ts=self.start_ts, end_ts=self.end_ts, out_dir=out, limiter=lim, lookup_limiter=lim, pool_mints={},
            workers=1, max_bytes=10**9, anchor_slot=450278777, anchor_time=1790319576, slot_start=0, slot_end=len(self.blocks),
            min_slots_per_hour=1, max_slots_per_hour=1000, budget=CreditBudget(10**9, 0), checkpoint=None, checkpoint_path=None,
            event_v=event_v,
        )

    def test_events_stream_and_legacy_streams(self) -> None:
        with tempfile.TemporaryDirectory() as t_off, tempfile.TemporaryDirectory() as t_on:
            off, on = Path(t_off), Path(t_on)
            s_off = self._run(off, False)
            s_on = self._run(on, True)
            self.assertNotIn("event_v", s_off)
            self.assertNotIn("events", s_off["files"])
            self.assertFalse((off / "events").exists())
            self.assertTrue(s_on["event_v"])
            self.assertTrue((on / "events" / f"events-{self.KEY}.jsonl.zst").is_file())
            kinds = sorted(r["type"] for r in iter_jsonl(on / "events" / f"events-{self.KEY}.jsonl.zst"))
            # two boost txs, plus InitBoost in the 10-08 pool creation and in the 09-20 migrate tx
            self.assertEqual(kinds, ["boost_buy_and_burn", "boost_buy_and_burn", "init_boost", "init_boost"])
            self.assertEqual(s_on["event_rows"], 4)
            self.assertGreater(sum(s_on["unknown_disc"].values()), 0)
            self.assertEqual(s_on["bad_disc"], {"3e2f370aa503dc2a": 1, "67f4521f2cf57777": 1})
            for sub in ("trades", "creates", "migrations"):
                a = [r for r in iter_jsonl(off / sub / f"{sub}-{self.KEY}.jsonl.zst")] if (off / sub / f"{sub}-{self.KEY}.jsonl.zst").exists() else []
                b = [r for r in iter_jsonl(on / sub / f"{sub}-{self.KEY}.jsonl.zst")] if (on / sub / f"{sub}-{self.KEY}.jsonl.zst").exists() else []
                strip = lambda rows: [{k: v for k, v in r.items() if k not in NEW_ROW_KEYS} for r in rows]  # noqa: E731
                self.assertEqual(strip(b), a, sub)
                self.assertEqual(len(a), len(b))
            for key in s_off:
                if key in ("elapsed_s", "files", "bytes"):
                    continue
                self.assertEqual(s_on[key], s_off[key], key)


if __name__ == "__main__":
    unittest.main()
