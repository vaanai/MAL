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
    ANCHOR_EVENT_IX_TAG,
    EVENT_V_KEYS,
    PUMP_BONDING_PROGRAM,
    PUMPSWAP_PROGRAM,
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
                # rows only event_v can produce (migration recovered from a self-CPI) are additions, not legacy rows
                stripped = [{k: v for k, v in r.items() if k not in NEW_ROW_KEYS} for r in on[bucket] if r.get("event_source") != "inner_event"]
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
        self.assertEqual((ev["type"], ev["event_source"]), ("init_boost", "inner_event"))
        self.assertEqual(ev["virtual_quote_reserves"], 17584505288)
        # the 09-20 migrate tx (pre-redeploy, full getTransaction shape) has the same self-CPI InitBoostEvent
        sept = _load("migrate_tx_sep20.json", SEPT)
        block = {"slot": sept["slot"], "blockTime": sept["blockTime"], "transactions": [{"transaction": sept["transaction"], "meta": sept["meta"]}]}
        (ev2,) = rows_from_block(block, {}, "t", event_v=True)["events"]
        self.assertEqual((ev2["type"], ev2["event_source"]), ("init_boost", "inner_event"))

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
        self.assertEqual(ev["event_source"], "instruction")
        self.assertEqual(ev["pool"], _decode("create_pool_init_boost.json", event_v=True)["events"][0]["pool"])
        self.assertIn(ev["pool"], keys)

    def test_log_only_fallback_and_none(self) -> None:
        doc = _load("create_pool_init_boost.json")
        doc["meta"]["innerInstructions"] = []
        doc["meta"]["logMessages"] = [line for line in doc["meta"]["logMessages"] if "InitBoost" not in line]
        self.assertEqual(rows_from_block(_block(doc), {}, "t", event_v=True)["events"], [])
        doc["meta"]["logMessages"] = ["Program log: Instruction: InitBoost"] + doc["meta"]["logMessages"]
        (ev,) = rows_from_block(_block(doc), {}, "t", event_v=True)["events"]
        self.assertEqual((ev["event_source"], ev["pool"]), ("log_line", None))

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
        self.assertEqual(on["migrations"][0]["event_source"], "log")
        self.assertEqual(on["migrations"][0]["source"], "backfill")  # the legacy `source` key keeps its value
        stripped = {k: v for k, v in on["migrations"][0].items() if k not in ("init_boost", "event_source")}
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

    def _run(self, out: Path, event_v: bool, *, checkpoint=None, checkpoint_path=None, max_slots=None) -> dict:
        lim = RateLimiter(1000)
        return run_hour(
            url="http://x", start_ts=self.start_ts, end_ts=self.end_ts, out_dir=out, limiter=lim, lookup_limiter=lim, pool_mints={},
            workers=1, max_bytes=10**9, anchor_slot=450278777, anchor_time=1790319576, slot_start=0, slot_end=len(self.blocks),
            min_slots_per_hour=1, max_slots_per_hour=1000, budget=CreditBudget(10**9, 0), checkpoint=checkpoint, checkpoint_path=checkpoint_path,
            event_v=event_v, max_slots=max_slots,
        )

    @staticmethod
    def _snapshot(root: Path) -> dict[str, bytes]:
        return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}

    def _toggle_case(self, first: bool, second: bool) -> None:
        from tools.pump_history_backfill import empty_checkpoint

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            ckpt_path = out / "checkpoint.json"
            ckpt = empty_checkpoint()
            part = self._run(out, first, checkpoint=ckpt, checkpoint_path=ckpt_path, max_slots=3)
            self.assertEqual(ckpt["hours"][self.KEY]["status"], "partial", part)
            before, ckpt_before = self._snapshot(out), json.dumps(ckpt, sort_keys=True)
            with self.assertRaises(bf.EventVResumeMismatch) as cm:
                self._run(out, second, checkpoint=ckpt, checkpoint_path=ckpt_path)
            self.assertIn(f"event_v={first}", str(cm.exception))
            self.assertIn(f"event_v={second}", str(cm.exception))
            self.assertEqual(self._snapshot(out), before)  # files untouched
            self.assertEqual(json.dumps(ckpt, sort_keys=True), ckpt_before)  # checkpoint untouched
            # same flag resumes and finishes
            done = self._run(out, first, checkpoint=ckpt, checkpoint_path=ckpt_path)
            self.assertIsNone(done["stop_reason"])
            self.assertEqual(ckpt["hours"][self.KEY]["status"], "sealed")

    def test_resume_with_event_v_turned_on_is_refused(self) -> None:
        self._toggle_case(first=False, second=True)

    def test_resume_with_event_v_turned_off_is_refused(self) -> None:
        self._toggle_case(first=True, second=False)

    def test_main_exits_3_on_toggle(self) -> None:
        import contextlib
        import io

        def boom(**kwargs):
            raise bf.EventVResumeMismatch("backfill X: event_v mismatch")

        orig = bf.run_hour
        bf.run_hour = boom
        self.addCleanup(setattr, bf, "run_hour", orig)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            bf._run_hour_exit3(url="x")
        self.assertEqual(cm.exception.code, 3)
        self.assertIn("event_v mismatch", err.getvalue())

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
                strip = lambda rows: [{k: v for k, v in r.items() if k not in NEW_ROW_KEYS} for r in rows if r.get("event_source") != "inner_event"]  # noqa: E731
                self.assertEqual(strip(b), a, sub)
                # the only extra rows are migration rows recovered from self-CPIs of the two migrate fixtures
                self.assertEqual(len(b) - len(a), 2 if sub == "migrations" else 0, sub)
            for key in s_off:
                if key in ("elapsed_s", "files", "bytes"):
                    continue
                expect = s_off[key] + 2 if key == "migrations" else s_off[key]
                self.assertEqual(s_on[key], expect, key)
            self.assertEqual(s_on["cpi_disc"].get("bde95db95c94ea94"), 2)
            self.assertEqual(s_on["event_types"], {"boost_buy_and_burn": 2, "init_boost": 2})
            self.assertIs(s_on["post_complete_buy_missing"], False)


