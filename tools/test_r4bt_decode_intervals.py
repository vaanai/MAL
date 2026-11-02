"""r4bt_decode_evidence, QP Addendum 1 A: every write is checked on its own live interval. Synthetic blocks and stubbed samples: no network."""
from __future__ import annotations

import pytest

from tools import a3_redeploy_check as ARC
from tools import r4bt_decode_evidence as DE
from tools import r4bt_decode_parity as PAR
from tools.test_r4bt_decode_evidence import PINS, R4PINS, T0, WRITE, install, stubs  # noqa: F401  (stubs is a fixture)
from tools.test_r4bt_decode_parity import BOND, BUY, td, tx


class Blocks:
    """getBlock fake: {slot: [tx log lists]}; records the slots asked."""

    def __init__(self, table):
        self.table, self.asked = table, []

    def call(self, method, params):
        assert method == "getBlock"
        self.asked.append(params[0])
        txs = self.table.get(params[0])
        return None if txs is None else {"transactions": [{"meta": {"err": None, "logMessages": logs}} for logs in txs]}


def run(client, end, adapter, now=T0, deploy=T0):
    return DE.check_write(WRITE, PINS, R4PINS, client=client, http_get=None, now_ms=lambda: now, deploy_ms=deploy, adapter_check=adapter, end_slot=end)


def test_write_intervals_pair_each_write_with_the_next_write_of_the_same_program_and_skip_fees():
    ws = [{"name": "pump", "slot": 100, "at_ms": 5}, {"name": "pumpswap", "slot": 50, "at_ms": 3}, {"name": "pump", "slot": 200, "at_ms": 9}, {"name": "fees", "slot": 10, "at_ms": 1}]
    out = DE.write_intervals(ws)
    assert [(w["name"], w["slot"], w["end_slot"], w["deploy_ms"]) for w in out] == [("pumpswap", 50, None, 3), ("pump", 100, 200, 3), ("pump", 200, None, 3)]
    with pytest.raises(ValueError):
        DE.write_intervals([{"name": "pump", "slot": "x", "at_ms": 1}])


def test_fetch_interval_sample_never_passes_end_and_reports_the_population():
    one = tx(td.PUMPSWAP_PROGRAM, BUY)
    cl = Blocks({s: [one] for s in range(1000, 1020)})
    sample, scanned, whole = DE.fetch_interval_sample(cl, 1000, 1010)
    assert len(sample) == 10 and scanned == 10 and whole is True and max(cl.asked) == 1009
    cl = Blocks({s: [one] for s in range(1000, 6000)})
    sample, scanned, whole = DE.fetch_interval_sample(cl, 1000, 5000)  # longer than SAMPLE_MAX_SLOTS: sampled from the start, not the population
    assert scanned == ARC.SAMPLE_MAX_SLOTS and len(sample) == ARC.SAMPLE_MAX_SLOTS and whole is False


def test_an_interval_shorter_than_the_offset_is_vacuous_without_any_sample_rpc(stubs):
    r = run(None, 1000 + ARC.SAMPLE_START_OFFSET_SLOTS, lambda s, population=False: [])
    assert r["verdict"] == "PASS" and r["mode"] == "vacuous" and r["slot_range"] == [1150, 1150]


def test_an_interval_with_zero_program_transactions_is_a_recorded_vacuous_pass(stubs, monkeypatch):
    monkeypatch.setattr(DE, "migrate_decode_evidence", lambda *a, **k: {"n_migrate_txs": 0, "seen_events": []})
    r = run(Blocks({s: [] for s in range(1150, 1200)}), 1200, lambda s, population=False: ["must not run"])
    assert r["verdict"] == "PASS" and r["mode"] == "vacuous" and r["slot_range"] == [1150, 1200] and r["detail"]["n_txs"] == 0


def test_a_small_interval_is_checked_whole_without_the_floors_but_parity_still_decides(stubs, monkeypatch):
    monkeypatch.setattr(DE, "migrate_decode_evidence", lambda *a, **k: {"n_migrate_txs": 0, "seen_events": []})
    monkeypatch.setattr(ARC, "evaluate_sample", lambda *a, **k: {"seen_events": [], "undecodable": 0, "unknown": {}, "idl_mismatch": 0, "layout_mismatch": 0, "disc_len": {},
                                                                   "capped": 0, "n_swap_events": 0})
    cl = Blocks({1150: [tx(td.PUMPSWAP_PROGRAM, BUY)], 1160: [tx(td.PUMP_BONDING_PROGRAM, BOND)]})
    real = lambda sample, population=False: PAR.parity_failures(sample, min_txs=0, require_venues=False)  # noqa: E731
    r = run(cl, 1200, real)
    assert r["verdict"] == "PASS" and r["mode"] == "population" and r["detail"]["n_txs"] == 2  # 2 txs: below the 500 floor, required events unseen: not failures here
    bad = run(cl, 1200, lambda s, population=False: ["trade.value:token_raw x1"])
    assert bad["verdict"] == "FAIL" and bad["reasons"] == ["c: adapter path: trade.value:token_raw x1"]


def test_a_long_bounded_interval_keeps_the_sample_floor(stubs, monkeypatch):
    monkeypatch.setattr(DE, "migrate_decode_evidence", lambda *a, **k: {"n_migrate_txs": 0, "seen_events": ["Complete"]})
    cl = Blocks({s: [tx(td.PUMPSWAP_PROGRAM, BUY)] for s in range(1150, 1150 + 1000, 5)})
    r = run(cl, 1150 + 1000, lambda s, population=False: [])
    assert r["mode"] == "sample" and r["verdict"] == "FAIL" and r["reasons"][0].startswith(f"c: sample has 60 txs after {ARC.SAMPLE_MAX_SLOTS} slots, need {ARC.SAMPLE_MIN_TXS}")


def test_bounded_migrate_evidence_filters_to_the_interval_and_has_no_floor(monkeypatch):
    install(monkeypatch, 40, 0)  # slots 1000..1039
    ev = DE.migrate_decode_evidence(None, {}, 1, 1e9, {}, {}, end_slot=1010)
    assert ev["n_migrate_txs"] == 10 and ev["slot_max"] == 1009  # 10 < MIGRATE_K_MIN, still no error: the interval's whole population
    assert DE.migrate_decode_evidence(None, {}, 1, 1e9, {}, {}, end_slot=900)["n_migrate_txs"] == 0
    install(monkeypatch, 10, 0)
    with pytest.raises(ARC.CheckError):
        DE.migrate_decode_evidence(None, {}, 1, 1e9, {}, {})  # open-ended: the floor is an ERROR


def test_check_all_gives_one_result_per_write_and_does_not_stop_at_a_failure(stubs):
    ws = [{"name": "pump", "slot": 1000, "at_ms": T0 - 5_000}, {"name": "pump", "slot": 1100, "at_ms": T0 - 4_000}, {"name": "pumpswap", "slot": 2000, "at_ms": T0 - 3_000}]
    res = DE.check_all(ws, PINS, R4PINS, client=None, http_get=None, now_ms=lambda: T0, adapter_check=lambda s, population=False: ["x"])
    assert [(r["name"], r["slot"]) for r in res] == [("pump", 1000), ("pump", 1100), ("pumpswap", 2000)]
    assert [r["verdict"] for r in res] == ["PASS", "FAIL", "FAIL"] and res[0]["mode"] == "vacuous"  # the first write's interval is empty (next write within the offset)
