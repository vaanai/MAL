"""tools/arena_feed.py LateSeen / Feed.seen: a late re-delivery is not written again, and everything else on the tape is byte-identical to the pre-fix feed.

Synthetic notices only (the observe fixtures), fake clocks, a temp out-dir. No network, no arena tape, no ledger. The pre-fix feed is the git blob of
tools/arena_feed.py at f5bd26e7 (HV4's feed pin, sha256 7a9d424b...), run through the same stream; those tests skip when the blob is not in the clone."""

from __future__ import annotations

import collections
import hashlib
import json
import subprocess
import sys
import time
import types
from pathlib import Path
from typing import Any

import pytest

from observe.fake_ws import T0, fixture_line
from observe.trade_source import RawNotice, SeenSet
from tools import arena_feed as af

REPO = Path(__file__).resolve().parent.parent
PRE_FIX_BLOB = "d6969658f9d2f23f45a29b3ee785fc0d7b746774"  # tools/arena_feed.py at f5bd26e7, the feed before this fix
PRE_FIX_SHA256 = "7a9d424b6584b097ee61e4c7b48bef6e43b317bdd438ba588074057b5828826f"
HOUR = 3_600_000
H0 = T0 - T0 % HOUR  # start of the UTC hour that holds T0
DU = collections.namedtuple("DU", "total used free")
DATA_TYPES = ("trade", "create", "complete", "pool_create")


def roomy(_path) -> DU:
    return DU(10**12, 5 * 10**11, 5 * 10**11)


class Clock:
    """The replay's monotonic clock, in seconds: set from each stream element's receive time."""

    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


def sig_of(i: int) -> str:
    return f"{i:08d}" + "S" * 80  # 88 characters, like a base58 signature


def note(slot: int, sig: str, logs: list[str], t: int) -> RawNotice:
    return RawNotice(slot=slot, signature=sig, failed=False, logs=tuple(logs), t_recv_ms=t, commitment="confirmed", feed="public_rpc_logs")


BUY, SELL, BOND = fixture_line("pumpswap_buy_event.b64"), fixture_line("pumpswap_sell_event.b64"), fixture_line("pump_trade_event.b64")
LOGS = [[BUY], [SELL], [BOND], [BUY, SELL], [BOND, "Program log: x"], ["Program log: nothing to decode"]]  # the 4th tx has two events (event_index 0 and 1); the 6th none
PROGS = ("pumpswap", "pumpswap", "pumpfun", "pumpswap", "pumpfun", "pumpswap")


class Stream:
    """A list of (t_ms, prog, notice) in receive order. original() is the first copy of transaction i; repeat() adds a later copy of it."""

    def __init__(self) -> None:
        self.items: list[tuple[int, str, RawNotice]] = []
        self.first: dict[int, tuple[int, str, RawNotice]] = {}

    def original(self, t: int, i: int, slot: int | None = None) -> None:
        k = i % len(LOGS)
        self.first[i] = (t, PROGS[k], note(slot if slot is not None else 1000 + i, sig_of(i), LOGS[k], t))
        self.items.append(self.first[i])

    def repeat(self, i: int, delay_s: float, *, prog: str | None = None, slot: int | None = None) -> None:
        t0, p0, n0 = self.first[i]
        t = t0 + round(delay_s * 1000)
        self.items.append((t, prog or p0, note(n0.slot if slot is None else slot, n0.signature, list(n0.logs), t)))

    def ordered(self) -> list[tuple[int, str, RawNotice]]:
        return sorted(self.items, key=lambda x: x[0])  # stable: equal times keep insertion order


def base_stream(n: int = 100, step_s: int = 60, start_s: int = 600) -> Stream:
    """n distinct transactions step_s apart from H0 + start_s (two hours at the defaults), with the duplicates the OLD feed already dropped: a second
    socket's copy 0.3 s later, a copy through the other program 2 s later, and a copy 119 s later (inside the old 120 s window)."""
    s = Stream()
    for i in range(n):
        s.original(H0 + (start_s + i * step_s) * 1000, i, slot=1000 + 2 * i + (99 if i >= 40 else 0))  # a slot jump at i == 40
    for i in range(0, n, 7):
        s.repeat(i, 0.3)
    for i in range(3, n, 11):
        s.repeat(i, 2.0, prog="pumpfun" if s.first[i][1] == "pumpswap" else "pumpswap")
    for i in range(5, n, 13):
        s.repeat(i, 119.0)
    return s


