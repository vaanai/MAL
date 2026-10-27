"""HV8 C3 tests: tools/hv8_dump.py. SYNTHETIC tape in a temp root only: no real hour, no API call, no key, no network. Nothing here says HV8 makes money.
Run: /data/mal/venv/bin/python -B -m pytest tools/test_hv8_*.py -q"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import hv7_rank as RK
from tools import hv8_dump as D
from tools import hv8_rate as H8
from tools.drill_hv6_item6 import FakeChain
from tools.test_hv4_runner import write_tape
from tools.test_hv7_runner import HOUR, MINTS, POOLS, T0, WSOL, _env, tape_rows  # noqa: F401 - _env is an autouse fixture

WIN = D.Window(block_start_ms=T0 - 3 * HOUR, first_h_ms=T0 + HOUR, last_h_excl_ms=T0 + 4 * HOUR, end_ms=T0 + 5 * HOUR)


def run(tmp: Path, rows, tag="a", t_ds=None, win=WIN):
    tape = tmp / f"tape-{tag}"
    write_tape(tape, rows)
    return D.collect(tape, tmp / f"scratch-{tag}", win, t_ds=t_ds)


def shas(res):
    return [[H8.view_sha256(c) for c in cs] for _t, cs in res["sets"]]


# ---------------------------------------------------------------- grid and QP condition on #1011
def test_grid_is_167_instants_from_0821t12_to_0828t1002():
    g = D.DEFAULT.grid()
    assert len(g) == 167
    assert D.iso_s(g[0]) == "2026-08-21T12:02:00Z" and D.iso_s(g[-1]) == "2026-08-28T10:02:00Z"


@pytest.mark.parametrize("iso", ["2026-08-28T11:02:00Z", "2026-08-28T12:02:00Z", "2026-08-28T12:00:00Z", "2026-08-21T11:02:00Z", "2026-08-21T12:03:00Z"])
def test_a_t_d_outside_the_grid_or_at_the_end_raises(iso):
    with pytest.raises(D.Refused):
        D.check_td(D.iso_ms(iso))


def test_fakechain_chain_part_lacks_the_three_tape_only_risk_inputs():
    part = FakeChain().chain_part("p", "m", 1_000_000, 5)
    for k in ("holder_count", "quote_sol_15m_ago", "quote_sol_peak"):
        assert k not in part


# ---------------------------------------------------------------- 08-28T12 refusals
def test_hour_file_at_or_after_0828t12_raises_and_nothing_is_written(tmp_path):
    d = tmp_path / "tp"
    d.mkdir()
    (d / "tape-2026-08-28T12.jsonl.zst").write_bytes(b"")
    with pytest.raises(D.Refused, match="hour_file_at_or_after"):
        D.hour_files(d)
    with pytest.raises(D.Refused):
        D.collect(d, tmp_path / "sc", t_ds=[])
    assert not (tmp_path / "sc").exists()


def test_hour_file_before_the_block_start_raises(tmp_path):
    d = tmp_path / "tp"
    d.mkdir()
    (d / "tape-2026-08-14T11.jsonl").write_bytes(b"")
    with pytest.raises(D.Refused, match="before_block_start"):
        D.hour_files(d)


def test_hour_file_0828t11_is_accepted_by_name(tmp_path):
    d = tmp_path / "tp"
    d.mkdir()
    (d / "tape-2026-08-28T11.jsonl").write_bytes(b"")
    assert len(D.hour_files(d)) == 1


@pytest.mark.parametrize("key", ["t_recv_ms", "event_ts", "block_time"])
def test_a_planted_row_at_0828t12_in_an_allowed_hour_file_raises(tmp_path, key):
    d = tmp_path / "tp"
    d.mkdir()
    end = D.DEFAULT.end_ms
    row = {"type": "trade", "t_recv_ms": end - 5 * 60_000}
    row[key] = end if key == "t_recv_ms" else end // 1000
    (d / "tape-2026-08-28T11.jsonl").write_text(json.dumps(row) + "\n")
    with pytest.raises(D.Refused, match="row_at_or_after"):
        D.collect(d, tmp_path / "sc", t_ds=[])


def test_a_row_one_ms_before_the_end_is_not_refused(tmp_path):
    D.guard_row({"type": "trade", "t_recv_ms": D.DEFAULT.end_ms - 1})
    with pytest.raises(D.Refused):
        D.guard_row({"type": "trade", "t_recv_ms": D.DEFAULT.end_ms})


def test_replay_applies_the_row_guard_to_rows_read_before_they_queue(tmp_path):
    rows = tape_rows()
    rows.append({"v": 1, "type": "trade", "prog": "pumpswap", "slot": 1, "t_recv_ms": WIN.end_ms, "signature": "late", "event_index": 0, "pool": POOLS[0],
                 "mint": MINTS[0], "trader": "X", "side": "buy", "sol_lamports": 1, "token_raw": 1, "quote_reserve": 1, "base_reserve": 1, "quote_mint": WSOL})
    with pytest.raises(D.Refused):
        run(tmp_path, rows)


@pytest.mark.parametrize("p", ["/x/fresh-0828/w1", "/x/fresh-0808", "/data/mal/arena/tape", "/data/mal/meta-capture/x", "/x/hv4-out"])
def test_denied_paths_raise(p):
    with pytest.raises(D.Refused, match="denied_path"):
        D.check_path(p)


# ---------------------------------------------------------------- replay, created_known, no-lookahead
def test_synthetic_replay_gives_sets_with_tape_only_views(tmp_path):
    res = run(tmp_path, tape_rows())
    assert len(res["sets"]) == 3 and res["periods"] == 3
    for t, cards in res["sets"]:
        assert 3 <= len(cards) <= 8 and all(c["as_of_ms"] == t for c in cards)
    v = H8.tape_view(res["sets"][0][1][0])
    assert "mint" not in v and "top1_frac" not in v["risk"]


def _without_pool_create(rows, k):
    return [r for r in rows if not (r["type"] == "pool_create" and r["pool"] == POOLS[k])]


def test_a_pool_without_a_create_event_is_dropped_before_the_cut_and_counted(tmp_path):
    base = run(tmp_path, tape_rows(), "a")
    res = run(tmp_path, _without_pool_create(tape_rows(), 1), "b")
    assert base["dropped_instances"] == 0
    assert res["dropped_pools"] == 1 and res["dropped_instances"] >= 3
    ids_a = {c["id"] for _t, cs in base["sets"] for c in cs}
    ids_b = {c["id"] for _t, cs in res["sets"] for c in cs}
    assert POOLS[1] in ids_a and POOLS[1] not in ids_b      # its first print is long before t_d, yet it never gets created_known
    assert all(len(cs) == 3 for _t, cs in res["sets"])


def test_a_complete_event_before_t_d_sets_created_known_and_one_after_does_not(tmp_path):
    rows = _without_pool_create(tape_rows(), 1)
    early = rows + [{"v": 1, "type": "complete", "t_recv_ms": T0 - 3 * HOUR + 5, "mint": MINTS[1], "event_ts": (T0 - 3 * HOUR) // 1000, "signature": "c1", "event_index": 0}]
    res = run(tmp_path, sorted(early, key=lambda r: r["t_recv_ms"]), "a")
    assert POOLS[1] in {c["id"] for _t, cs in res["sets"] for c in cs} and res["dropped_instances"] == 0
    late = rows + [{"v": 1, "type": "complete", "t_recv_ms": T0 + 2 * HOUR + 30 * 60_000, "mint": MINTS[1], "signature": "c1", "event_index": 0}]
    res2 = run(tmp_path, sorted(late, key=lambda r: r["t_recv_ms"]), "b")
    first = {c["id"] for c in res2["sets"][0][1]}
    assert POOLS[1] not in first                                   # complete after t_d of the first set does not count for it
    assert POOLS[1] in {c["id"] for c in res2["sets"][-1][1]}      # it does count once observed before a later t_d


def test_known_ignores_events_before_the_block_start_and_first_print(tmp_path):
    k = D.Known(WIN)
    k.observe({"type": "complete", "t_recv_ms": WIN.block_start_ms - 1, "mint": "M"})
    k.observe({"type": "trade", "t_recv_ms": WIN.block_start_ms + 1, "mint": "M", "pool": "P"})
    assert not k.known("P", "M", WIN.end_ms)
    k.observe({"type": "pool_create", "t_recv_ms": WIN.block_start_ms, "mint": "M", "pool": "P"})
    assert k.known("P", None, WIN.block_start_ms + 1) and not k.known("P", None, WIN.block_start_ms)


@pytest.mark.parametrize("n", [0, 1, 2])
def test_no_lookahead_card_bytes_equal_on_the_truncated_tape(tmp_path, n):
    t_d = WIN.grid()[n]
    full = run(tmp_path, tape_rows(), "full", t_ds=[t_d])
    cut = run(tmp_path, [r for r in tape_rows() if r["t_recv_ms"] < t_d], "cut", t_ds=[t_d])
    assert full["sets"] and shas(full) == shas(cut)
    a, b = full["sets"][0][1], cut["sets"][0][1]
    assert [json.dumps(c, sort_keys=True) for c in a] == [json.dumps(c, sort_keys=True) for c in b]


# ---------------------------------------------------------------- outputs
def test_write_dump_manifest_prompts_and_cards(tmp_path):
    res = run(tmp_path, tape_rows())
    out = tmp_path / "out"
    r = D.write_dump(res, out, head="a" * 40, win=WIN, out_root=None)
    m = json.loads((out / "manifest.json").read_text())
    n = sum(len(c) for _t, c in res["sets"])
    assert m["cards"] == r["cards"] == n and m["api_calls"] == 0 and m["outcome_free"]
    assert m["retest"] == -(-n // 10) and m["caps"]["cap"] >= n + m["retest"]
    assert m["batch_plan"]["first_batches"] == 1 and m["batch_plan"]["retest_batches"] == 1 and m["batch_plan"]["rerun_batch"] == 1
    cards = [json.loads(x) for x in (out / "cards.jsonl").read_text().splitlines()]
    assert len(cards) == n and len({c["card_sha"] for c in cards}) == n
    assert not {"ret", "pnl", "label", "y", "exit"} & set(cards[0])
    import hashlib
    for f in m["files"]:
        assert hashlib.sha256((out / f["file"]).read_bytes()).hexdigest() == f["sha256"]
    lines = [json.loads(x) for f in m["files"] for x in (out / f["file"]).read_text().splitlines()]
    assert len(lines) == n + m["retest"] and {l["seq"] for l in lines} == {0, 1}
    assert len({l["request_sha256"] for l in lines}) == len(lines)          # retest nonce differs from the first rating
    with pytest.raises(D.Refused, match="out_not_empty"):
        D.write_dump(res, out, head="a" * 40, win=WIN, out_root=None)
    with pytest.raises(D.Refused, match="out_not_under_root"):
        D.write_dump(res, tmp_path / "other", head="a" * 40, win=WIN)


def test_two_replays_write_identical_bytes(tmp_path):
    outs = []
    for tag in "ab":
        res = run(tmp_path, tape_rows(), tag)
        D.write_dump(res, tmp_path / f"o{tag}", head="a" * 40, win=WIN, out_root=None)
        outs.append(tmp_path / f"o{tag}")
    for name in ("manifest.json", "cards.jsonl", "prompts/batch-01.jsonl"):
        assert (outs[0] / name).read_bytes() == (outs[1] / name).read_bytes()


def test_main_refuses_a_denied_tape_path(tmp_path, capsys):
    assert D.main(["--tape", str(tmp_path / "fresh-0828"), "--scratch", str(tmp_path / "s"), "--count-only"]) == 2
