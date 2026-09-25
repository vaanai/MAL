"""Attention poller: first-seen, parsers, backoff. No network."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from observe.attention import (
    Candidate,
    FirstSeenIndex,
    HttpJson,
    TinyDiskHold,
    attention_record_from_candidate,
    backoff_s,
    is_solana_mint,
    parse_dex_list,
    parse_dex_orders,
    parse_gecko_trending,
    parse_pump_graduating,
    parse_pump_hot,
    parse_pump_list,
    parse_time_ms,
    stored_attention,
)
from observe.trade_store import hour_stamp


class _Resp:
    def __init__(self, body: bytes, status: int = 200, headers: dict | None = None):
        self.status = status
        self.headers = headers or {}
        self._body = BytesIO(body)

    def read(self) -> bytes:
        return self._body.read()

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class ParseTests(unittest.TestCase):
    def test_dex_list_keeps_solana_drops_evm(self) -> None:
        payload = [
            {
                "chainId": "solana",
                "tokenAddress": "CvGrKzmaonHfXNTtjRx17Wq94LHhJJVdNKAAbbnNpump",
                "amount": 30,
                "totalAmount": 90,
                "links": [{"type": "twitter", "url": "https://x.com/x"}],
            },
            {
                "chainId": "ethereum",
                "tokenAddress": "0xabc",
                "amount": 10,
            },
        ]
        rows = parse_dex_list(payload, kind="dex_boost", source="dex_boosts_latest")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].mint, "CvGrKzmaonHfXNTtjRx17Wq94LHhJJVdNKAAbbnNpump")
        self.assertEqual(rows[0].rank, 0)
        self.assertEqual(rows[0].extra["amount"], 30)
        self.assertEqual(rows[0].extra["links"][0]["type"], "twitter")

    def test_dex_orders_token_profile_paid_at(self) -> None:
        payload = {
            "orders": [
                {
                    "chainId": "solana",
                    "tokenAddress": "CvGrKzmaonHfXNTtjRx17Wq94LHhJJVdNKAAbbnNpump",
                    "type": "tokenProfile",
                    "status": "approved",
                    "paymentTimestamp": 1790349283329,
                }
            ],
            "boosts": [],
        }
        rows = parse_dex_orders(payload, "CvGrKzmaonHfXNTtjRx17Wq94LHhJJVdNKAAbbnNpump")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].kind, "dex_paid_profile")
        self.assertEqual(rows[0].paid_at_ms, 1790349283329)

    def test_graduating_rank0_is_koth(self) -> None:
        payload = {
            "board": "graduating",
            "coins": [
                {"mint": "Ham46f8JY1CmXqerYPJAZGcXnNhW9LGbuUKT24PKpump", "name": "A", "reply_count": 3},
                {"mint": "GB3PUAEjgC4gSj39D8XRrNKc4zjYb72dLzGyfDQ1pump", "name": "B", "reply_count": 1},
            ],
        }
        rows = parse_pump_graduating(payload)
        self.assertEqual([r.kind for r in rows], ["pump_koth", "pump_graduating"])
        self.assertEqual(rows[0].rank, 0)
        self.assertEqual(rows[0].extra["reply_count"], 3)

    def test_hot_coin_null_is_empty(self) -> None:
        self.assertEqual(parse_pump_hot({"hotCoin": None}), [])

    def test_hot_coin_present(self) -> None:
        payload = {
            "hotCoin": {
                "coin": {
                    "mint": "Ham46f8JY1CmXqerYPJAZGcXnNhW9LGbuUKT24PKpump",
                    "name": "King",
                    "twitter": "https://x.com/k",
                },
                "expiresAt": 1790349999000,
            }
        }
        rows = parse_pump_hot(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].kind, "pump_koth")
        self.assertEqual(rows[0].extra["expires_at"], 1790349999000)

    def test_live_list_keeps_socials(self) -> None:
        payload = [
            {
                "mint": "42pHP3TzLVX7Egx8zBwidnAFB4vUR9tzGgZssyGspump",
                "name": "Live",
                "is_currently_live": True,
                "num_participants": 15,
                "telegram": "https://t.me/x",
                "website": "https://example.com",
            }
        ]
        rows = parse_pump_list(payload, kind="pump_live", source="pump_currently_live")
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].extra["is_currently_live"])
        self.assertEqual(rows[0].extra["num_participants"], 15)
        self.assertEqual(rows[0].extra["telegram"], "https://t.me/x")

    def test_gecko_maps_base_mint(self) -> None:
        payload = {
            "data": [
                {
                    "relationships": {
                        "base_token": {"data": {"id": "solana_EpEfnZxQyiBXppSKi8sncc8w4corn1UJbF9G91fQpump"}}
                    },
                    "attributes": {"name": "FOO / SOL", "address": "Pool111", "reserve_in_usd": "100"},
                }
            ],
            "included": [
                {
                    "id": "solana_EpEfnZxQyiBXppSKi8sncc8w4corn1UJbF9G91fQpump",
                    "type": "token",
                    "attributes": {"address": "EpEfnZxQyiBXppSKi8sncc8w4corn1UJbF9G91fQpump"},
                }
            ],
        }
        rows = parse_gecko_trending(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].mint, "EpEfnZxQyiBXppSKi8sncc8w4corn1UJbF9G91fQpump")
        self.assertEqual(rows[0].kind, "gecko_trending")

    def test_skips_wsol(self) -> None:
        self.assertFalse(is_solana_mint("So11111111111111111111111111111111111111112"))
        self.assertFalse(is_solana_mint("0xabc"))


class FirstSeenTests(unittest.TestCase):
    def test_records_once_per_kind_mint(self) -> None:
        idx = FirstSeenIndex()
        cand = Candidate(mint="Abc111111111111111111111111111111111111pump", kind="dex_boost", source="x")
        a = attention_record_from_candidate(cand, idx, 1000)
        b = attention_record_from_candidate(cand, idx, 2000)
        self.assertIsNotNone(a)
        self.assertIsNone(b)
        self.assertEqual(a["t_first_ms"], 1000)
        self.assertEqual(a["mint"], cand.mint)
        self.assertEqual(a["v"], 1)
        other = Candidate(mint=cand.mint, kind="pump_live", source="y")
        c = attention_record_from_candidate(other, idx, 3000)
        self.assertIsNotNone(c)
        self.assertEqual(c["kind"], "pump_live")

    def test_reload_keeps_earlier_stamp(self) -> None:
        idx = FirstSeenIndex()
        idx.load_record({"kind": "dex_boost", "mint": "Mint1111111111111111111111111111111111111pump", "t_first_ms": 50})
        idx.load_record({"kind": "dex_boost", "mint": "Mint1111111111111111111111111111111111111pump", "t_first_ms": 90})
        self.assertEqual(idx.seen[("dex_boost", "Mint1111111111111111111111111111111111111pump")], 50)

    def test_stored_row_is_tiny(self) -> None:
        rec = stored_attention(
            Candidate(
                mint="Mint1111111111111111111111111111111111111pump",
                kind="dex_profile",
                source="dex_profiles_latest",
                extra={"description": "nope"},
            ),
            10,
            10,
        )
        line = json.dumps(rec, separators=(",", ":"))
        self.assertLess(len(line), 400)
        self.assertNotIn("header", rec)
        self.assertNotIn("icon", rec)


class BackoffTests(unittest.TestCase):
    def test_retry_after_wins(self) -> None:
        self.assertEqual(backoff_s(9, 7.0), 7.0)

    def test_grows_and_caps(self) -> None:
        with patch("observe.attention.random.random", return_value=0.0):
            self.assertEqual(backoff_s(1), 5.0)
            self.assertEqual(backoff_s(2), 10.0)
            self.assertEqual(backoff_s(10), 120.0)

    def test_http_json_ok(self) -> None:
        body = json.dumps([{"ok": 1}]).encode()

        def opener(req, timeout=0, context=None):
            ua = req.get_header("User-agent") or ""
            self.assertIn("MAL-paper-attention", ua)
            return _Resp(body)

        client = HttpJson(opener=opener)
        self.assertEqual(client.get("https://example.invalid/x"), [{"ok": 1}])


class DiskAndStampTests(unittest.TestCase):
    def test_hour_stamp_matches_writer_prefix(self) -> None:
        stamp = hour_stamp(datetime(2026, 9, 25, 15, tzinfo=timezone.utc))
        self.assertEqual(stamp, "2026-09-25T15")

    def test_hold_flips_on_low_free(self) -> None:
        with TemporaryDirectory() as tmp:
            hold = TinyDiskHold(Path(tmp))
            with patch("observe.attention.filesystem_bytes", return_value=(100, 90, 10)):
                hold.tick()
            self.assertTrue(hold.holding)
            with patch("observe.attention.filesystem_bytes", return_value=(100, 70, 30)):
                hold.tick()
            self.assertFalse(hold.holding)

    def test_parse_time_iso_and_ms(self) -> None:
        self.assertEqual(parse_time_ms(1790349283329), 1790349283329)
        dt = datetime(2026, 9, 25, 14, 2, 20, 85000, tzinfo=timezone.utc)
        self.assertEqual(parse_time_ms("2026-09-25T14:02:20.085Z"), int(dt.timestamp() * 1000))


if __name__ == "__main__":
    unittest.main()