def pre_fix_module() -> types.ModuleType:
    try:
        src = subprocess.run(["git", "cat-file", "blob", PRE_FIX_BLOB], cwd=REPO, capture_output=True, check=True, timeout=30).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("the pre-fix tools/arena_feed.py blob is not in this clone")
    assert hashlib.sha256(src).hexdigest() == PRE_FIX_SHA256
    mod = types.ModuleType("arena_feed_pre_fix")
    mod.__package__, mod.__file__ = "tools", str(REPO / "tools" / "arena_feed.py")
    sys.modules["arena_feed_pre_fix"] = mod
    try:
        exec(compile(src, "arena_feed_pre_fix.py", "exec"), mod.__dict__)
    finally:
        sys.modules.pop("arena_feed_pre_fix", None)
    return mod


def run(mod, out: Path, items: list[tuple[int, str, RawNotice]], monkeypatch, *, ticks: tuple[int, ...] = (), pre_fix: bool = False) -> tuple[dict[str, bytes], Any]:
    """Feed the notices (and housekeeping ticks at the given times) through mod.Feed on a replay clock. pre_fix=True: mod is the old file, and its
    Feed.seen is replaced by the old SeenSet(120 s, 200k) on the replay clock (the old Feed builds it on the real clock). Returns ({tape file: bytes}, feed);
    the status file is not compared (pid, rss)."""
    clock, wall = Clock(), [H0]
    with monkeypatch.context() as m:
        m.setattr(mod, "proc_rss_mb", lambda *_a, **_k: 1.0)
        m.setattr(time, "monotonic", clock)
        kw = {} if pre_fix else {"dedup_clock": clock}
        feed = mod.Feed(out, [], {"pumpswap": 1, "pumpfun": 1}, wall=lambda: wall[0], rpc_url=None, compress=False, disk_usage=roomy, **kw)
        if pre_fix:
            feed.seen = SeenSet(mod.DEDUP_WINDOW_S, mod.DEDUP_MAX, clock)
        events = [(t, 0, prog, n) for t, prog, n in items] + [(t, 1, None, None) for t in ticks]
        for t, _order, prog, n in sorted(events, key=lambda e: (e[0], e[1])):
            clock.t, wall[0] = 1_000_000.0 + (t - H0) / 1000.0, t
            if n is not None:
                feed.handle_notice(prog, n)
            else:
                feed.tick(t)
        feed.flush_pending(wall[0])
        feed.writer.close()
    return {p.name: p.read_bytes() for p in sorted(out.glob("tape-*.jsonl"))}, feed


def rows_of(files: dict[str, bytes]) -> list[dict]:
    return [json.loads(ln) for _name, b in sorted(files.items()) for ln in b.decode().splitlines()]


def lines_of(files: dict[str, bytes]) -> dict[str, list[bytes]]:
    return {n: b.splitlines() for n, b in sorted(files.items()) if b.splitlines()}


def trade_counts(files: dict[str, bytes]) -> collections.Counter:
    return collections.Counter((r["signature"], r["event_index"]) for r in rows_of(files) if r["type"] == "trade")