def _tx_from(doc: dict, base: Path) -> dict:
    """A getBlock-shaped tx from either fixture format (trimmed walk2_event_v, or full pump_structure_monitor)."""
    doc = copy.deepcopy(doc)  # tests mutate the tx; never share nested dicts with the loaded fixture
    if "accountKeys" in doc:
        return {"transaction": {"signatures": [doc["signature"]], "message": {"accountKeys": doc["accountKeys"], "instructions": []}}, "meta": doc["meta"]}
    return {"transaction": doc["transaction"], "meta": doc["meta"]}


def _blk(doc: dict, tx: dict) -> dict:
    return {"slot": doc["slot"], "blockTime": doc["blockTime"], "transactions": [tx]}


def _add_cpi(tx: dict, program: str, blob: bytes) -> None:
    """Append an Anchor emit_cpi! self-CPI (tag + event blob) as an inner instruction of `program`."""
    keys = tx["transaction"]["message"]["accountKeys"]
    ins = {"programIdIndex": keys.index(program), "accounts": [], "data": b58encode(ANCHOR_EVENT_IX_TAG + blob)}
    tx["meta"].setdefault("innerInstructions", []).append({"index": 0, "instructions": [ins]})


MIGRATE_DISC_HEX = "bde95db95c94ea94"
PCB_HEX = "6fb06d8b316cd5fb"
MIGRATE_FIXTURES = ((FIX, "create_pool_init_boost.json"), (SEPT, "migrate_tx_sep20.json"))


