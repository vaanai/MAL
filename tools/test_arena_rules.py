"""arena_rules host on a synthetic tape (scope items 1 to 7 of COHORT2-SCOPE section 3; item 8 is in test_arena_rules_h2ps.py). No network, no key, no real arena tape.

The tape is synthetic and flat: nothing here says any rule makes money."""

from __future__ import annotations

import ast
import collections
import errno
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools import arena_rules as R
from tools.arena_paper import load_registry
from tools.arena_rule_h2ps import make_rule
from tools.arena_rules import Buy, Host, HostError, HostFatal, Rule, position_id
from tools.arena_rules_testkit import (CLOCK, MIN, MINT, POOL, SHA, SHA40, T0, TICK_START, at, decision_files, decision_rows, ledger_rows, make_host, scenario_rows,
                                       swap)

T_END = T0 + 28 * MIN
BOT = "h2ps-a1"
REPO = Path(__file__).resolve().parent.parent
ROWS = scenario_rows(make_rule())


@pytest.fixture(scope="module", autouse=True)
def _arena_test_env():
    """register_bot's now_ms / verify_sha=False overrides are refused outside tests unless MAL_ARENA_TEST=1 (arena_paper)."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MAL_ARENA_TEST", "1")
        yield


@pytest.fixture(autouse=True)
def _plenty_of_disk(monkeypatch):
    """The ledger refuses appends below its free-space floor (25% free): no test may depend on how full the host's disk is."""
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


def full_run(tmp: Path) -> Host:
    h = make_host(tmp, [make_rule()], rows=ROWS)
    h.resume()
    h.run(TICK_START, T_END)
    return h


# -------------------------------------------------------------------------------------------------------------------------- (1) determinism
def test_two_replays_give_identical_decision_logs_and_ledger_rows(tmp_path):
    a, b = full_run(tmp_path / "a"), full_run(tmp_path / "b")
    fa, fb = decision_files(tmp_path / "a" / "rules", BOT), decision_files(tmp_path / "b" / "rules", BOT)
    assert fa and fa == fb  # sha256 of every day file
    assert ledger_rows(a, BOT) == ledger_rows(b, BOT)
    kinds = collections.Counter(r["kind"] for r in decision_rows(a, BOT))
    assert kinds["entry"] == 2 and kinds["exit"] == 2 and not a.hook_errors  # the replay is not vacuous
    led = collections.Counter(r["kind"] for r in ledger_rows(a, BOT))
    assert led["open"] == 2 and led["trade"] == 6  # both positions priced at 0.25 / 0.5 / 1.0
    a.log.verify(BOT)


# -------------------------------------------------------------------------------------------------------------------------- (2) kill + resume
@pytest.mark.parametrize("kill,n_open", [(T0 + 4 * MIN, 0), (T0 + 6 * MIN + 30_000, 1), (T0 + 15 * MIN, 1)])
def test_kill_and_resume_gives_the_same_decisions(tmp_path, kill, n_open):
    whole = full_run(tmp_path / "whole")
    split = tmp_path / "split"
    first = make_host(split, [make_rule()], rows=ROWS)
    first.resume()
    first.run(TICK_START, kill)
    del first  # killed: no shutdown hook runs, nothing is flushed that was not already fsynced
    second = make_host(split, [make_rule()], rows=ROWS)
    n = second.resume()
    assert n["open"] == n_open and n["reopened"] == 0 and n["dropped"] == 0
    second.run(kill, T_END)
    assert decision_files(split / "rules", BOT) == decision_files(tmp_path / "whole" / "rules", BOT)  # byte for byte: no marker row in a sim resume
    assert len([r for r in decision_rows(second, BOT) if r["kind"] == "entry"]) == 2  # the attempt memory survived: buy_A_after_exit did not re-enter
    if n_open:  # the position open at the kill is booked by the engine as excluded (engine_restart), as in hv4
        ex = [r for r in ledger_rows(second, BOT) if r["kind"] == "excluded"]
        assert len(ex) == 3 and {r["exclusion_reason"] for r in ex} == {"engine_restart"}