def without_exact_repeats(old: dict[str, bytes], window_s: float) -> dict[str, list[bytes]]:
    """The referee: the old tape's lines minus every data row that is an exact repeat (all fields but t_recv_ms) of an EARLIER data row with the same
    (prog, type, signature, event_index) first written less than window_s before it (tools/arena_tape_dupcount's rep_strict / xh_strict idea, with the
    window of the fix added). One more consequence is applied, because hb.rows_last_min counts the data rows WRITTEN in the last completed minute: an hb
    row loses one for each removed row of its program with t_recv_ms in that minute. Every other byte of every other row is kept."""
    first: dict[tuple, tuple[int, dict]] = {}
    kept: list[tuple[str, dict, bytes]] = []
    removed: collections.Counter = collections.Counter()  # (prog, minute) -> removed data rows
    for name, b in sorted(old.items()):
        for raw in b.splitlines():
            r = json.loads(raw)
            if r["type"] in DATA_TYPES:
                key = (r["prog"], r["type"], r["signature"], r["event_index"])
                body = {k: v for k, v in r.items() if k != "t_recv_ms"}
                f = first.get(key)
                if f is not None and f[1] == body and r["t_recv_ms"] - f[0] < window_s * 1000:
                    removed[(r["prog"], r["t_recv_ms"] // 60_000)] += 1
                    continue
                first.setdefault(key, (r["t_recv_ms"], body))
            kept.append((name, r, raw))
    out: dict[str, list[bytes]] = {}
    for name, r, raw in kept:
        if r["type"] == "hb":
            r["rows_last_min"] -= removed[(r["prog"], r["t_recv_ms"] // 60_000 - 1)]
            raw = json.dumps(r, separators=(",", ":"), allow_nan=False).encode()
        out.setdefault(name, []).append(raw)
    return out


# ---- LateSeen on its own -------------------------------------------------------------------------------------------
def test_constants_cover_the_qp_floor_and_the_measured_peak() -> None:
    assert af.LATE_DEDUP_WINDOW_S >= 900.0
    peak_per_s = 1404.617 + 396.858  # PumpSwap + pump.fun notices per second in the busiest minute QP measured (QP-RULING-1031-tape-dupes-reads s2.3)
    assert af.LATE_DEDUP_MAX >= 2 * peak_per_s * af.LATE_DEDUP_WINDOW_S  # no cap eviction before the window at twice the measured peak
    assert (af.DEDUP_WINDOW_S, af.DEDUP_MAX) == (120.0, 200_000)  # the per-program socket merge is not changed
    src = af.build_source("pumpswap", [], 2, "confirmed")
    assert (src.seen.window_s, src.seen.max_keys) == (120.0, 200_000)


def test_window_runs_from_the_first_sight_and_a_duplicate_does_not_refresh_it() -> None:
    clock = Clock()
    s = af.LateSeen(900.0, 1000, clock)
    t0 = clock.t
    assert s.add("a") is True and s.add("b") is True
    for delay in (0.3, 119.999, 120.0, 600.0, 899.999):
        clock.t = t0 + delay
        assert s.add("a") is False, delay
    assert s.dropped == 5 and s.dropped_late == 3  # 0.3 and 119.999 s are inside the old 120 s window; from 120.0 s on the old feed wrote the row again
    clock.t = t0 + 900.0
    assert s.add("a") is True  # exactly window_s after the first sight the key is gone, as in SeenSet: the documented residual
    assert s.add("b") is True and len(s) == 2


@pytest.mark.parametrize("delay", [121.0, 600.0, 899.0])
def test_the_old_set_forgets_a_late_copy_and_the_new_one_drops_it(delay: float) -> None:
    clock = Clock()
    old, new = SeenSet(af.DEDUP_WINDOW_S, af.DEDUP_MAX, clock), af.LateSeen(clock=clock)
    assert old.add("x") and new.add("x")
    clock.t += delay
    assert old.add("x") is True  # forgotten after 120 s: the old feed wrote this row a second time
    assert new.add("x") is False


def test_cap_evicts_the_oldest_and_counts_keys_that_were_still_inside_the_window() -> None:
    cap, window = 9_000, 900.0
    for factor, evicts in ((0.99, False), (1.2, True)):  # just under the cap rate, and 20% over it (scaled: cap / window keys a second is the limit)
        clock = Clock()
        s = af.LateSeen(window, cap, clock)
        t0, rate = clock.t, cap / window * factor
        assert s.add("first")
        for i in range(int(rate * 899)):
            clock.t = t0 + (i + 1) / rate
            s.add(f"k{i}")
        clock.t = t0 + 899.0
        assert s.add("first") is (evicts is True)  # under the cap rate the first key is still there at 899 s; over it, it was pushed out early
        assert (s.evicted_young > 0) is evicts and len(s) <= cap
        assert (s.high_water == cap) is evicts


def test_same_signature_other_content_keeps_the_first_and_counts_it() -> None:
    s = af.LateSeen(900.0, 100, Clock())
    assert s.add("sig", (False, 3, 100))
    assert s.add("sig", (False, 3, 100)) is False and s.last_mismatch is False
    assert s.add("sig", (False, 4, 100)) is False and s.last_mismatch is True and s.mismatched == 1
    assert s.add("sig", (False, 3, 101)) is False and s.last_mismatch is True and s.mismatched == 2
    assert s.add("sig", (False, 3, 100)) is False and s.last_mismatch is False  # still compared with the first sight


def test_distinct_signatures_never_collide_and_odd_keys_do_not_raise() -> None:
    s = af.LateSeen(900.0, 10_000, Clock())
    keys = [sig_of(i) for i in range(5000)] + [sig_of(i)[:-1] + "T" for i in range(5000)]
    assert all(s.add(k) for k in keys) and len(s) == 10_000
    assert s.add(None) is True and s.add(None) is False and s.add("\ud800") is True  # not a str, and a lone surrogate: no exception


def test_bad_arguments_are_refused() -> None:
    with pytest.raises(ValueError):
        af.LateSeen(0.0, 10)
    with pytest.raises(ValueError):
        af.LateSeen(900.0, 0)


# ---- Feed wiring ---------------------------------------------------------------------------------------------------
def test_feed_uses_late_seen_with_the_module_constants_and_reports_it(tmp_path: Path) -> None:
    feed = af.Feed(tmp_path, [], {"pumpswap": 1, "pumpfun": 1}, wall=lambda: T0, rpc_url=None, compress=False, disk_usage=roomy)
    assert isinstance(feed.seen, af.LateSeen) and (feed.seen.window_s, feed.seen.max_keys) == (af.LATE_DEDUP_WINDOW_S, af.LATE_DEDUP_MAX)
    st = feed.status(T0)
    assert st["late_dedup"] == {"window_s": 900.0, "max_keys": 3_300_000, "keys": 0, "high_water": 0, "dropped": 0, "dropped_late": 0, "mismatched": 0,
                                "evicted_young": 0}
    assert "cross_dedup_dropped" in st and "counters" in st  # the existing status keys stay
    feed.writer.close()


def test_feed_counts_late_drops_and_content_mismatches(tmp_path: Path, monkeypatch) -> None:
    s = Stream()
    s.original(H0 + 600_000, 0)
    s.repeat(0, 0.3)
    s.repeat(0, 200.0)
    s.repeat(0, 300.0, slot=s.first[0][2].slot + 1)  # same signature, another slot: the first sight is kept and the mismatch is counted
    files, feed = run(af, tmp_path, s.ordered(), monkeypatch)
    rows = [r for r in rows_of(files) if r["type"] == "trade"]
    assert len(rows) == 1 and rows[0]["slot"] == s.first[0][2].slot
    c = feed.counters
    assert (c["cross_dedup_dropped"], c["late_dedup_dropped"], c["dedup_content_mismatch"]) == (3, 2, 1)
    assert feed.status(H0)["late_dedup"]["dropped_late"] == 2


# ---- equivalence replays against the pre-fix feed ------------------------------------------------------------------
TICKS = tuple(range(H0 + 600_000, H0 + 7_190_000, 30_000))  # every 30 s: hb rows, silence checks, status writes


def test_no_late_duplicates_gives_byte_identical_tapes_old_and_new(tmp_path: Path, monkeypatch) -> None:
    items = base_stream().ordered()
    old, _ = run(pre_fix_module(), tmp_path / "old", items, monkeypatch, ticks=TICKS, pre_fix=True)
    new, feed = run(af, tmp_path / "new", items, monkeypatch, ticks=TICKS)
    assert set(old) == set(new) and len(old) == 2  # two hour files
    for name in old:
        assert hashlib.sha256(old[name]).hexdigest() == hashlib.sha256(new[name]).hexdigest(), name
    kinds = collections.Counter(r["type"] for r in rows_of(new))
    assert kinds["trade"] > 100 and kinds["hb"] > 0 and kinds["gap"] > 0  # the comparison covers data, hb and gap rows
    assert feed.seen.dropped_late == 0 and feed.counters["late_dedup_dropped"] == 0  # nothing late in this stream


def test_late_duplicates_are_the_only_rows_that_go_and_each_is_an_exact_repeat(tmp_path: Path, monkeypatch) -> None:
    s = base_stream()
    dropped = {10: 121.0, 12: 600.0, 13: 899.0, 15: 200.0, 16: 840.0, 60: 899.9}  # inside the new window, outside the old one
    written = {18: 900.0, 19: 1500.0}  # the documented residual: at or after the window both feeds write the second copy
    for i, d in {**dropped, **written, 20: 5.0}.items():  # 20: inside the old window as well, dropped by both
        s.repeat(i, d)
    items = s.ordered()
    old, _ = run(pre_fix_module(), tmp_path / "old", items, monkeypatch, ticks=TICKS, pre_fix=True)
    new, feed = run(af, tmp_path / "new", items, monkeypatch, ticks=TICKS)
    assert lines_of(new) == without_exact_repeats(old, af.LATE_DEDUP_WINDOW_S)  # old tape minus the exact repeats < 900 s apart: bytes, order and files
    co, cn = trade_counts(old), trade_counts(new)
    assert sum(cn.values()) < sum(co.values())
    for i in dropped:
        keys = [k for k in co if k[0] == sig_of(i)]
        assert keys and all(co[k] == 2 and cn[k] == 1 for k in keys), i  # the old tape has the event twice, the new one once
    for i in written:
        keys = [k for k in co if k[0] == sig_of(i)]
        assert keys and all(co[k] == 2 and cn[k] == 2 for k in keys), i
    assert all(cn[k] == co[k] == 1 for k in co if k[0] == sig_of(20))
    assert feed.counters["late_dedup_dropped"] == len(dropped)  # one count per notice, whatever its number of events
    assert feed.seen.dropped_late == feed.counters["late_dedup_dropped"]


def test_cross_hour_late_duplicate_is_dropped(tmp_path: Path, monkeypatch) -> None:
    s = Stream()
    s.original(H0 + 600_000, 0)
    s.original(H0 + HOUR - 100_000, 1)  # 58 min 20 s into hour 0: its copy 14 min later lands in the NEXT hour's file
    s.original(H0 + HOUR + 30_000, 2)
    s.repeat(1, 840.0)
    items = s.ordered()
    old, _ = run(pre_fix_module(), tmp_path / "old", items, monkeypatch, pre_fix=True)
    new, _ = run(af, tmp_path / "new", items, monkeypatch)

    def per_file(files: dict[str, bytes]) -> list[int]:
        return [sum(1 for ln in b.splitlines() if sig_of(1).encode() in ln) for _n, b in sorted(files.items())]

    assert per_file(old) == [1, 1] and per_file(new) == [1, 0]  # old: one row in each hour file; new: only the first
    assert lines_of(new) == without_exact_repeats(old, af.LATE_DEDUP_WINDOW_S)


def test_a_late_duplicate_is_still_a_sign_of_life_so_gap_rows_do_not_change(tmp_path: Path, monkeypatch) -> None:
    """The gap rules run before the filter, as before: a silence that only a late copy ends is closed in both tapes, at the same instants."""
    s = Stream()
    s.original(H0 + 600_000, 0)
    s.original(H0 + 630_000, 1)
    s.original(H0 + 700_000, 3)  # a PumpSwap tx; then nothing new from PumpSwap, and nothing at all from pump.fun
    s.repeat(3, 150.0)  # its copy at +150 s (old window: 120 s) is the only notice that ends PumpSwap's silence
    items, ticks = s.ordered(), tuple(range(H0 + 600_000, H0 + 1_000_000, 5_000))
    old, _ = run(pre_fix_module(), tmp_path / "old", items, monkeypatch, ticks=ticks, pre_fix=True)
    new, _ = run(af, tmp_path / "new", items, monkeypatch, ticks=ticks)

    def kind(files: dict[str, bytes], t: str) -> list[bytes]:
        return [ln for _n, b in sorted(files.items()) for ln in b.splitlines() if f'"type":"{t}"'.encode() in ln]

    def hb_without_rows_last_min(files: dict[str, bytes]) -> list[dict]:
        return [{k: v for k, v in json.loads(ln).items() if k != "rows_last_min"} for ln in kind(files, "hb")]  # that field counts rows written: see the referee

    assert kind(old, "gap") == kind(new, "gap") and hb_without_rows_last_min(old) == hb_without_rows_last_min(new) and len(kind(new, "hb")) >= 4
    ended_by_the_copy = [g for g in map(json.loads, kind(new, "gap")) if g["kind"] == "silence" and g.get("closed") and g["to_ms"] == H0 + 850_000]
    assert len(ended_by_the_copy) == 1 and ended_by_the_copy[0]["prog"] == "pumpswap"
    assert lines_of(new) == without_exact_repeats(old, af.LATE_DEDUP_WINDOW_S)
    assert trade_counts(old)[(sig_of(3), 0)] == 2 and trade_counts(new)[(sig_of(3), 0)] == 1


def test_pre_fix_blob_is_the_hv4_feed_pin() -> None:
    """Replaces tools/test_feed_handover_proof.py::test_arena_feed_v1_untouched, which hashed the file on main: HV4's pin is the blob at f5bd26e7."""
    pre_fix_module()  # asserts the sha256 and skips when the blob is absent
    out = subprocess.run(["git", "rev-parse", "f5bd26e7:tools/arena_feed.py"], cwd=REPO, capture_output=True, text=True, timeout=30)
    if out.returncode == 0:
        assert out.stdout.strip() == PRE_FIX_BLOB
