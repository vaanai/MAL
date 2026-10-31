"""Offline tests for the bridge's DEC-032 s3 "Venue" pre-send mint screen (tools/arena_live_bridge.py: OrderPort.mint_screen_problem, Bridge.mint_screen_refusal,
the live port's screen over h5_arena_executor.mint_screen_record + arena_close_blocked.classify_record). Synthetic mint bytes, the probe tests' fake RPC, a throwaway
Keypair; no network, no key, no decision row of any book."""

from __future__ import annotations

import base64
import struct
import unittest
from pathlib import Path
from unittest import mock

from tools import arena_live_bridge as alb
from tools import mint_account_fetch as mf
from tools.test_arena_live_bridge import Env, StubPort, TmpCase

ALLOW = (18, 19)  # metadataPointer, tokenMetadata
# Every Token-2022 mint extension type outside the allowlist (spl-token-2022 ExtensionType), plus an id no parser knows.
OUTSIDE = (1, 3, 4, 6, 9, 10, 12, 14, 16, 20, 21, 22, 23, 24, 25, 26, 40)


def mint_value(owner: str = mf.SPL_TOKEN_PROGRAM, freeze: bool = False, ext_types: tuple = ()) -> dict:
    """A getAccountInfo value for a mint: SPL layout (82 bytes) or Token-2022 with TLV extension types (same shape as tools/test_h5_arena_executor.mint_value)."""
    raw = bytearray(82)
    raw[45] = 1
    if freeze:
        struct.pack_into("<I", raw, 46, 1)
        raw[50:82] = bytes([7]) * 32
    if owner == mf.TOKEN_2022_PROGRAM:
        raw = bytearray(raw) + bytearray(165 - 82) + bytearray([1])
        for t in ext_types:
            raw += struct.pack("<HH", t, 4) + bytes(4)
    return {"data": [base64.b64encode(bytes(raw)).decode(), "base64"], "owner": owner, "lamports": 1, "executable": False}


class ScreenPort(StubPort):
    """StubPort whose screen records each call and can raise or advance the clock."""

    def __init__(self, *a, clock=None, advance_ms: int = 0, boom: bool = False, **kw) -> None:
        super().__init__(*a, **kw)
        self.screened: list[str] = []
        self.clock, self.advance_ms, self.boom = clock, advance_ms, boom

    def mint_screen_problem(self, mint):
        self.screened.append(mint)
        if self.clock is not None:
            self.clock.t += self.advance_ms  # the read takes this long
        if self.boom:
            raise RuntimeError("rpc down")
        return self.screen


class BridgeScreenTests(TmpCase):
    def env(self, **port_kw) -> Env:
        e = Env(self.tmp)
        e.port = ScreenPort(**port_kw)
        e.start()
        return e

    def test_a_blocked_mint_is_refused_before_the_write_ahead(self):
        e = self.env()
        e.port.screen = "close_blocked_mint:extension_outside_allowlist"
        e.entry("o1", mint="M1")
        b = e.bridge()
        b.tick()
        self.assertEqual(e.port.calls, [])  # nothing sent, dry or live
        self.assertEqual(e.port.screened, ["M1"])
        self.assertNotIn("o1", b.state.orders)  # refused before the write-ahead: no order, no attempt, the mint is not marked bought
        self.assertNotIn("M1", b.state.bought)
        self.assertEqual(b.state.attempts, 0)
        self.assertEqual(b.state.counts.get("refused_close_blocked_mint"), 1)
        self.assertEqual([r["why"] for r in e.log_rows("entry_refused")], ["close_blocked_mint:extension_outside_allowlist"])

    def test_a_clear_mint_is_bought(self):
        e = self.env()
        e.entry("o1", mint="M1")
        e.bridge().tick()
        self.assertEqual([c[:3] for c in e.port.calls], [("buy", "o1", "M1")])
        self.assertEqual(e.port.screened, ["M1"])

    def test_a_raising_screen_refuses(self):
        e = self.env(boom=True)
        e.entry("o1", mint="M1")
        b = e.bridge()
        b.tick()
        self.assertEqual(e.port.calls, [])
        self.assertEqual([r["why"] for r in e.log_rows("entry_refused")], [alb.MINT_SCREEN_ERROR])

    def test_an_answer_that_is_not_none_or_a_screen_label_refuses(self):
        for bad in ("", "ok", "universe_v_out_of_range", False, 0, "close_blocked_mint"):
            with self.subTest(bad=bad):
                b = alb.Bridge(alb.Config.from_dict(Env(self.tmp).raw), StubPort(), root_checks=False)
                b.port.screen = bad
                self.assertEqual(b.mint_screen_refusal("M1"), alb.MINT_SCREEN_ERROR)

    def test_a_port_without_its_own_screen_refuses_and_the_dry_port_does_not_read(self):
        self.assertEqual(alb.OrderPort().mint_screen_problem("M1"), alb.MINT_SCREEN_NOT_DECLARED)
        self.assertTrue(alb.MINT_SCREEN_NOT_DECLARED.startswith("close_blocked_mint:"))
        self.assertIsNone(alb.DryPort(1).mint_screen_problem("M1"))

    def test_the_screen_is_the_last_check_no_read_after_an_earlier_refusal(self):
        e = self.env()
        e.port.universe = "universe_v_out_of_range"
        e.entry("o1", pool="P1", mint="M1")
        e.bridge().tick()
        self.assertEqual(e.port.screened, [])  # the universe refusal comes first and keeps its label
        self.assertEqual([r["why"] for r in e.log_rows("entry_refused")], ["universe_v_out_of_range"])
        e2 = Env(sub(self.tmp, "floor"))
        e2.port = ScreenPort(balance=1)  # under the wallet floor
        e2.start()
        e2.entry("o1", mint="M1")
        e2.bridge().tick()
        self.assertEqual(e2.port.screened, [])
        self.assertEqual([r["why"] for r in e2.log_rows("entry_refused")], ["wallet_floor"])

    def test_the_decision_age_is_judged_again_after_the_read(self):
        e = Env(self.tmp)
        e.port = ScreenPort(clock=e.clock, advance_ms=alb.MAX_DECISION_AGE_MS)
        e.start()
        e.entry("o1", mint="M1")
        b = e.bridge()
        b.tick()
        self.assertEqual(e.port.calls, [])
        self.assertEqual(b.state.orders["o1"]["refused"], "stale_decision")
        self.assertTrue(e.log_rows("entry_refused")[-1]["presend"])


