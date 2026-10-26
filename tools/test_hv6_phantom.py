"""HV6 phantom-row filter (QP ruling 2026-10-26). Synthetic tape only: no real tape, no API, no RPC, no HV4/Q50 output."""

from __future__ import annotations

import copy

from tools import hv6_phantom as P
from tools import hv6_runner as R
from tools.hv6_phantom import PhantomFilter
from tools.test_hv4_runner import T0, trade_rows, write_tape
from tools.test_hv6_runner import _env, make, rows_of  # noqa: F401  (_env is autouse)

MIN = 60_000
HOUR = 3_600_000
BAD = "BUpWiWy8Tqed4jy86dr7ZpoaE747DpQsrGnwmtpMwfBr"
BAD2 = "oi1ewFJP7HikgdbjhnUboCzuWXHanAe4Wtvy8UFEnd6"


def row(sig, slot, t=1_000, trader="T1", typ="trade", ei=0):
    return {"type": typ, "slot": slot, "t_recv_ms": t, "signature": sig, "event_index": ei, "trader": trader}


def test_pinned_signers():
    assert P.PHANTOM_SIGNERS == {BAD, BAD2} and P.ALL_ONES_SIG == "1" * 64 and len(P.ALL_ONES_SIG) == 64


def test_all_ones_signature_dropped_every_time_and_not_remembered():
    f = PhantomFilter()
    assert f.check(row("1" * 64, 10)) == "sig_ones" and f.check(row("1" * 64, 10)) == "sig_ones" and f.check(row("1" * 64, 99, typ="pool_create")) == "sig_ones"
    assert f.counts == {"sig_ones": 3, "signer": 0, "repeat_slot": 0} and f.size() == 0
    assert f.check(row("1" * 63, 10)) is None and f.check(row("1" * 65, 10)) is None  # only the exact 64-character string


def test_listed_signers_dropped_on_trade_rows_only():
    f = PhantomFilter()
    assert f.check(row("a", 1, trader=BAD)) == "signer" and f.check(row("b", 1, trader=BAD2)) == "signer"
    assert f.check(row("c", 1, trader=BAD, typ="pool_create")) is None  # trade rows only
    assert f.check(row("d", 1, trader="BUpWiWy8")) is None and f.counts["signer"] == 2


def test_repeat_signature_at_other_slot_dropped_same_slot_kept():
    f = PhantomFilter()
    assert f.check(row("x", 100)) is None                      # first sighting passes (cannot be retracted)
    assert f.check(row("x", 100, ei=1)) is None                # same tx, another event, same slot: real
    assert f.check(row("x", 101)) == "repeat_slot"
    assert f.check(row("x", 100)) is None                      # the first slot stays the reference: the drop did not overwrite it
    assert f.counts["repeat_slot"] == 1