def test_resume_rebuilds_memory_from_the_log_and_reopens_engine_orphans(tmp_path):
    h = make_host(tmp_path, [make_rule()], rows=ROWS)
    h.resume()
    h.run(TICK_START, T0 + 6 * MIN)
    pid = next(iter(h.books[BOT].open))
    book = make_host(tmp_path, [make_rule()], rows=ROWS)
    book.resume()
    assert set(book.books[BOT].open) == {pid} and book.books[BOT].attempted == {MINT["A"]}
    # the engine holds a position the log shows as sold (the sell was written and accepted, never priced): it is re-opened and logged
    f = make_host(tmp_path / "f", [make_rule()], rows=ROWS, fake=True)
    f.log.append(BOT, T0 + 1, {"kind": "entry", "order_id": "p1", "pool": POOL["A"], "meta": {"wallet": "w", "mint": "m"}, "decision_slot": 1, "stake_sol": 0.25, "attempt_key": "m"})
    f.log.append(BOT, T0 + 2, {"kind": "exit", "order_id": "p1", "pool": POOL["A"], "reason": "wallet_sell", "decision_slot": 2, "attempt": 1})
    f.engine.positions["p1"] = type("P", (), {"bot_id": BOT, "pool": POOL["A"], "meta": {"wallet": "w", "mint": "m"}, "decision_ms": T0 + 1, "stamp_ms": T0 + 1, "decision_slot": 1})()
    f.engine.positions["p9"] = type("P", (), {"bot_id": BOT, "pool": POOL["A"], "meta": {}, "decision_ms": T0 + 3, "stamp_ms": T0 + 3, "decision_slot": 3})()
    n = f.resume()
    assert n["reopened"] == 2 and set(f.books[BOT].open) == {"p1", "p9"} and f.books[BOT].attempted == {"m"}
    assert [r["kind"] for r in f.log.rows(BOT)][-2:] == ["resume_reopen", "resume_reopen"]
    assert f.books[BOT].open["p1"].exit_reason == "wallet_sell" and f.books[BOT].open["p9"].exit_reason == "resume_reopen"  # the logged decision survives; an unlogged one gets a reason
    f.step(TICK_START)  # the first tick after the restart sends both sells (the exit decision row is written first, as always)
    assert [(c[0], c[2]) for c in f.engine.calls] == [("sell", "p1"), ("sell", "p9")] and all(c[-1] is True for c in f.engine.calls) and not f.books[BOT].open
    assert [r["attempt"] for r in f.log.rows(BOT) if r["kind"] == "exit"] == [1, 2, 1]  # p1: the logged attempt 1, then 2 after the restart; p9: its first
    g = make_host(tmp_path / "g", [make_rule()], rows=ROWS, fake=True)  # logged as an entry, never reached the engine (killed between the log and the buy)
    g.log.append(BOT, T0 + 1, {"kind": "entry", "order_id": "p2", "pool": POOL["A"], "meta": {}, "decision_slot": 1, "stake_sol": 0.25, "attempt_key": "m2"})
    n = g.resume()
    assert n["dropped"] == 1 and not g.books[BOT].open and g.books[BOT].attempted == {"m2"}  # still an attempt
    assert g.log.rows(BOT)[-1]["kind"] == "resume_dropped"


class Crash(BaseException):
    """The process dies here (not an Exception: nothing in the host may catch it)."""


