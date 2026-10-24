"""venue_tape: decode of captured public-RPC logs (fixtures/venue_tape/notices.jsonl, captured 2026-10-24) and synthetic edge cases. No network."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as N

import pytest

from tools import venue_tape as vt

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "venue_tape" / "notices.jsonl"


def cases() -> dict[str, dict]:
    return {c["case"]: c for c in (json.loads(l) for l in FIX.read_text().splitlines())}


def note(c: dict, t: int = 1_790_000_000_000) -> N:
    return N(slot=c["slot"], signature=c["signature"], failed=c["err"] is not None, logs=c["logs"], t_recv_ms=t)


def test_discriminators_match_anchor_hash():
    assert hashlib.sha256(b"event:TradeEvent").digest()[:8] == vt.LL_TRADE_DISC
    assert hashlib.sha256(b"event:PoolCreateEvent").digest()[:8] == vt.LL_CREATE_DISC


def test_program_ids_are_pinned():
    assert vt.VENUES["launchlab"]["program"] == "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj"
    assert vt.VENUES["meteora_dbc"]["program"] == "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN"


def test_b58_known():
    assert vt.b58(bytes(32)) == "1" * 32
    assert vt.b58(b"\x00\x01") == "12"


@pytest.mark.parametrize("case,side", [("ll_buy", "buy"), ("ll_sell", "sell")])
def test_launchlab_trade_fixture(case, side):
    rows = vt.rows_for_venue("launchlab", note(cases()[case]))
    t = [r for r in rows if r["type"] == "trade"]
    assert len(t) == 1
    r = t[0]
    assert r["side"] == side and r["venue"] == "launchlab" and r["event_index"] == 0
    assert "trader" not in r  # the signer is not in the logs
    # reserves are consistent with the amounts: the quote reserve moves by amount minus fees (buy) or by the payout (sell)
    dq = r["real_quote_after"] - r["real_quote_before"]
    if side == "buy":
        assert dq == r["amount_in"] - r["protocol_fee"] - r["platform_fee"] - r["creator_fee"] - r["share_fee"]
        assert r["real_base_after"] > r["real_base_before"]
    else:
        assert r["real_base_after"] < r["real_base_before"] or r["real_base_after"] == r["real_base_before"] - r["amount_in"]
    json.dumps(r, allow_nan=False)


def test_launchlab_failed_and_nodata_are_ix_rows():
    for k in ("ll_failed", "ll_nodata"):
        rows = vt.rows_for_venue("launchlab", note(cases()[k]))
        for r in rows:
            assert r["type"] in ("ix", "trade", "create", "event_undecoded")
    f = vt.rows_for_venue("launchlab", note(cases()["ll_failed"]))
    assert all(r.get("failed") for r in f)


def test_dbc_swap_is_instruction_only():
    rows = vt.rows_for_venue("meteora_dbc", note(cases()["dbc_swap"]))
    assert len(rows) == 1
    r = rows[0]
    assert r["type"] == "ix" and "Swap2" in r["ix"] and r["amounts_undecoded"] is True and r["events_in_logs"] is False
    assert not any(k in r for k in ("amount_in", "real_quote_before", "trader"))


def test_dbc_failed_flagged_and_migration_named():
    r = vt.rows_for_venue("meteora_dbc", note(cases()["dbc_failed"]))[0]
    assert r["failed"] is True
    m = vt.rows_for_venue("meteora_dbc", note(cases()["dbc_migrate"]))[0]
    assert "MigrationDammV2" in m["ix"]


def test_other_venue_program_absent():
    assert vt.rows_for_venue("launchlab", note(cases()["dbc_swap"])) == []
    assert vt.rows_for_venue("meteora_dbc", note(cases()["ll_buy"])) == []


def _ll(data: list[str], program=vt.VENUES["launchlab"]["program"]) -> N:
    logs = [f"Program {program} invoke [1]", "Program log: Instruction: BuyExactIn"] + [f"Program data: {d}" for d in data] + [f"Program {program} success"]
    return N(slot=5, signature="sig", failed=False, logs=logs, t_recv_ms=7)


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def test_synthetic_trade_exact_layout():
    body = bytes(range(32)) + b"".join((i + 1).to_bytes(8, "little") for i in range(13)) + bytes([1, 2, 1])
    r = vt.rows_for_venue("launchlab", _ll([b64(vt.LL_TRADE_DISC + body)]))[0]
    assert r["type"] == "trade" and r["side"] == "sell" and r["pool_status"] == "trade" and r["exact_in"] is True
    assert r["total_base_sell"] == 1 and r["share_fee"] == 13 and r["amount_out"] == 9


def test_bad_enum_wrong_length_unknown_disc_bad_b64_are_undecoded():
    body = bytes(32) + bytes(13 * 8)
    for raw, reason in [
        (vt.LL_TRADE_DISC + body + bytes([7, 0, 1]), "trade_enum"),
        (vt.LL_TRADE_DISC + body + bytes([0, 0]), "trade_len_146"),
        (bytes(8) + body, "unknown_disc"),
    ]:
        r = vt.rows_for_venue("launchlab", _ll([b64(raw)]))[0]
        assert r["type"] == "event_undecoded" and r["reason"] == reason and "data_b64" in r
    r = vt.rows_for_venue("launchlab", _ll(["!!notb64!!"]))[0]
    assert r["type"] == "event_undecoded" and r["reason"] == "bad_base64"


def test_create_event_leading_pubkeys_only():
    raw = vt.LL_CREATE_DISC + bytes([1]) * 32 + bytes([2]) * 32 + bytes([3]) * 32 + b"tail"
    r = vt.rows_for_venue("launchlab", _ll([b64(raw)]))[0]
    assert r["type"] == "create" and r["pool"] == vt.b58(bytes([1]) * 32) and r["config"] == vt.b58(bytes([3]) * 32) and "params_undecoded_b64" in r


def test_data_from_other_programs_is_not_attributed():
    P, O = vt.VENUES["launchlab"]["program"], "OtherProg1111111111111111111111111111111111"
    logs = [f"Program {P} invoke [1]", "Program log: Instruction: BuyExactIn", f"Program {O} invoke [2]", "Program data: AAAA", f"Program {O} success",
            f"Program {P} success"]
    rows = vt.rows_for_venue("launchlab", N(slot=1, signature="s", failed=False, logs=logs, t_recv_ms=1))
    assert [r["type"] for r in rows] == ["ix"]  # the foreign event is not ours


def test_via_cpi_and_truncated_flags():
    P = vt.VENUES["launchlab"]["program"]
    logs = ["Program Router111 invoke [1]", f"Program {P} invoke [2]", "Program log: Instruction: SellExactIn", f"Program {P} success",
            "Program Router111 success", "Log truncated"]
    r = vt.rows_for_venue("launchlab", N(slot=1, signature="s", failed=False, logs=logs, t_recv_ms=1))[0]
    assert r["via_cpi"] is True and r["logs_truncated"] is True and r["ix"] == ["SellExactIn"]


def test_event_index_counts_per_type():
    body = bytes(32) + bytes(13 * 8) + bytes([0, 2, 1])
    rows = vt.rows_for_venue("launchlab", _ll([b64(vt.LL_TRADE_DISC + body)] * 2))
    assert [r["event_index"] for r in rows] == [0, 1]


# ---------------------------------------------------------------------------------------------------------- writer / gaps / compaction
class Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


T0 = 1_790_000_400
T0 -= T0 % 3600


def lines(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text().splitlines()]


def row(t_ms: int, i: int = 0) -> dict:
    return {"v": 1, "venue": "launchlab", "type": "ix", "slot": i, "t_recv_ms": t_ms, "signature": f"s{i}", "event_index": 0}


def test_hourly_files_and_late_rows_go_to_current_hour(tmp_path):
    clk = Clock(T0 + 10)
    w = vt.TapeWriter(tmp_path, ["launchlab"], clock=clk, free_fn=lambda p: 0.9)
    assert w.write("launchlab", row((T0 + 5) * 1000))
    clk.t = T0 + 3600 + 30
    assert w.write("launchlab", row((T0 + 3600 + 1) * 1000, 1))
    assert w.write("launchlab", row((T0 + 3590) * 1000, 2))  # hour 0 ended <120 s ago: still its own file
    clk.t = T0 + 3600 + 300
    assert w.write("launchlab", row((T0 + 100) * 1000, 3))  # hour 0 closed: goes to the current hour
    w.close()
    names = sorted(p.name for p in (tmp_path / "launchlab").iterdir())
    assert len(names) == 2
    h0, h1 = (tmp_path / "launchlab" / n for n in names)
    assert [r["slot"] for r in lines(h0)] == [0, 2] and [r["slot"] for r in lines(h1)] == [1, 3]


def test_disk_guard_pauses_and_resumes_with_rows_dropped(tmp_path):
    clk = Clock(T0 + 10)
    free = [0.5]
    w = vt.TapeWriter(tmp_path, ["launchlab"], clock=clk, free_fn=lambda p: free[0])
    assert w.write("launchlab", row((T0 + 1) * 1000))
    free[0] = 0.19
    clk.t += 6
    assert not w.write("launchlab", row((T0 + 2) * 1000, 1))
    assert not w.write("launchlab", row((T0 + 3) * 1000, 2))
    free[0] = 0.5
    clk.t += 6
    assert w.write("launchlab", row((T0 + 4) * 1000, 3))
    w.close()
    rs = lines(next((tmp_path / "launchlab").iterdir()))
    types = [r["type"] for r in rs]
    assert types == ["ix", "disk_pause", "disk_resume", "ix"]
    assert rs[2]["rows_dropped"] == 2


def test_torn_last_line_is_ended(tmp_path):
    clk = Clock(T0 + 10)
    d = tmp_path / "launchlab"
    d.mkdir()
    (d / vt.hour_name(T0 * 1000)).write_text('{"a":1}\n{"torn')
    w = vt.TapeWriter(tmp_path, ["launchlab"], clock=clk, free_fn=lambda p: 0.9)
    w.write("launchlab", row((T0 + 1) * 1000))
    w.close()
    assert w.torn_lines_ended == 1
    ls = (d / vt.hour_name(T0 * 1000)).read_text().splitlines()
    assert ls[1] == '{"torn' and json.loads(ls[2])["type"] == "ix"


def test_monitor_gap_rows_for_reconnect_and_silence(tmp_path):
    clk = Clock(T0 + 10)
    w = vt.TapeWriter(tmp_path, ["launchlab"], clock=clk, free_fn=lambda p: 0.9)
    m = vt.Monitor(w, ["launchlab"])
    start = m.started_ms
    m.tick(start + 1000, {0: 0, 1: 0})
    m.tick(start + 2000, {0: 2, 1: 0})
    m.tick(start + vt.VENUES["launchlab"]["silence_s"] * 1000 + 5000, {0: 2, 1: 0})
    m.saw("launchlab", start + 1_000_000)
    w.close()
    gaps = [r for r in lines(next((tmp_path / "launchlab").iterdir())) if r["type"] in ("gap", "gap_end")]
    assert [(g["type"], g["reason"]) for g in gaps] == [("gap", "socket_reconnect"), ("gap", "silence"), ("gap_end", "silence")]
    assert gaps[0]["socket"] == 0 and gaps[0]["new"] == 2


def test_compact_closed_only_closed_hours(tmp_path):
    zstd = pytest.importorskip("tools.arena_zst")
    if zstd.zstd_path() is None:
        pytest.skip("zstd CLI missing")
    d = tmp_path / "launchlab"
    d.mkdir()
    old, cur = d / vt.hour_name(T0 * 1000), d / vt.hour_name((T0 + 3600) * 1000)
    old.write_text(json.dumps(row(T0 * 1000)) + "\n")
    cur.write_text(json.dumps(row((T0 + 3600) * 1000)) + "\n")
    done = vt.compact_closed(tmp_path, ["launchlab"], (T0 + 3600 + 130) * 1000, set(), zstd.compact_file)
    assert done == [str(old)]
    assert not old.exists() and Path(str(old) + ".zst").exists() and cur.exists()
    # an open file is never touched
    done = vt.compact_closed(tmp_path, ["launchlab"], (T0 + 7200 + 500) * 1000, {("launchlab", cur.name)}, zstd.compact_file)
    assert done == [] and cur.exists()


def test_ws_url_with_key_is_refused(tmp_path, capsys):
    import asyncio
    args = vt.argparse.Namespace(venues="launchlab", ws_url=["wss://mainnet.helius-rpc.com/?api-key=x"], out_dir=str(tmp_path), sockets=1,
                                 min_free=0.2, no_compress=True)
    pytest.importorskip("websockets")
    assert asyncio.run(vt.run(args)) == 2