def test_real_rows_pass_untouched():
    f = PhantomFilter()
    rows = [row(f"sig{i}", 1000 + i // 3, t=1_000 + i, trader=f"w{i % 7}") for i in range(500)]
    assert all(f.check(r) is None for r in rows) and f.total() == 0
    assert rows == [row(f"sig{i}", 1000 + i // 3, t=1_000 + i, trader=f"w{i % 7}") for i in range(500)]  # the filter never mutates a row
    assert f.check({"type": "trade", "slot": 5}) is None and f.check({"type": "trade", "signature": None, "slot": None}) is None  # missing fields are kept


def test_first_match_counts_once():
    f = PhantomFilter()
    assert f.check(row("1" * 64, 1, trader=BAD)) == "sig_ones" and f.total() == 1


def test_history_keeps_two_hours_and_memory_is_bounded():
    f = PhantomFilter()
    assert f.check(row("old", 5, t=0)) is None
    t = 0
    for i in range(1, 9):                                       # one row per 15 min for 2 h: the signature is still remembered at +2 h
        t = i * 15 * MIN
        assert f.check(row(f"f{i}", 10 + i, t=t)) is None
    assert f.check(row("old", 6, t=2 * HOUR)) == "repeat_slot"
    for h in range(3, 12):                                      # a million-ish rows would hold at most 3 generations: here, 500 per hour
        for i in range(500):
            f.check(row(f"h{h}-{i}", 100 * h, t=h * HOUR + i))
        assert len(f.gens) <= 3 and f.size() <= 3 * 501 + 8
    assert f.check(row("old", 6, t=12 * HOUR)) is None          # forgotten after 3 h or more (bounded)


def _digest(run):
    return {"log_md5": run.log.md5(), "entries": {a: list(b.entries_ms) for a, b in run.books.items()}, "realized": {a: dict(b.realized) for a, b in run.books.items()}}


def _inject(rows):
    """Phantom copies of real rows: an all-'1' signature, a listed signer, and a repeat of a real signature at another slot with a wild price."""
    out = list(rows)
    trades = [r for r in rows if r["type"] == "trade"]
    for i, r in enumerate(trades[200:4000:97]):
        a = copy.deepcopy(r); a["signature"] = "1" * 64; a["event_index"] = 0; a["sol_lamports"] *= 50; a["quote_reserve"] *= 3
        b = copy.deepcopy(r); b["signature"] = f"phB{i}"; b["trader"] = BAD if i % 2 else BAD2; b["quote_reserve"] //= 3
        c = copy.deepcopy(r); c["slot"] += 7; c["t_recv_ms"] += 1; c["quote_reserve"] *= 2   # same signature as r, another slot
        out += [a, b, c]
    return sorted(out, key=lambda r: (r["t_recv_ms"], r["signature"], r["slot"])), len(trades[200:4000:97])


def test_runner_drops_phantoms_counts_them_and_decides_as_on_the_clean_tape(tmp_path_factory):
    clean = make(tmp_path_factory.mktemp("ph_clean"))
    clean.run(T0 + 9 * MIN + 2000, T0 + 110 * MIN)
    rows, n = _inject(trade_rows())
    d = tmp_path_factory.mktemp("ph_dirty")
    write_tape(d / "tape", rows)
    dirty = make(d)
    dirty.run(T0 + 9 * MIN + 2000, T0 + 110 * MIN)
    c = dirty.counters
    assert (c["phantom_drop_sig_ones"], c["phantom_drop_signer"], c["phantom_drop_repeat_slot"]) == (n, n, n) and n > 10
    assert _digest(dirty)["entries"] == _digest(clean)["entries"] and any(_digest(clean)["entries"].values())
    assert _digest(dirty)["realized"] == _digest(clean)["realized"] and not dirty.hook_errors
    # the clean log has no phantom field; the dirty SCAN rows carry the cumulative counts
    assert all("phantom_drops" not in r for r in rows_of(clean, "SCAN", "scan"))
    assert rows_of(dirty, "SCAN", "scan")[-1]["phantom_drops"] == {"sig_ones": n, "signer": n, "repeat_slot": n}
    assert dirty.summary()["phantom_drops"] == {"sig_ones": n, "signer": n, "repeat_slot": n}
    # with the filter off the phantoms reach the state: the same tape does change the book's inputs
    off = make(tmp_path_factory.mktemp("ph_off"), cfg_kw={"phantom_filter": False})
    assert off.phantom is None and "phantom_drops" not in off._phantom_fields()


def test_filter_on_equals_filter_off_when_no_phantom_rows(tmp_path_factory):
    out = {}
    for on in (True, False):
        run = make(tmp_path_factory.mktemp("ph_eq"), cfg_kw={"phantom_filter": on})
        run.run(T0 + 9 * MIN + 2000, T0 + 110 * MIN)
        out[on] = (_digest(run), run)
    assert out[True][0] == out[False][0] and any(out[True][0]["entries"].values())
    assert out[True][1].phantom.total() == 0 and not any(k.startswith("phantom") for k in out[True][1].counters)
    assert R.RunnerConfig().phantom_filter is True


def test_decision_slot_is_the_highest_slot_seen_even_when_a_late_pumpfun_print_arrives_last(tmp_path_factory):
    """QP feed-lag ruling 2026-10-26: the engine's slot clock anchors to the latest received row; the runner must pass the highest slot, not that row's slot."""
    run = make(tmp_path_factory.mktemp("ph_slot"))
    base = {"type": "trade", "t_recv_ms": T0, "event_index": 0, "trader": "w", "side": "buy"}
    run._on_row({**base, "prog": "pumpswap", "slot": 500, "signature": "a"})
    run._on_row({**base, "prog": "pumpfun", "slot": 200, "signature": "b"})   # a late bonding-curve print, 300 slots behind the head
    assert run.slot == 500
    run._on_row({**base, "prog": "pumpfun", "slot": 501, "signature": "c"})
    assert run.slot == 501