def tick_at_or_after(ms: int) -> int:
    return TICK_START + -(-(ms - TICK_START) // 1000) * 1000


def test_a_kill_between_the_exit_row_and_engine_sell_is_sold_at_the_first_tick_after_resume(tmp_path):
    first = make_host(tmp_path, [make_rule()], rows=ROWS)  # the REAL engine
    first.resume()

    def die(*a, **k):
        raise Crash()

    first.engine.sell = die  # the exit row is on disk, engine.sell is never reached
    with pytest.raises(Crash):
        first.run(TICK_START, T_END)
    del first
    t_resume = tick_at_or_after(at("sell_A") + 1)  # the tick whose poll would have covered the wallet's sell
    second = make_host(tmp_path, [make_rule()], rows=ROWS)
    n = second.resume()
    assert n["open"] == 1 and n["reopened"] == 1
    (reopen,) = [r for r in decision_rows(second, BOT) if r["kind"] == "resume_reopen"]
    assert reopen["exit_reason"] == "wallet_sell"
    second.run(t_resume, T_END)
    rows = decision_rows(second, BOT)
    pid = next(r["order_id"] for r in rows if r["kind"] == "entry")
    exits = [r for r in rows if r["kind"] == "exit" and r["order_id"] == pid]
    assert [(x["attempt"], x["reason"]) for x in exits] == [(1, "wallet_sell"), (2, "wallet_sell")]  # the sell the wallet triggered, not a later time cap
    assert exits[1]["ms"] == t_resume and exits[1]["ms"] - exits[0]["ms"] < 1000  # sent at the first tick, 0.9 s after the logged decision (not 600 s)
    assert not any(r["reason"] == "time_cap_600s" and r["order_id"] == pid for r in rows if r["kind"] == "exit")
    ex = [r for r in ledger_rows(second, BOT) if r["kind"] == "excluded" and r["position_id"] == pid]
    assert len(ex) == 3 and {r["exclusion_reason"] for r in ex} == {"engine_restart"}  # booked, not left as a restart orphan
    assert pid not in second.engine.positions and pid not in second.books[BOT].open
    second.log.verify(BOT)


def test_a_broken_chain_stops_the_start(tmp_path):
    h = full_run(tmp_path / "a")
    f = h.log.files(BOT)[0]
    f.write_bytes(f.read_bytes().replace(b'"stake_sol":0.25', b'"stake_sol":0.50', 1))
    again = make_host(tmp_path / "a", [make_rule()], rows=ROWS)
    with pytest.raises(R.LogChainError):
        again.resume()


# -------------------------------------------------------------------------------------------------------------------------- (3) no order before the clock
class AlwaysBuy(Rule):
    """A test double that tries to buy on every PumpSwap A print and every tick, and records what the host shows it."""

    bot_id = "t-always"
    uses_ticks = True

    def __init__(self) -> None:
        self.rows: list[int] = []
        self.ticks: list[int] = []

    def on_row(self, ctx, row):
        self.rows.append(row["t_recv_ms"])
        return [Buy(row["pool"], {"sig": row["signature"]})] if row.get("type") == "trade" and row.get("pool") == POOL["A"] else []

    def on_tick(self, ctx, T):
        self.ticks.append(T)
        return [Buy(POOL["A"], {"tick": T})]


def test_no_order_before_clock_start_ms(tmp_path):
    rule = AlwaysBuy()
    h = make_host(tmp_path, [rule], rows=ROWS, fake=True)
    h.resume()
    h.run(TICK_START, T0 + 3 * MIN)
    rows = decision_rows(h, rule.bot_id)
    assert rows and all(r["ms"] >= CLOCK for r in rows)  # not one decision row stamped before the clock
    assert rows[0]["ms"] == CLOCK  # the print stamped exactly at the clock counts (>=)
    assert min(rule.rows) >= CLOCK and min(rule.ticks) >= CLOCK  # the rule was never even shown a row or a tick from before it
    assert len(h.engine.calls) == len(rows)
    # the host's own guard: an order stamped before the clock is dropped, counted, never logged
    n = len(decision_rows(h, rule.bot_id))
    h._submit(rule, [Buy(POOL["A"], {})], CLOCK - 1)
    assert len(decision_rows(h, rule.bot_id)) == n and h.counters["order_before_clock"] == 1


def test_rows_before_the_clock_still_feed_the_state_and_engine(tmp_path):
    rule = AlwaysBuy()
    h = make_host(tmp_path, [rule], rows=ROWS, fake=True)
    h.resume()
    h.run(TICK_START, CLOCK)  # ticks T < clock: nothing is shown to the rule
    assert rule.rows == [] and rule.ticks == [] and h.engine.rows_seen > 0 and h.state.pool(POOL["A"]) is not None


# -------------------------------------------------------------------------------------------------------------------------- (4) import closure and paths
BANNED_PARTS = ("r4b", "l3", "hv4", "haiku", "laya", "lightgbm", "anthropic", "openrouter")
FORBIDDEN_STRINGS = ("/data/mal/blocks", "/var/lib/mal/sealed", "tip-tape", "tip_tape", "tiptape", "forward-1016", "forward-1002", "/data/mal/r4b", "laya-opt", "models/r4b")
# Pre-existing hits in the engine's own import closure (tools/arena_paper.py imports SlotClock from tools/h5_shadow.py): a default path of the shadow's tip follower and its
# refusal list. Neither is called by this host; test_replay_opens_no_forbidden_path proves no such path is opened. A NEW hit anywhere fails the test.
KNOWN_ENGINE_STRING_HITS = {"tools.h5_shadow": {"/var/lib/mal/sealed", "forward-1002", "forward-1016", "tip-tape"}, "tools.pump_structure_monitor": {"forward-1002"}}


def _modfile(name: str) -> Path | None:
    p = REPO / (name.replace(".", "/") + ".py")
    if p.exists():
        return p
    p = REPO / name.replace(".", "/") / "__init__.py"
    return p if p.exists() else None


def closure(roots: list[str]) -> tuple[dict[str, Path], set[str]]:
    """Static import closure inside the repo (AST, no import) and the set of external top-level packages imported along the way."""
    seen: dict[str, Path] = {}
    external: set[str] = set()

    def walk(name: str) -> None:
        if name in seen:
            return
        f = _modfile(name)
        if f is None:
            external.add(name.split(".")[0])
            return
        seen[name] = f
        for n in ast.walk(ast.parse(f.read_text())):
            if isinstance(n, ast.Import):
                for a in n.names:
                    walk(a.name)
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                walk(n.module)
                for a in n.names:
                    if _modfile(f"{n.module}.{a.name}"):
                        walk(f"{n.module}.{a.name}")

    for r in roots:
        walk(r)
    return seen, external


def _roots() -> list[str]:
    return ["tools.arena_rules", "tools.arena_declog", *R.RULE_MODULES.values()]  # the rule modules are loaded by name, so they are roots too


def test_import_closure_has_no_model_laya_r4b_lightgbm_or_anthropic_code():
    mods, external = closure(_roots())
    assert {"tools.arena_rules", "tools.arena_rule_h2ps", "tools.arena_declog", "tools.arena_paper", "tools.arena_state"} <= set(mods)
    bad = sorted(m for m in [*mods, *external] if any(p in m.lower() for p in BANNED_PARTS))
    assert not bad, bad


def test_no_string_in_the_closure_reaches_a_sealed_or_blocks_path():
    mods, _ = closure(_roots())
    hits: dict[str, set[str]] = {}
    for name, f in mods.items():
        for n in ast.walk(ast.parse(f.read_text())):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                for s in FORBIDDEN_STRINGS:
                    if s in n.value:
                        hits.setdefault(name, set()).add(s)
    assert hits == KNOWN_ENGINE_STRING_HITS, hits
    for mine in ("tools.arena_rules", "tools.arena_rule_h2ps", "tools.arena_declog"):  # this PR's own modules carry none, not even in a docstring
        assert mine not in hits


_OPENED: list[str] | None = None


def _audit(event: str, args: tuple) -> None:
    if _OPENED is not None and event in ("open", "os.scandir", "os.listdir") and args and isinstance(args[0], (str, bytes, os.PathLike)):
        _OPENED.append(os.fsdecode(args[0]))


sys.addaudithook(_audit)


def test_replay_opens_no_forbidden_path(tmp_path):
    global _OPENED
    _OPENED = []
    try:
        full_run(tmp_path)
        opened = list(_OPENED)
    finally:
        _OPENED = None
    assert any(p.startswith(str(tmp_path / "tape")) for p in opened) and any(p.startswith(str(tmp_path / "rules")) for p in opened)  # the hook sees the host's files
    bad = [p for p in opened if any(s in p for s in (*FORBIDDEN_STRINGS, "/data/mal/arena/tape", "/data/mal/arena/ledger", "/data/mal/arena/bots.json", "/data/mal/arena/rules"))]
    assert not bad, bad[:3]


# -------------------------------------------------------------------------------------------------------------------------- (5) the decision row is on disk before the order
def test_decision_row_is_on_disk_before_buy_and_sell(tmp_path):
    h = make_host(tmp_path, [make_rule()], rows=ROWS, fake=True)  # FakeEngine.buy / sell raise AssertionError unless the decision row is already on disk
    h.resume()
    h.run(TICK_START, T_END)
    assert h.fatal is None and not h.hook_errors
    kinds = collections.Counter(c[0] for c in h.engine.calls)
    assert kinds == {"buy": 2, "sell": 2} and all(c[-1] is True for c in h.engine.calls)


def test_if_the_log_cannot_be_written_nothing_is_sent_and_the_host_stops(tmp_path):
    h = make_host(tmp_path, [make_rule()], rows=ROWS, fake=True)
    h.resume()

    def boom(*a, **k):
        raise OSError(28, "No space left on device")

    h.log.append = boom
    with pytest.raises(HostFatal):
        h.run(TICK_START, T_END)
    assert h.engine.calls == [] and not h.books[BOT].open and not h.books[BOT].attempted


class EveryPrint(Rule):
    """Buys pool A on every A print: several orders in ONE poll batch."""

    bot_id = "t-every"

    def on_row(self, ctx, row):
        return [Buy(POOL["A"], {"sig": row["signature"]})] if row.get("type") == "trade" and row.get("pool") == POOL["A"] else []


class PairAtTick(Rule):
    """Two buys (pools A and E) in one tick's batch."""

    bot_id = "t-pair"
    uses_ticks = True

    def __init__(self, at_tick: int) -> None:
        self.at_tick = at_tick

    def on_tick(self, ctx, T):
        return [Buy(POOL["A"], {"i": 1}), Buy(POOL["E"], {"i": 2})] if T == self.at_tick else []


def fail_nth_append(h: Host, nth: int) -> list[int]:
    """Make the nth call of the host's log.append raise ENOSPC (once; every other call works). Returns the list of call numbers seen."""
    real, calls = h.log.append, []

    def append(*a, **k):
        calls.append(len(calls) + 1)
        if len(calls) == nth:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(*a, **k)

    h.log.append = append
    return calls


def test_a_failed_append_mid_batch_stops_the_host_at_once_row_path(tmp_path):
    T = T0 + 10_000
    rows = [swap(T - 4 + i, "A", "FILL0", "buy", i) for i in range(4)]  # four A prints, all in the batch of tick T
    h = make_host(tmp_path, [EveryPrint()], rows=rows, fake=True, clock=0, register=False)
    calls = fail_nth_append(h, 2)  # the 1st decision row is written and sent, the 2nd append fails, the 3rd and 4th would have worked
    h.catch_up(T - 1000)
    with pytest.raises(HostFatal):
        h.step(T)
    assert calls == [1, 2]  # no third append was even attempted
    assert [r["kind"] for r in h.log.rows("t-every")] == ["entry"] and [c[0] for c in h.engine.calls] == ["buy"]  # nothing after the failed write is logged or sent
    assert isinstance(h.fatal, OSError) and h.fatal.errno == errno.ENOSPC
    with pytest.raises(HostFatal):  # a dead host stays dead
        h.step(T + 1000)
    assert len(h.log.rows("t-every")) == 1 and len(h.engine.calls) == 1 and calls == [1, 2]


def test_a_failed_append_mid_batch_stops_the_host_at_once_tick_path(tmp_path):
    T = T0 + 10_000
    rule = PairAtTick(T)
    h = make_host(tmp_path, [rule], rows=[swap(T - 5, "A", "FILL0", "buy", 1), swap(T - 4, "E", "FILL0", "buy", 2)], fake=True, clock=0, register=False)
    calls = fail_nth_append(h, 2)
    h.catch_up(T - 1000)
    with pytest.raises(HostFatal):
        h.step(T)
    assert calls == [1, 2] and [r["pool"] for r in h.log.rows(rule.bot_id)] == [POOL["A"]] and [c[2] for c in h.engine.calls] == [POOL["A"]]


def test_a_failed_append_while_retrying_sells_sends_no_sell(tmp_path):
    h = make_host(tmp_path, [make_rule()], rows=ROWS, fake=True)
    h.resume()
    from tools.arena_rules import OpenPos

    for pid in ("p1", "p2"):  # two positions whose exit decision is pending a retry
        h.engine.positions[pid] = type("P", (), {"bot_id": BOT})()
        h.books[BOT].add(OpenPos(pid, POOL["A"], {}, T0, 1, exit_reason="wallet_sell"))
    calls = fail_nth_append(h, 1)
    with pytest.raises(HostFatal):
        h.step(TICK_START)
    assert calls == [1] and h.engine.calls == [] and h.log.rows(BOT) == []


def test_a_refused_buy_is_logged_leaves_no_position_and_still_counts_as_the_attempt(tmp_path):
    h = make_host(tmp_path, [make_rule()], rows=ROWS, fake=True, refuse_buy=True)
    h.resume()
    h.run(TICK_START, T_END)
    rows = decision_rows(h, BOT)
    assert [r["kind"] for r in rows] == ["entry", "order_refused", "entry", "order_refused"]  # mints A and E, once each, however many followed buys came later
    assert all(r["side"] == "buy" and r["reason"] == "clock_not_ready" for r in rows if r["kind"] == "order_refused")
    assert not h.books[BOT].open and len(h.books[BOT].attempted) == 2 and h.counters["entries_refused"] == 2


# -------------------------------------------------------------------------------------------------------------------------- (6) tick mode and as-of
class Probe(Rule):
    bot_id = "t-probe"
    uses_ticks = True

    def __init__(self) -> None:
        self.max_row = 0
        self.seen: list[tuple[int, int, int]] = []

    def on_row(self, ctx, row):
        self.max_row = max(self.max_row, row["t_recv_ms"])
        return []

    def on_tick(self, ctx, T):
        self.seen.append((T, self.max_row, ctx.state.now_ms))
        return []


def test_tick_never_reads_a_row_at_or_after_T(tmp_path):
    T = T0 + 10_000
    rows = [swap(t, "A", "FILL0", "buy", i) for i, t in enumerate((T - 1001, T - 1, T, T + 1, T + 999, T + 1000))]
    p = Probe()
    h = make_host(tmp_path, [p], rows=rows, fake=True, clock=0, register=False)
    h.catch_up(T - 1000)
    h.step(T)
    assert p.seen[-1] == (T, T - 1, T - 1)  # the row stamped T - 1 is in, the row stamped T is not
    h.step(T + 1000)
    assert p.seen[-1] == (T + 1000, T + 999, T + 999)  # T, T + 1 and T + 999 arrive at the next tick; T + 1000 waits for the one after
    h.step(T + 2000)
    assert p.seen[-1][1] == T + 1000


def test_over_a_whole_run_every_tick_sees_only_rows_before_it(tmp_path):
    p = Probe()
    h = make_host(tmp_path, [p], rows=ROWS, fake=True, clock=0, register=False)
    h.run(TICK_START, T_END)
    assert len(p.seen) == (T_END - TICK_START) // 1000
    assert all(max_row < T and now < T for T, max_row, now in p.seen)


# -------------------------------------------------------------------------------------------------------------------------- (7) universe guard (host side)
class Rogue(Rule):
    """Buys whatever it is shown, on any venue, any quote, any pool, once per print."""

    bot_id = "t-rogue"

    def on_row(self, ctx, row):
        return [Buy(row.get("pool") or row["mint"], {})] if row.get("type") == "trade" else []


def test_host_drops_every_buy_outside_pumpswap_wsol(tmp_path):
    h = make_host(tmp_path, [Rogue()], rows=ROWS, fake=True)
    h.resume()
    h.run(TICK_START, T0 + 3 * MIN)
    pools = {c[2] for c in h.engine.calls}
    assert pools and pools <= {POOL["A"], POOL["D"], POOL["E"]}  # not the non-WSOL pool B, not the bonding curve (its id is the mint), not an unknown id
    assert {r["pool"] for r in decision_rows(h, "t-rogue") if r["kind"] == "entry"} == pools
    assert h.counters["blocked_universe"] > 0
    bonding = [r for r in ROWS if r.get("prog") == "pumpfun"]
    assert bonding and not any(r.get("pool") in (POOL["B"], bonding[0]["mint"]) for r in decision_rows(h, "t-rogue"))


def test_unverified_quote_is_outside_the_universe(tmp_path):
    rows = [{"v": 1, "type": "trade", "prog": "pumpswap", "venue": "pumpswap", "slot": 400_000_000 + i, "t_recv_ms": T0 + 1000 * i, "signature": f"u{i}", "event_index": 0,
             "pool": "PoolU" + "1" * 39, "mint": None, "quote_mint": None, "trader": "T", "side": "buy", "sol_lamports": 1, "token_raw": 1, "quote_reserve": 1, "base_reserve": 1}
            for i in range(5)]
    h = make_host(tmp_path, [Rogue()], rows=rows, fake=True, clock=0, register=False)
    h.run(T0, T0 + 10_000)
    assert h.engine.calls == [] and h.counters["blocked_universe"] == 5


# -------------------------------------------------------------------------------------------------------------------------- registration, one sha, ids
def test_position_id_matches_hv4_runner_and_the_engine(tmp_path):
    from tools import hv4_runner

    assert position_id("b", "pool", 1790316000123, 400_000_001) == hv4_runner.position_id("b", "pool", 1790316000123, 400_000_001)
    h = full_run(tmp_path)
    opens = [r for r in ledger_rows(h, BOT) if r["kind"] == "open"]
    entries = [r for r in decision_rows(h, BOT) if r["kind"] == "entry"]
    assert [o["position_id"] for o in opens] == [e["order_id"] for e in entries]  # the logged order id is the engine's position id
    assert all(o["decision_ms"]["entry"] == e["ms"] and o["bot_sol_in"] == 0.25 for o, e in zip(opens, entries))


def test_registration_line_has_the_rules_stake_and_no_model(tmp_path):
    make_host(tmp_path, [make_rule()], rows=ROWS)
    e = load_registry(tmp_path / "bots.json")[BOT]
    assert e["deciding_stake"] == 0.25 and e["model_ids"] == [] and e["clock_start_ms"] == CLOCK and e["frozen_sha"] == SHA


def test_start_refuses_unregistered_mismatched_or_stopped_books(tmp_path):
    h = make_host(tmp_path / "ok", [make_rule()], rows=ROWS, register=False)
    with pytest.raises(HostError, match="not registered"):
        h.resume()
    h = make_host(tmp_path / "clock", [make_rule()], rows=ROWS)
    h.cfg.clock_start_ms += 1
    with pytest.raises(HostError, match="clock"):
        h.resume()
    h = make_host(tmp_path / "sha", [make_rule()], rows=ROWS)
    h.cfg.frozen_sha = "fedcba9876543"
    with pytest.raises(HostError, match="registered at sha"):
        h.resume()


def test_one_process_hosts_books_of_one_frozen_sha(tmp_path):
    a, b = make_host(tmp_path, [make_rule()], rows=ROWS), Rogue()
    R.register_rules([b], str(tmp_path / "bots.json"), "0123456789abc", CLOCK, now_ms=T0 - 1, verify_sha=False)  # a second book, registered at ANOTHER sha
    both = make_host(tmp_path, [make_rule(), b], rows=ROWS, register=False)
    with pytest.raises(HostError, match="one process hosts books of one sha"):
        both.resume()
    a.resume()  # the single-sha process is fine


def test_duplicate_or_missing_rules_are_refused(tmp_path):
    with pytest.raises(HostError):
        make_host(tmp_path / "x", [make_rule(), make_rule()], rows=ROWS)
    with pytest.raises(HostError):
        make_host(tmp_path / "y", [], rows=ROWS)


# -------------------------------------------------------------------------------------------------------------------------- live mode (fake wall clock)
def live_host(tmp: Path, lag_ms: int, **kw) -> tuple[Host, dict]:
    """A live host whose wall clock is the state clock + lag (so a row is never 'from the future'), advanced by the loop's own sleeps."""
    clk = {"now": TICK_START}
    holder: dict = {}

    def now() -> int:
        h = holder.get("h")
        return max(clk["now"], (h.state.now_ms if h else 0) + lag_ms)

    h = make_host(tmp, [make_rule()], rows=ROWS, fake=True, live=True, now_fn=now, binding={"git_sha": SHA40, "dirty": False}, **kw)
    holder["h"] = h
    return h, clk


def test_live_decision_ms_is_the_wall_clock_and_start_is_logged(tmp_path):
    h, clk = live_host(tmp_path, 30)
    h.resume()
    h.run(TICK_START, T0 + 8 * MIN + 10_000, sleep=lambda s: clk.__setitem__("now", clk["now"] + int(s * 1000)))
    rows = decision_rows(h, BOT)
    assert rows[0]["kind"] == "start" and rows[0]["binding"] == {"git_sha": SHA40, "dirty": False} and rows[0]["rules"] == [BOT]
    entry, exit_ = [r for r in rows if r["kind"] == "entry"][0], [r for r in rows if r["kind"] == "exit"][0]
    assert entry["ms"] == at("buy_A") + 30 and exit_["ms"] == at("sell_A") + 30  # wall clock at the write, not the row's stamp


def test_live_does_not_show_stale_rows_to_the_rules(tmp_path):
    h, clk = live_host(tmp_path, 60_000)  # every row is a minute old at dispatch: a restart's catch-up over old tape
    h.resume()
    h.run(TICK_START, T0 + 8 * MIN + 10_000, sleep=lambda s: clk.__setitem__("now", clk["now"] + int(s * 1000)))
    assert [r for r in decision_rows(h, BOT) if r["kind"] in ("entry", "exit")] == [] and h.counters["stale_rows"] > 0


def test_live_needs_a_clean_checkout_at_the_frozen_sha(tmp_path):
    for i, binding in enumerate((None, {"git_sha": SHA40, "dirty": True}, {"git_sha": "1" * 40, "dirty": False}, {"git_sha": None, "dirty": None})):
        with pytest.raises(HostError):
            make_host(tmp_path / str(i), [make_rule()], rows=ROWS, fake=True, live=True, binding=binding)


# -------------------------------------------------------------------------------------------------------------------------- CLI and job script
def test_replay_refuses_to_write_inside_the_arena_tree(tmp_path, capsys):
    base = ["--rules", BOT, "--mode", "replay", "--frozen-sha", SHA, "--clock-start-ms", "1", "--from-ms", "1", "--to-ms", "2"]
    assert R.main(base) == 2  # default root / ledger / registry are inside /data/mal/arena
    assert "refuses to write inside" in capsys.readouterr().err
    assert R.main([*base, "--root", str(tmp_path / "r"), "--ledger-root", str(tmp_path / "l"), "--registry", "/data/mal/arena/bots.json"]) == 2
    assert not (tmp_path / "r").exists() and not (tmp_path / "l").exists()
    assert R.main(["--rules", "nope", "--frozen-sha", SHA, "--clock-start-ms", "1"]) == 2


SCRIPT = REPO / "scripts" / "research" / "arena-rules-job.sh"


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env=env).stdout.strip()


