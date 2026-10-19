"""h2ps-a1 (H2 Book A restricted to PumpSwap pools quoted in WSOL): the pinned wallet list, one attempt per mint, the exit rules, the universe. Synthetic tape only.

h2ps-a1 is a harness fixture and is not registered by this PR. Nothing here says the rule makes money."""

from __future__ import annotations

import ast
import collections
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import arena_rule_h2ps as H
from tools.arena_rule_h2ps import H2PSRule, WalletListError, make_rule
from tools.arena_rules import Buy, PositionView, Sell
from tools.arena_rules_testkit import MIN, MINT, OTHER, POOL, T0, TICK_START, V, WSOL, at, decision_rows, ledger_rows, make_host, scenario_rows, swap, wallets

REPO = Path(__file__).resolve().parent.parent
BOT = "h2ps-a1"
RULE = make_rule()
W1, W2, W3 = wallets(RULE)[:3]
ROWS = scenario_rows(RULE)
T_END = T0 + 28 * MIN


@pytest.fixture(scope="module", autouse=True)
def _arena_test_env():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MAL_ARENA_TEST", "1")
        yield


@pytest.fixture(autouse=True)
def _plenty_of_disk(monkeypatch):
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


# -------------------------------------------------------------------------------------------------------------------------- the pinned wallet list
def test_wallet_file_sha256_is_pinned_to_the_frozen_list():
    data = H.WALLETS_PATH.read_bytes()
    assert H.WALLETS_SHA256.startswith("345d3903") and hashlib.sha256(data).hexdigest() == H.WALLETS_SHA256  # RULE.md: frozen_wallets.txt sha256 345d3903...
    assert H.WALLETS_SHA256 == "345d390311579d1e61b5b622417e02d583244895c5909f19055eab42e9335499"
    assert len(RULE.wallets) == H.N_WALLETS == 50 and all(32 <= len(w) <= 44 for w in RULE.wallets)
    assert H.WALLETS_PATH == REPO / "data" / "arena" / "h2ps_wallets.txt"


def test_a_changed_wallet_list_is_refused(tmp_path):
    data = H.WALLETS_PATH.read_bytes()
    for name, blob in (("extra", data + b"11111111111111111111111111111111\n"), ("crlf", data.replace(b"\n", b"\r\n")), ("empty", b""), ("drop_last", data.rsplit(b"\n", 2)[0] + b"\n")):
        p = tmp_path / name
        p.write_bytes(blob)
        with pytest.raises(WalletListError):
            make_rule(p)
    p = tmp_path / "same"
    p.write_bytes(data)
    assert make_rule(p).wallets == RULE.wallets  # the sha, not the path, is what is pinned


def test_rule_identity_and_registration_line_inputs():
    assert (RULE.bot_id, RULE.deciding_stake, RULE.model_ids, RULE.universe, RULE.uses_ticks) == (BOT, 0.25, (), "pumpswap_wsol", True)
    assert H.V_LO == 17_500_000_000 and H.V_HI == 17_700_000_000  # RULE.md's canonical-pool band is kept
    assert H.HOLD_MS == 600_000 + 1_900