class MintRpc:
    """The probe tests' LiveRpc, plus mint accounts served by getAccountInfo (a value dict, None, or an exception to raise)."""

    def __new__(cls, clock):
        from tools.test_probe_live import LiveRpc

        class _Rpc(LiveRpc):
            def __init__(self, clock):
                super().__init__(clock)
                self.mints: dict = {}
                self.mint_reads: list[str] = []

            def __call__(self, method, params):
                if method == "getAccountInfo" and params[0] in self.mints:
                    self.calls.append(method)
                    self.mint_reads.append(params[0])
                    v = self.mints[params[0]]
                    if isinstance(v, BaseException):
                        raise v
                    return {"context": {"slot": 77}, "value": v}
                return super().__call__(method, params)

        return _Rpc(clock)


class LivePortScreenTests(TmpCase):
    def setUp(self) -> None:
        super().setUp()
        try:
            from solders.keypair import Keypair
        except ModuleNotFoundError as e:
            self.skipTest(str(e))
        from tools.test_probe_executor import MINT

        self.Keypair, self.mint = Keypair, MINT

    def port(self, value=None, raw_over: dict | None = None, clock=None):
        from tools.test_probe_executor import Clock as PClock, T0 as PT0

        clock = clock or PClock()
        rpc = MintRpc(clock)
        rpc.mints[self.mint] = mint_value() if value is None else value
        raw = {"bot_id": "bot-x", "frozen_sha": "a" * 64, "root": self.tmp, "state_dir": str(Path(self.tmp) / "st"), "end_ms": PT0 + 86_400_000,
               "max_hold_ms": 600_000, "mode": "live", **(raw_over or {})}
        return alb.make_live_port(alb.Config.from_dict(raw), rpc, self.Keypair(), now_ms=clock), rpc

    def screen(self, value):
        port, _ = self.port(value)
        return port.mint_screen_problem(self.mint)

    def test_allowlisted_token2022_and_classic_spl_pass(self):
        self.assertIsNone(self.screen(mint_value(mf.TOKEN_2022_PROGRAM, ext_types=ALLOW)))
        self.assertIsNone(self.screen(mint_value(mf.TOKEN_2022_PROGRAM, ext_types=(18,))))
        self.assertIsNone(self.screen(mint_value(mf.TOKEN_2022_PROGRAM)))
        self.assertIsNone(self.screen(mint_value()))

    def test_every_token2022_extension_outside_the_allowlist_is_refused(self):
        for t in OUTSIDE:
            with self.subTest(ext=mf.EXT_NAMES.get(t, f"unknown{t}")):
                self.assertEqual(self.screen(mint_value(mf.TOKEN_2022_PROGRAM, ext_types=ALLOW + (t,))), "close_blocked_mint:extension_outside_allowlist")

    def test_a_freeze_authority_is_refused_on_either_program(self):
        self.assertEqual(self.screen(mint_value(freeze=True)), "close_blocked_mint:freeze_authority")
        self.assertEqual(self.screen(mint_value(mf.TOKEN_2022_PROGRAM, freeze=True, ext_types=ALLOW)), "close_blocked_mint:freeze_authority")

    def test_unknown_owner_and_failed_reads_are_refused_deny_by_default(self):
        self.assertEqual(self.screen(mint_value(owner="11111111111111111111111111111111")), "close_blocked_mint:unknown_owner_program")
        self.assertEqual(self.screen(mint_value(owner=alb_pumpswap())), "close_blocked_mint:unknown_owner_program")
        port, rpc = self.port()
        rpc.mints[self.mint] = None  # account not found
        self.assertEqual(port.mint_screen_problem(self.mint), "close_blocked_mint:fetch_failed")
        rpc.mints[self.mint] = RuntimeError("timeout")
        self.assertEqual(port.mint_screen_problem(self.mint), "close_blocked_mint:fetch_failed")
        rpc.mints[self.mint] = {"data": ["", "base64"], "owner": mf.TOKEN_2022_PROGRAM, "lamports": 1}  # too short for a mint
        self.assertEqual(port.mint_screen_problem(self.mint), "close_blocked_mint:fetch_failed")

    def test_every_call_is_a_fresh_read_of_that_mint(self):
        port, rpc = self.port(mint_value(mf.TOKEN_2022_PROGRAM, ext_types=ALLOW))
        self.assertIsNone(port.mint_screen_problem(self.mint))
        rpc.mints[self.mint] = mint_value(mf.TOKEN_2022_PROGRAM, ext_types=ALLOW + (14,))
        self.assertEqual(port.mint_screen_problem(self.mint), "close_blocked_mint:extension_outside_allowlist")  # no cache
        self.assertEqual(rpc.mint_reads, [self.mint, self.mint])

    def test_the_hv4_c0_profile_gets_the_same_screen(self):
        from tools import hv4_live_c0 as c0

        value = mint_value(mf.TOKEN_2022_PROGRAM, ext_types=ALLOW + (4,))
        port, _ = self.port(value, {"bot_id": "hv4-BIG-X", "frozen_sha": c0.FROZEN_SHA, "profile": c0.PROFILE, "funded_lamports": 400_000_000})
        self.assertEqual(port.mint_screen_problem(self.mint), "close_blocked_mint:extension_outside_allowlist")

    def test_it_reuses_the_q50_h5_executor_functions(self):
        from tools import arena_close_blocked as acb
        from tools import h5_arena_executor as ax

        with mock.patch.object(ax, "mint_screen_record", wraps=ax.mint_screen_record) as rec, mock.patch.object(acb, "classify_record", return_value="probe_label") as cls:
            port, rpc = self.port()
            self.assertEqual(port.mint_screen_problem(self.mint), "close_blocked_mint:probe_label")
        self.assertEqual(rec.call_args.args[1], self.mint)
        self.assertEqual(cls.call_count, 1)

    def test_end_to_end_a_blocked_mint_never_reaches_a_buy(self):
        """Bridge.tick over the real live port (LIVE_OK absent: a passing entry would be DRY, a refused one is never routed anywhere)."""
        from solders.pubkey import Pubkey

        from tools import pumpswap_tx as tx

        pool = str(tx.canonical_pool(Pubkey.from_string(self.mint)))
        for value, want in ((mint_value(mf.TOKEN_2022_PROGRAM, ext_types=ALLOW + (14,)), "close_blocked_mint:extension_outside_allowlist"),
                            (mint_value(mf.TOKEN_2022_PROGRAM, ext_types=ALLOW), None)):
            with self.subTest(want=want):
                e = Env(sub(self.tmp, "blocked" if want else "clear"), live=True)
                raw = {k: v for k, v in e.raw.items() if k not in ("stop_file", "halt_file")} | {"mode": "live"}
                cfg = alb.Config.from_dict(raw)
                rpc = MintRpc(e.clock)
                rpc.mints[self.mint] = value
                e.port = alb.make_live_port(cfg, rpc, self.Keypair(), now_ms=e.clock)
                e.start()
                e.entry("o1", pool=pool)
                b = e.bridge()
                b.tick()
                self.assertNotIn("sendTransaction", rpc.calls)
                self.assertEqual(rpc.mint_reads, [self.mint])
                if want:
                    self.assertEqual([r["why"] for r in e.log_rows("entry_refused")], [want])
                    self.assertNotIn("o1", b.state.orders)
                else:
                    self.assertEqual(e.log_rows("entry_refused"), [])
                    self.assertTrue(e.log_rows("buy_sent")[0]["would_send"])  # DRY for want of LIVE_OK, after the screen passed


def sub(tmp: str, name: str) -> str:
    d = Path(tmp) / name
    d.mkdir()
    return str(d)


def alb_pumpswap() -> str:
    from tools import pumpswap_tx as tx

    return str(tx.PUMPSWAP_PROGRAM)


if __name__ == "__main__":
    unittest.main()
