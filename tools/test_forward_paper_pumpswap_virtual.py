"""DEC-016 Am.4 s6: the runner prices PumpSwap execution on vault + V.

Offline. The reference for (b) is `tools/pumpswap_virtual_adapter.make_wrapper` (mcap_mode "v") wrapped
around the parser, run on tape rows that carry no V field.
"""

from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tools.laya_v0 as laya_v0
from tools.forward_paper import (
    BookSpec,
    ForwardEngine,
    LatencyMeter,
    flow_from_tape_row,
    replay_rows,
)
from tools.pumpswap_virtual_adapter import correct_print, make_wrapper
from tools.test_forward_paper import B0, T0, _books, _create, _fixture, _trade

V = 17_580_000_000
POOL = "PoolA"
END = T0 + 60_000


def _swap_rows(with_v: object = "absent") -> list[dict[str, object]]:
    creates, rows = _fixture()
    out = [dict(r) for r in rows]
    for i in range(1, 40):
        out.append(
            _trade("MintA", T0 + 12_000 + i * 500, trader=f"P{i}", venue="pumpswap", side="buy" if i % 3 else "sell",
                   sol=1_000_000_000 + i * 10_000_000, token=4_000_000 + i * 1_000, quote=70_000_000_000 + i * 50_000_000,
                   base=B0 // 2 - i * 1_000_000, slot=30 + i, event_index=i)
        )
    for r in out:
        if r.get("venue") == "pumpswap":
            r["pool"] = POOL
            if with_v != "absent":
                r["virtual_quote_reserve"] = with_v
    return out


def _books_migrate() -> list[BookSpec]:
    return [BookSpec("migrate_hold_30s", "migrate", "hold_30s", creator_cooldown_ms=0, token_cooldown_ms=0)]


def _run(rows, mode="off", books=None):
    creates, _ = _fixture()
    return replay_rows(creates.values(), rows, books or _books_migrate(), tape_end_ms=END,
                       kill_file=Path("/tmp/forward-paper-virtual-test"), offsets_ms=(5_000, 15_000), pumpswap_virtual=mode)


def _dump(engine) -> str:
    return json.dumps({"positions": engine.positions, "skips": [r.ceiling.skip_reasons for r in engine.books]}, sort_keys=True, default=str)


class VirtualRunnerTests(unittest.TestCase):
    def test_a_old_format_tape_is_identical(self) -> None:
        base = _dump(_run(_swap_rows(), "off"))
        # the default engine (no kwarg) is the same as explicit "off"
        creates, _ = _fixture()
        default = replay_rows(creates.values(), _swap_rows(), _books_migrate(), tape_end_ms=END,
                              kill_file=Path("/tmp/forward-paper-virtual-test"), offsets_ms=(5_000, 15_000))
        self.assertEqual(base, _dump(default))
        # a new-format tape with the mode off is identical to the old format, V ignored
        self.assertEqual(base, _dump(_run(_swap_rows(V), "off")))
        self.assertEqual(base, _dump(_run(_swap_rows(None), "off")))
        self.assertIn('"event": "open"', base) if '"event": "open"' in base else self.assertTrue(json.loads(base)["positions"])

    def test_a2_flow_print_unchanged_without_field(self) -> None:
        for row in _swap_rows():
            self.assertEqual(flow_from_tape_row(row), laya_v0.flow_from_row(row) if row.get("quote_is_wsol") is not False else None)

    def test_b_fills_match_adapter_math(self) -> None:
        # reference: the adapter wraps the parser, rows carry no V field, mode off
        vmap = {POOL: V}
        wrapped = make_wrapper(laya_v0.print_from_trade_row, vmap, "v")
        with mock.patch.object(laya_v0, "print_from_trade_row", wrapped):
            ref = _run(_swap_rows(), "off")
        got = _run(_swap_rows(V), "require")
        self.assertTrue(json.loads(_dump(got))["positions"])
        self.assertEqual(_dump(ref), _dump(got))
        # and the path prints are exactly correct_print() of the plain parse
        book = got.library["MintA"]
        n = 0
        for flow_pr, path_pr in zip(book.flow, book.path.prints):
            if flow_pr.venue != "pumpswap":
                self.assertEqual(path_pr, flow_pr.to_tape())
                continue
            row = next(r for r in _swap_rows(V) if r.get("t_recv_ms") == flow_pr.t_recv_ms and r.get("venue") == "pumpswap")
            name, adapted = wrapped(dict(row))
            self.assertEqual(path_pr, adapted)
            # the vault-only parse + V, for the quote itself
            self.assertEqual(path_pr.quote_reserve, flow_pr.quote_reserve + V)
            n += 1
        self.assertGreater(n, 10)

    def test_b2_virtual_exec_print_matches_correct_print_on_posted_quote(self) -> None:
        row = _swap_rows(V)[6]
        assert row["venue"] == "pumpswap"
        _m, flow = flow_from_tape_row(row)
        self.assertEqual(flow.exec_tape.quote_reserve, flow.quote_reserve + V)
        self.assertAlmostEqual(flow.exec_tape.price_sol, flow.exec_tape.quote_reserve / (flow.base_reserve * 1000))
        self.assertAlmostEqual(flow.exec_tape.market_cap_sol, flow.exec_tape.price_sol * 1e9)

    def test_c_features_are_byte_identical_with_and_without_v(self) -> None:
        # (1) the FlowPrint fields every feature reads are the same, V or not
        for with_v in (V, None, 0):
            for plain, new in zip(_swap_rows(), _swap_rows(with_v)):
                a, b = flow_from_tape_row(plain), flow_from_tape_row(new)
                if a is None:
                    self.assertIsNone(b)
                    continue
                self.assertEqual(a[0], b[0])
                self.assertEqual(a[1], dataclasses.replace(b[1], exec_tape=None, v_null=False))
        # (2) what the EXP-012 online features are fed (note_print) and the book's flow are the same
        def feed(rows, mode):
            calls: list[object] = []
            creates, _ = _fixture()
            engine = ForwardEngine(_books_migrate(), kill_file=Path("/tmp/forward-paper-virtual-test"),
                                   latency=LatencyMeter(), tape_end_ms=END, offsets_ms=(5_000, 15_000), pumpswap_virtual=mode)
            spy = mock.MagicMock()
            spy.note_print.side_effect = lambda *a, **k: calls.append((a, sorted(k.items())))
            spy.features_at.return_value = ({"f": 1.0}, None)
            spy.mig_ms_of.return_value = 0
            engine.exp012 = spy
            for create in creates.values():
                engine.push_create(create)
            for row in rows:
                parsed = flow_from_tape_row(row)
                if parsed is not None:
                    engine.push_print(parsed[0], parsed[1], T0 // 1000)
            engine.drain_until(END, final=True)
            return calls, [dataclasses.replace(p, exec_tape=None, v_null=False) for p in engine.library["MintA"].flow]

        base_calls, base_flow = feed(_swap_rows(), "off")
        self.assertTrue(base_calls)
        for mode, with_v in (("off", V), ("require", V)):
            calls, flow = feed(_swap_rows(with_v), mode)
            self.assertEqual(json.dumps(base_calls, default=str), json.dumps(calls, default=str))
            self.assertEqual(base_flow, flow)

    def test_d_null_v_skips_with_no_v(self) -> None:
        eng = _run(_swap_rows(None), "require")
        run = eng.books[0]
        self.assertGreaterEqual(run.ceiling.skip_reasons.get("no_v", 0), 1)
        self.assertEqual([r for r in eng.positions if r.get("event") in ("open", "close")], [])
        # absent field + require is also "no V": skipped, never priced on the vault alone
        eng2 = _run(_swap_rows(), "require")
        self.assertGreaterEqual(eng2.books[0].ceiling.skip_reasons.get("no_v", 0), 1)
        # with V the same tape enters
        eng3 = _run(_swap_rows(V), "require")
        self.assertEqual(eng3.books[0].ceiling.skip_reasons.get("no_v", 0), 0)

    def test_mode_is_validated_and_configured_require(self) -> None:
        with self.assertRaises(ValueError):
            ForwardEngine(_books_migrate(), kill_file=Path("/tmp/x"), pumpswap_virtual="on")
        cfg = json.loads((Path(__file__).resolve().parent.parent / "scripts/mal-fast/fast-forward-paper.json").read_text())
        self.assertEqual(cfg["pumpswap_virtual"], "require")


if __name__ == "__main__":
    unittest.main()