class CpiRecoveryTests(unittest.TestCase):
    """Self-CPI events (tag e445a52e51cb9a1d + disc + payload) of pump / pump_amm, read from inner instructions."""

    def test_migration_row_recovered_from_cpi_on_both_recorded_migrate_txs(self) -> None:
        for base, name in MIGRATE_FIXTURES:
            doc = _load(name, base)
            blk = _blk(doc, _tx_from(doc, base))
            off = rows_from_block(blk, {}, "t")
            on = rows_from_block(blk, {}, "t", event_v=True)
            self.assertEqual(off["migrations"], [], name)  # legacy walks lose it: the log is cut before the event
            self.assertTrue(any("truncated" in line for line in doc["meta"]["logMessages"]), name)
            (row,) = on["migrations"]
            self.assertEqual((row["type"], row["event_source"], row["init_boost"], row["source"]), ("migration", "inner_event", True, "backfill"), name)
            (ib,) = [e for e in on["events"] if e["type"] == "init_boost"]
            self.assertEqual(row["pool"], ib["pool"], name)
            self.assertEqual(row["sol_lamports"] > 0 and row["migration_fee"] > 0, True, name)
            # same legacy fields the log decoder would have produced from that blob
            blob = [b for b in bf.cpi_events_from_tx(_tx_from(doc, base)) if b[:8].hex() == MIGRATE_DISC_HEX][0]
            expect = bf.decode_migration_event(blob)
            for key, value in expect.items():
                self.assertEqual(row[key], value, (name, key))
            # the other legacy buckets are untouched
            for bucket in ("trades", "creates", "unresolved"):
                self.assertEqual(on[bucket], off[bucket], (name, bucket))

    def test_cpi_counters(self) -> None:
        doc = _load("create_pool_init_boost.json")
        on = rows_from_block(_blk(doc, _tx_from(doc, FIX)), {}, "t", event_v=True)
        self.assertEqual(on["cpi_disc"], {"b1310cd2a076a774": 1, "ae7c4af90451f611": 1, MIGRATE_DISC_HEX: 1})
        # CreatePoolEvent is in the log too; the migration and InitBoost events are only in the CPIs
        self.assertEqual(on["cpi_only_disc"], {"ae7c4af90451f611": 1, MIGRATE_DISC_HEX: 1})
        off = rows_from_block(_blk(doc, _tx_from(doc, FIX)), {}, "t")
        self.assertNotIn("cpi_disc", off)

    def test_event_in_both_log_and_cpi_is_one_row_from_the_log(self) -> None:
        doc = _load("create_pool_init_boost.json")
        tx = _tx_from(doc, FIX)
        blob = [b for b in bf.cpi_events_from_tx(tx) if b[:8].hex() == MIGRATE_DISC_HEX][0]
        tx["meta"]["logMessages"] = [_program_data_line(blob)] + tx["meta"]["logMessages"]
        on = rows_from_block(_blk(doc, tx), {}, "t", event_v=True)
        (row,) = on["migrations"]
        self.assertEqual((row["event_source"], row["init_boost"]), ("log", True))
        self.assertNotIn(MIGRATE_DISC_HEX, on["cpi_only_disc"])
        # a log migration row that differs from the CPI one still wins: one row per tx
        other = bytearray(blob)
        other[88:96] = (1).to_bytes(8, "little")  # migration_fee differs
        tx2 = _tx_from(doc, FIX)
        tx2["meta"]["logMessages"] = [_program_data_line(bytes(other))] + tx2["meta"]["logMessages"]
        on2 = rows_from_block(_blk(doc, tx2), {}, "t", event_v=True)
        self.assertEqual([(m["event_source"], m["migration_fee"]) for m in on2["migrations"]], [("log", 1)])

    def test_other_events_from_cpi_when_absent_from_logs_and_deduplicated(self) -> None:
        pool_sweep = bytes.fromhex("82a42461e48287a5") + (1_700_000_009).to_bytes(8, "little", signed=True) + b"".join(bytes([i]) * 32 for i in range(5, 10)) + (777).to_bytes(8, "little") + bytes([1])
        doc = _load("create_pool_init_boost.json")
        tx = _tx_from(doc, FIX)
        _add_cpi(tx, PUMPSWAP_PROGRAM, pool_sweep)
        _add_cpi(tx, PUMP_BONDING_PROGRAM, _synthetic_post_complete_buy())
        on = rows_from_block(_blk(doc, tx), {}, "t", event_v=True)
        by_type = {e["type"]: e for e in on["events"]}
        self.assertEqual(sorted(by_type), ["init_boost", "post_complete_buy", "sweep_pool_fee"])
        self.assertEqual(by_type["sweep_pool_fee"]["event_source"], "inner_event")
        self.assertEqual(by_type["post_complete_buy"]["event_source"], "inner_event")
        self.assertEqual(on["cpi_disc"][PCB_HEX], 1)
        # the same two events also in the logs: still one row each, now from the log
        tx2 = _tx_from(doc, FIX)
        _add_cpi(tx2, PUMPSWAP_PROGRAM, pool_sweep)
        _add_cpi(tx2, PUMP_BONDING_PROGRAM, _synthetic_post_complete_buy())
        tx2["meta"]["logMessages"] = [_program_data_line(pool_sweep), _program_data_line(_synthetic_post_complete_buy())] + tx2["meta"]["logMessages"]
        on2 = rows_from_block(_blk(doc, tx2), {}, "t", event_v=True)
        self.assertEqual(sorted(e["type"] for e in on2["events"]), ["init_boost", "post_complete_buy", "sweep_pool_fee"])
        self.assertEqual({e["type"]: e["event_source"] for e in on2["events"]}["post_complete_buy"], "log")

    def test_flag_off_ignores_cpi_events_entirely(self) -> None:
        doc = _load("create_pool_init_boost.json")
        tx = _tx_from(doc, FIX)
        _add_cpi(tx, PUMP_BONDING_PROGRAM, _synthetic_post_complete_buy())
        off = rows_from_block(_blk(doc, tx), {}, "t")
        self.assertEqual(set(off), {"trades", "creates", "migrations", "unresolved"})
        self.assertEqual(off["migrations"], [])

    def test_post_complete_buy_detector(self) -> None:
        """Rule: PostCompleteBuy (6fb06d8b316cd5fb) in cpi_disc while events/ holds 0 post_complete_buy rows = missing."""
        self.assertTrue(bf.post_complete_buy_missing({"cpi_disc": {PCB_HEX: 3}, "event_types": {"init_boost": 2}}))
        self.assertFalse(bf.post_complete_buy_missing({"cpi_disc": {PCB_HEX: 3}, "event_types": {"post_complete_buy": 3}}))
        self.assertFalse(bf.post_complete_buy_missing({"cpi_disc": {}, "event_types": {}}))
        self.assertFalse(bf.post_complete_buy_missing({}))
        doc = _load("create_pool_init_boost.json")
        # a decodable CPI event: rows exist, detector quiet
        tx = _tx_from(doc, FIX)
        _add_cpi(tx, PUMP_BONDING_PROGRAM, _synthetic_post_complete_buy())
        res = rows_from_block(_blk(doc, tx), {}, "t", event_v=True)
        stats = {"cpi_disc": res["cpi_disc"], "event_types": {t: sum(1 for e in res["events"] if e["type"] == t) for t in {e["type"] for e in res["events"]}}}
        self.assertFalse(bf.post_complete_buy_missing(stats))
        # a CPI event the decoder cannot read (short blob): seen but no row -> detector fires, and bad_disc counts it
        tx = _tx_from(doc, FIX)
        _add_cpi(tx, PUMP_BONDING_PROGRAM, _synthetic_post_complete_buy()[:100])
        res = rows_from_block(_blk(doc, tx), {}, "t", event_v=True)
        stats = {"cpi_disc": res["cpi_disc"], "event_types": {t: sum(1 for e in res["events"] if e["type"] == t) for t in {e["type"] for e in res["events"]}}}
        self.assertTrue(bf.post_complete_buy_missing(stats))
        self.assertEqual(res["bad_disc"], {PCB_HEX: 1})