def _job(repo: Path, extra_env: dict | None = None, sh_args: tuple = ()) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ["PATH"], "HOME": str(repo), "ARENA_RULES_REPO": str(repo), "PYTHON": "echo", "ARENA_RULES": BOT, "ARENA_RULES_CLOCK_START_MS": str(CLOCK),
           "ARENA_RULES_FROZEN_SHA": _git(repo, "rev-parse", "HEAD"), **(extra_env or {})}
    return subprocess.run(["sh", *sh_args, str(SCRIPT)], capture_output=True, text=True, env=env, cwd=str(repo))


@pytest.fixture()
def tiny_repo(tmp_path):
    r = tmp_path / "repo"
    (r / "tools").mkdir(parents=True)
    (r / "tools" / "arena_rules.py").write_text("print('x')\n")
    _git(r, "init", "-q")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "x")
    return r


def test_job_script_is_posix_and_has_no_bashisms():
    text = SCRIPT.read_text()
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))  # the header comments may name what is not used
    assert text.startswith("#!/bin/sh") and subprocess.run(["sh", "-n", str(SCRIPT)]).returncode == 0
    for bad in ("PIPESTATUS", "[[", "source ", "<<<", "set -o pipefail", "declare ", "local ", "echo -e"):
        assert bad not in code, bad
    assert "KEY_FILE" not in text and "_API_KEY" not in text  # no credential of any kind