def test_module_is_pure_by_construction():
    tree = ast.parse((REPO / "tools" / "arena_rule_h2ps.py").read_text())
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert mods <= {"__future__", "hashlib", "pathlib", "typing", "tools.arena_rules", "tools.arena_state"}  # no time, os, random, socket, json, open() of a feed
    for fn in ("on_row", "on_tick"):
        node = next(n for c in tree.body if isinstance(c, ast.ClassDef) and c.name == "H2PSRule" for n in c.body if isinstance(n, ast.FunctionDef) and n.name == fn)
        calls = {c.func.id for c in ast.walk(node) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        assert not calls & {"open", "print", "input", "exec", "eval", "__import__"}


# -------------------------------------------------------------------------------------------------------------------------- the rule as a pure function
class FakeState:
    def __init__(self, pools=None):
        self.pools = pools or {}

    def pool(self, key):
        return self.pools.get(key)


def pool(quote=WSOL, mint=MINT["A"], v=V, prog="pumpswap"):
    return SimpleNamespace(prog=prog, quote_mint=quote, mint=mint, v=v)


def ctx(pools=None, attempted=(), positions=()):
    return SimpleNamespace(state=FakeState(pools if pools is not None else {POOL["A"]: pool()}), attempted=frozenset(attempted), positions=tuple(positions), now_ms=0)


def row(side="buy", trader=W1, prog="pumpswap", venue="pumpswap", pool_id=POOL["A"], mint=MINT["A"], quote=WSOL, v=V, **kw):
    r = {"type": "trade", "prog": prog, "venue": venue, "pool": pool_id, "mint": mint, "trader": trader, "side": side, "quote_mint": quote, "virtual_quote_reserves": v, "slot": 7,
         "signature": "sig", "t_recv_ms": T0 + 5}
    r.update(kw)
    return r


def test_followed_buy_on_a_wsol_pumpswap_pool_makes_one_buy_keyed_by_the_mint():
    out = RULE.on_row(ctx(), row())
    assert out == [Buy(POOL["A"], {"rule": BOT, "wallet": W1, "mint": MINT["A"], "signal_slot": 7, "signal_sig": "sig", "signal_t_recv_ms": T0 + 5, "v": V}, attempt_key=MINT["A"])]


@pytest.mark.parametrize("r,c", [
    (row(prog="pumpfun", venue="pump_bonding", pool_id=None), ctx()),  # a bonding print is never a signal, even for a followed wallet with a WSOL quote
    (row(venue="pump_bonding"), ctx()),
    (row(quote=OTHER), ctx({POOL["A"]: pool(quote=OTHER)})),  # not WSOL
    (row(quote=None), ctx({POOL["A"]: pool(quote=None)})),  # not verified
    (row(quote=None), ctx({})),  # an unknown pool
    (row(quote=WSOL), ctx({POOL["A"]: pool(quote=OTHER)})),  # the state says otherwise
    (row(trader="NOTFOLLOWED"), ctx()),
    (row(trader=None), ctx()),
    (row(), ctx(attempted={MINT["A"]})),  # one attempt per mint
    (row(v=17_499_999_999), ctx()),  # canonical band, low edge
    (row(v=17_700_000_001), ctx()),  # canonical band, high edge
    (row(v=None), ctx({POOL["A"]: pool(v=0)})),
    (row(pool_id=None), ctx()),
    (row(mint=None), ctx({POOL["A"]: pool(mint=None)})),
    (row(type="create"), ctx()),
    (row(side="other"), ctx()),
])
def test_rows_that_must_not_make_an_order(r, c):
    assert RULE.on_row(c, r) == []


def test_band_edges_are_inclusive_and_the_pool_v_is_used_when_the_print_has_none():
    for v in (H.V_LO, H.V_HI):
        assert len(RULE.on_row(ctx(), row(v=v))) == 1
    assert len(RULE.on_row(ctx({POOL["A"]: pool(v=V)}), row(v=None))) == 1


def position(pid="p1", wallet=W1, mint=MINT["A"], decision_ms=T0 + 1000, exiting=False):
    return PositionView(pid, POOL["A"], {"wallet": wallet, "mint": mint}, decision_ms, 1, exiting)


def test_exit_on_the_entering_wallets_sell_of_the_mint_only():
    pos = position()
    assert RULE.on_row(ctx(positions=[pos]), row(side="sell")) == [Sell("p1", "wallet_sell")]
    assert RULE.on_row(ctx(positions=[pos]), row(side="sell", trader=W2)) == []  # another followed wallet
    assert RULE.on_row(ctx(positions=[pos]), row(side="sell", trader="ZED")) == []
    assert RULE.on_row(ctx({POOL["E"]: pool(mint=MINT["E"])}, positions=[pos]), row(side="sell", pool_id=POOL["E"], mint=MINT["E"])) == []  # another mint
    assert RULE.on_row(ctx(positions=[position(exiting=True)]), row(side="sell")) == []  # an exit is already being sent
    assert RULE.on_row(ctx(positions=[pos]), row(side="sell", quote=OTHER)) == []  # a sell on a non-WSOL print is not the signal
    assert RULE.on_row(ctx(positions=[pos]), row(side="sell", prog="pumpfun", venue="pump_bonding", pool_id=None)) == []


def test_time_cap_is_600_s_after_the_deciding_leg_lands():
    pos = position(decision_ms=T0 + 1000)
    deadline = T0 + 1000 + 600_000 + 1_900
    assert RULE.on_tick(ctx(positions=[pos]), deadline - 1) == []
    assert RULE.on_tick(ctx(positions=[pos]), deadline) == [Sell("p1", "time_cap_600s")]
    assert RULE.on_tick(ctx(positions=[position(exiting=True)]), deadline + 10_000) == []
    assert RULE.on_tick(ctx(positions=[]), deadline) == []


def test_rule_is_pure_same_inputs_same_orders_and_no_state_change():
    before = dict(vars(RULE))
    c = ctx(positions=[position()])
    first = [RULE.on_row(c, row()), RULE.on_row(c, row(side="sell")), RULE.on_tick(c, T0 + 10 ** 7)]
    second = [RULE.on_row(c, row()), RULE.on_row(c, row(side="sell")), RULE.on_tick(c, T0 + 10 ** 7)]
    assert first == second and dict(vars(RULE)) == before and isinstance(RULE, H2PSRule)


# -------------------------------------------------------------------------------------------------------------------------- hosted, on the synthetic tape
def hosted(tmp_path, rows=ROWS, **kw):
    h = make_host(tmp_path, [make_rule()], rows=rows, **kw)
    h.resume()
    return h


def tick_at_or_after(ms: int) -> int:
    return TICK_START + -(-(ms - TICK_START) // 1000) * 1000


def test_hosted_entries_one_per_mint_and_only_in_the_universe(tmp_path):
    h = hosted(tmp_path)
    h.run(TICK_START, T_END)
    ent = [r for r in decision_rows(h, BOT) if r["kind"] == "entry"]
    assert [(e["pool"], e["meta"]["wallet"], e["ms"], e["attempt_key"]) for e in ent] == [(POOL["A"], W1, at("buy_A"), MINT["A"]), (POOL["E"], W3, at("buy_E"), MINT["E"])]
    # not the second followed wallet on A, not W1's second and post-exit buys on A, not the pre-clock buy on E, not B (non-WSOL), C (bonding), D (V out of band)
    assert {e["pool"] for e in ent}.isdisjoint({POOL["B"], POOL["D"]}) and all(e["stake_sol"] == 0.25 and e["meta"]["rule"] == BOT for e in ent)
    led = ledger_rows(h, BOT)
    assert {r["prog"] for r in led if "prog" in r} == {"pumpswap"} and {r["pool"] for r in led} == {POOL["A"], POOL["E"]}
    assert h.counters.get("blocked_universe", 0) == 0 and h.counters.get("blocked_duplicate_attempt", 0) == 0  # the rule itself never asked for any of them


def test_hosted_exit_on_the_wallets_first_sell_or_at_600_s_whichever_first(tmp_path):
    h = hosted(tmp_path)
    h.run(TICK_START, T_END)
    ex = {r["order_id"]: r for r in decision_rows(h, BOT) if r["kind"] == "exit"}
    ent = {r["pool"]: r for r in decision_rows(h, BOT) if r["kind"] == "entry"}
    a, e = ex[ent[POOL["A"]]["order_id"]], ex[ent[POOL["E"]]["order_id"]]
    assert (a["reason"], a["ms"]) == ("wallet_sell", at("sell_A"))  # the entering wallet's sell, 180 s in: earlier than the cap
    # ZED's sell of A (unfollowed) and W2's sell of E (followed, but not the entering wallet) did not exit anything
    assert (e["reason"], e["ms"]) == ("time_cap_600s", tick_at_or_after(at("buy_E") + 600_000 + 1_900))
    assert len(ex) == 2 and all(r["attempt"] == 1 for r in ex.values())
    trades = [r for r in ledger_rows(h, BOT) if r["kind"] == "trade"]
    assert len(trades) == 6 and not h.books[BOT].open


def test_a_sell_before_the_entry_leg_lands_is_retried_not_dropped(tmp_path):
    keep = [r for r in ROWS if r.get("trader") not in {*wallets(RULE), "ZED" + "1" * 40}]  # filler only
    rows = sorted([*keep, swap(at("buy_A"), "A", W1, "buy", 900001), swap(at("buy_A") + 500, "A", W1, "sell", 900002)], key=lambda r: (r["t_recv_ms"], r["signature"]))
    h = hosted(tmp_path, rows=rows)
    h.run(TICK_START, T0 + 330_000)
    log = [r for r in decision_rows(h, BOT) if r["kind"] in ("entry", "exit", "order_refused")]
    kinds = [r["kind"] for r in log]
    assert kinds[0] == "entry" and kinds[1] == "exit" and log[1]["ms"] == at("buy_A") + 500  # decided on the sell print, 0.5 s after the entry
    refused = [r for r in log if r["kind"] == "order_refused"]
    assert refused and all(r["side"] == "sell" and "slower entry leg" in r["reason"] for r in refused)
    exits = [r for r in log if r["kind"] == "exit"]
    assert [x["attempt"] for x in exits] == list(range(1, len(exits) + 1)) and len(exits) == len(refused) + 1 and kinds[-1] == "exit"  # the last attempt was accepted
    assert [x["ms"] for x in exits] == sorted(x["ms"] for x in exits) and not h.books[BOT].open
    assert len([r for r in ledger_rows(h, BOT) if r["kind"] == "trade"]) == 3  # and it was priced at all three stakes