class ValidationTests(unittest.TestCase):
    def _buy_blob(self) -> bytearray:
        return bytearray(_event_blob(_load("buy_v1_481_wsol.json"), "67f4521f2cf57777"))

    def test_ix_name_must_be_printable_ascii_1_to_64(self) -> None:
        good = self._buy_blob()
        self.assertEqual(event_v_fields(bytes(good))["ix_name"], "buy")
        for label, mutate in (
            ("control char", lambda b: b.__setitem__(slice(405, 408), b"b\x01y")),
            ("non-ascii", lambda b: b.__setitem__(slice(405, 408), "bü".encode()[:3])),
            ("length 0", lambda b: b.__setitem__(slice(401, 405), (0).to_bytes(4, "little"))),
            ("length 65", lambda b: b.__setitem__(slice(401, 405), (65).to_bytes(4, "little"))),
        ):
            blob = self._buy_blob()
            mutate(blob)
            self.assertEqual(event_v_fields(bytes(blob)), {}, label)

    def test_bad_ix_name_is_counted_and_legacy_row_survives(self) -> None:
        doc = _load("buy_v1_481_wsol.json")
        blob = self._buy_blob()
        blob[405:408] = b"b\x01y"
        tx = _tx_from(doc, FIX)
        tx["meta"]["logMessages"] = [
            _program_data_line(bytes(blob)) if "Program data: " in line and base64.b64decode(line.split("Program data: ", 1)[1].strip())[:8].hex() == "67f4521f2cf57777" else line
            for line in tx["meta"]["logMessages"]
        ]
        on = rows_from_block(_blk(doc, tx), {"x": ("m", WSOL_MINT)}, "t", event_v=True)
        off = rows_from_block(_blk(doc, tx), {"x": ("m", WSOL_MINT)}, "t")
        self.assertEqual(on["bad_disc"], {"67f4521f2cf57777": 1})
        rows = on["trades"] + on["unresolved"]
        self.assertEqual(len(rows), 1)
        self.assertFalse(set(rows[0]) & set(EVENT_V_KEYS))
        self.assertEqual(len(off["trades"] + off["unresolved"]), 1)

    def test_extra_event_timestamp_sanity(self) -> None:
        good = _synthetic_post_complete_buy()
        self.assertIsNotNone(decode_extra_event(good))
        for ts in (5, 2_000_000_000, -1):
            bad = bytearray(good)
            bad[136:144] = ts.to_bytes(8, "little", signed=True)
            self.assertIsNone(decode_extra_event(bytes(bad)), ts)
        sweep = bytes.fromhex("82a42461e48287a5") + (5).to_bytes(8, "little", signed=True) + b"\x01" * 32 * 5 + (1).to_bytes(8, "little") + b"\x00"
        self.assertIsNone(decode_extra_event(sweep))
        doc = _load("create_pool_init_boost.json")
        tx = _tx_from(doc, FIX)
        tx["meta"]["logMessages"] = [_program_data_line(sweep)] + tx["meta"]["logMessages"]
        res = rows_from_block(_blk(doc, tx), {}, "t", event_v=True)
        self.assertEqual(res["bad_disc"], {"82a42461e48287a5": 1})


if __name__ == "__main__":
    unittest.main()