def test_job_script_runs_clean_checkout_and_passes_the_arguments(tiny_repo):
    p = _job(tiny_repo, {"ARENA_RULES_END_MS": "123", "ARENA_RULES_ROOT": "/x/r"})
    assert p.returncode == 0, p.stderr
    out = p.stdout
    for frag in ("tools/arena_rules.py --mode run --rules h2ps-a1", "--root /x/r", f"--frozen-sha {_git(tiny_repo, 'rev-parse', 'HEAD')}", f"--clock-start-ms {CLOCK}",
                 "--feed-delay-slots 7", "--end-ms 123"):
        assert frag in out, frag


def test_job_script_refuses_dirty_wrong_sha_xtrace_and_missing_env(tiny_repo):
    (tiny_repo / "stray.txt").write_text("x")  # an untracked file is a dirty checkout
    p = _job(tiny_repo)
    assert p.returncode == 3 and "dirty" in p.stderr
    (tiny_repo / "stray.txt").unlink()
    p = _job(tiny_repo, {"ARENA_RULES_FROZEN_SHA": "0" * 12})
    assert p.returncode == 3 and "not the frozen sha" in p.stderr
    p = _job(tiny_repo, sh_args=("-x",))
    assert p.returncode == 2 and "xtrace" in p.stderr
    for missing in ("ARENA_RULES_FROZEN_SHA", "ARENA_RULES_CLOCK_START_MS", "ARENA_RULES"):
        env = {"PATH": os.environ["PATH"], "HOME": str(tiny_repo), "ARENA_RULES_REPO": str(tiny_repo), "PYTHON": "echo", "ARENA_RULES": BOT, "ARENA_RULES_CLOCK_START_MS": "1",
               "ARENA_RULES_FROZEN_SHA": "abc"}
        env.pop(missing)
        assert subprocess.run(["sh", str(SCRIPT)], capture_output=True, text=True, env=env, cwd=str(tiny_repo)).returncode != 0
    assert _job(tiny_repo, {"ARENA_RULES_MODE": "bogus"}).returncode == 2
    assert _job(tiny_repo, {"ARENA_RULES_MODE": "replay"}).returncode != 0  # replay needs its window
