"""G4-FLOORDIP v1 streaming port (g4v1-a1): rule, G4 pool table, v2 host registration. Synthetic tape only. Nothing here says the rule makes money."""

from __future__ import annotations

import collections
import hashlib
from pathlib import Path

import pytest

from tools import arena_pool_table as PT
from tools import arena_rule_g4v1 as G
from tools import arena_rules_v2 as V2
from tools.arena_pool_table import PoolTable
from tools.arena_pool_table_g4 import PoolTableG4, pre_price
from tools.arena_rules import HostConfig, register_rules
from tools.arena_rules_testkit import FakeEngine, write_tape
from tools.arena_rules_v2 import HostV2, load_rules_v2
from tools.arena_state import ArenaState
from tools.test_arena_rule_l1 import H0, HOUR, SHA, SOLX, V, row

REPO = Path(__file__).resolve().parent.parent
HALF = 1_800_000


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


def prow(*a, **k):
    """tools.test_arena_rule_l1.row with a realistic base reserve (1e15 raw: price x 1e6 is the market cap in SOL, rule_entries.py)."""
    r = row(*a, **k)
    r["base_reserve"], r["token_raw"] = 10**15, r["sol_lamports"] * 1_000
    return r


def g4_rows(first=4, drop_at=24, q_hi=7 * SOLX, q_lo=1 * SOLX, sol=200_000_000, stop=70, gap=None, v0=V, extra=()):
    """Filler X at hour 0; pool P prints every 30 min from `first` h to `stop` h: quote reserve q_hi (price mcap 24.6) until `drop_at` h, then q_lo (mcap 18.6, r6 = 0.756)."""
    rows = [prow(H0 + 1000, "X")]
    t = H0 + first * HOUR
    while t <= H0 + stop * HOUR:
        if gap is None or not gap[0] * HOUR <= t - H0 < gap[1] * HOUR:
            rows.append(prow(t, "P", "buy" if (t // HALF) % 2 else "sell", q=q_hi if t < H0 + drop_at * HOUR else q_lo, sol=sol, v=v0 if t == H0 + first * HOUR else V))
        t += HALF
    rows += list(extra)
    return sorted(rows, key=lambda r: r["t_recv_ms"])


def g4_rules():
    rules = load_rules_v2(["g4v1-a1"])
    for r in rules:
        r.table_mode = "first_print"  # the synthetic tapes carry no create / complete rows (as test_arena_rule_l1)
    return rules


def run_host(tmp, rows, hours=60):
    tape = tmp / "tape"
    if not tape.exists():
        write_tape(tape, rows)
    rules = g4_rules()
    reg = tmp / "bots.json"
    if not reg.exists():
        register_rules(rules, str(reg), SHA, H0, now_ms=H0 - 1, verify_sha=False)
    cfg = HostConfig(root=str(tmp / "rules"), registry=str(reg), frozen_sha=SHA, clock_start_ms=H0)
    host = HostV2(cfg, rules, ArenaState(tape, start_ms=0), FakeEngine(tmp / "rules"))
    host.run(H0, H0 + int(hours * HOUR))
    return host


def entries(host):
    return [r for r in host.log.rows("g4v1-a1") if r.get("kind") == "entry"]


def exits(host):
    return [r for r in host.log.rows("g4v1-a1") if r.get("kind") == "exit"]


# ---------------------------------------------------------------------------------------------------------------- entry
def test_entry_once_at_the_first_hour_with_age_24h_with_fields(tmp_path):
    host = run_host(tmp_path, g4_rows())
    e = entries(host)
    assert len(e) == 1 and e[0]["ms"] == H0 + 28 * HOUR + 2_000  # M = 4 h, so the first hour with age >= 24 h is 28 h; decided at :00:02
    m = e[0]["meta"]
    assert m["rule"] == "g4v1-a1" and m["cell"] == "g4v1" and m["hold_s"] == 86400.0 and m["tp"] == 1.5 and m["age_s"] == 24 * 3600
    assert 0.75 < m["r6"] < 0.76 and 18.5 < m["mcap_sol"] < 18.7 and m["vol24_sol"] >= 1.0
    assert host.books["g4v1-a1"].open or exits(host)


@pytest.mark.parametrize("kw,why", [
    (dict(drop_at=200), "no fall: r6 = 1"),
    (dict(q_lo=9 * SOLX, q_hi=20 * SOLX), "mcap 26.6 >= 25 although r6 = 0.71"),
    (dict(sol=20_000_000), "vol24 below 1 SOL"),
    (dict(v0=25 * SOLX), "first-print V out of band: not in the universe"),
])
def test_each_entry_condition_blocks(tmp_path, kw, why):
    assert entries(run_host(tmp_path, g4_rows(**kw), hours=40)) == [], why


def test_age_below_24h_waits_for_the_next_hour_with_age_24h(tmp_path):
    e = entries(run_host(tmp_path, g4_rows(first=6), hours=40))
    assert [x["ms"] for x in e] == [H0 + 30 * HOUR + 2_000]


def test_no_print_in_the_last_6h_blocks_until_one_arrives(tmp_path):
    """Prints stop at 21.5 h and resume at 30 h: nothing in [H-6h, H) at 28 h .. 30 h; at 31 h the 30 h print (low price) vs the last print before 25 h (high price) fires."""
    rows = [r for r in g4_rows() if not (H0 + 22 * HOUR <= r["t_recv_ms"] < H0 + 30 * HOUR)]
    e = entries(run_host(tmp_path, rows, hours=40))
    assert [x["ms"] for x in e] == [H0 + 31 * HOUR + 2_000]


def test_a_print_at_the_boundary_is_not_used(tmp_path):
    """A print whose event time is exactly H (28 h) at the high price would make r6 ~ 1 if used; the features stop strictly before H."""
    extra = [prow(H0 + 28 * HOUR, "P", "buy", q=7 * SOLX)]
    e = entries(run_host(tmp_path, g4_rows(extra=extra), hours=40))
    assert [x["ms"] for x in e] == [H0 + 28 * HOUR + 2_000]


def test_decision_only_on_the_tick_after_the_boundary(tmp_path):
    host = run_host(tmp_path, g4_rows(), hours=60)
    assert all(r["ms"] % HOUR == 2_000 for r in entries(host))


# ---------------------------------------------------------------------------------------------------------------- exits and cooldown
def test_time_exit_24h_plus_leg_and_one_open_per_pool(tmp_path):
    host = run_host(tmp_path, g4_rows(), hours=52.1)  # the table forgets exit stamps older than 1 h at its hourly sweep
    e, x = entries(host), exits(host)
    assert len(e) == 1 and len(x) == 1 and x[0]["reason"] == G.REASON_TIME
    assert e[0]["ms"] + 86_400_000 + 1_900 <= x[0]["ms"] < e[0]["ms"] + 86_400_000 + 4_000
    assert host.table.exit_ms("P") == x[0]["ms"]


def _tp_rows(first_buy):
    t = H0 + 28 * HOUR
    return [prow(t + 10_000, "P", "sell", q=400_000_000, sol=1_000_000), prow(t + 60_000, "P", "buy", q=first_buy, sol=50_000_000)]


def test_take_profit_against_the_post_buy_mark(tmp_path):
    host = run_host(tmp_path, g4_rows(extra=_tp_rows(60 * SOLX)), hours=40)
    e, x = entries(host), exits(host)
    assert e[0]["ms"] == H0 + 28 * HOUR + 2_000 and x[0]["reason"] == G.REASON_TP and x[0]["ms"] < H0 + 29 * HOUR  # a later hour may legitimately re-enter once the position is closed


def test_a_print_at_1p25x_the_mark_does_not_take_profit(tmp_path):
    host = run_host(tmp_path, g4_rows(extra=_tp_rows(5 * SOLX)), hours=40)
    assert len(entries(host)) == 1 and not [x for x in exits(host) if x["reason"] == G.REASON_TP]


def test_restart_keeps_table_with_pre_prices_and_positions_in_log(tmp_path):
    first = run_host(tmp_path, g4_rows(), hours=40)
    first.save_table()
    assert len(entries(first)) == 1 and isinstance(first.table, PoolTableG4)
    again = PoolTableG4.load(first.table_path)
    assert again.to_dict() == first.table.to_dict() and list(again.pre["P"]) == list(first.table.pre["P"])
    host = HostV2(HostConfig(root=str(tmp_path / "rules"), registry=str(tmp_path / "bots.json"), frozen_sha=SHA, clock_start_ms=H0), g4_rules(),
                  ArenaState(tmp_path / "tape", start_ms=0), first.engine)
    host.resume()
    assert isinstance(host.table, PoolTableG4) and len(host.books["g4v1-a1"].open) == 1 and len(entries(host)) == 1


# ---------------------------------------------------------------------------------------------------------------- table and signal
def test_pre_trade_price_is_quote_plus_v_over_base():
    r = row(H0 + 4 * HOUR, "P", q=2 * SOLX)
    assert pre_price(r) == pytest.approx((2 * SOLX + V) / 10**12)
    assert pre_price({**r, "virtual_quote_reserves": None}) != pre_price(r) and pre_price({**r, "base_reserve": 0}) != pre_price(r)


def test_pre_array_is_aligned_and_the_anchor_is_the_last_trimmed_print():
    t = PoolTableG4("first_print")
    t.update(prow(H0 + 1000, "X"))
    for r in g4_rows(stop=40):
        t.update(r)
    pre, anchor = t.pre_for("P")
    pp = t.pool_prints("P")
    assert pre is not None and len(pre) == len(pp) and anchor is not None  # prints older than 26 arrival hours were trimmed
    t.sweep(H0 + 500 * HOUR)
    assert "P" not in t.prints and "P" not in t.pre


def test_signal_uses_the_anchor_when_the_p6_print_left_the_window():
    from bisect import bisect_left
    t = PoolTableG4("first_print")
    for r in g4_rows(stop=60):
        t.update(r)
    pp, (pre, _) = t.pool_prints("P"), t.pre_for("P")
    Hs = (H0 + 60 * HOUR) // 1000
    assert G.signal(t, "P", Hs) is None  # steady low price: r6 = 1
    k = bisect_left(pp.bt, Hs - 6 * 3600)  # keep only the last 6 h: no print precedes H - 6 h in the window
    anchor = pre[k - 1] * 2.0
    del pp.bt[:k], pp.sol[:k], pp.spot[:k], pp.hidx[:k], pre[:k]
    t.anchor["P"] = anchor
    vol, p_T, r6 = G.signal(t, "P", Hs)
    assert r6 == pytest.approx(0.5) and vol >= 1e9
    t.anchor.pop("P")
    assert G.signal(t, "P", Hs) is None  # no forward-fill value: fail closed


def test_checkpoint_of_the_base_table_loads_as_an_empty_g4_table(tmp_path):
    b = PoolTable("first_print")
    for r in g4_rows(stop=10):
        b.update(r)
    b.checkpoint(tmp_path / "l1.pkl")
    g = PoolTableG4.load(tmp_path / "l1.pkl")
    assert g.pre == {} and g.pre_for("P") == (None, None) and g.base("P") == b.base("P")


# ---------------------------------------------------------------------------------------------------------------- registration and frozen files
def test_registration_default_is_complete_mode_with_the_g4_table():
    r = G.make_rule()
    assert r.table_mode == "complete" and r.bot_id == "g4v1-a1" and r.table_cls is PoolTableG4 and r.universe == "pumpswap_wsol" and r.deciding_stake == 0.25


def test_l1_floor_v2_still_builds_the_base_table_and_is_registered():
    assert V2.RULE_MODULES_V2["l1-floor-v2"] == "tools.arena_rule_l1" and V2.RULE_MODULES_V2["g4v1-a1"] == "tools.arena_rule_g4v1"
    rules = load_rules_v2(["l1-floor-v2"])
    assert getattr(rules[0], "table_cls", PoolTable) is PoolTable


def test_hosting_both_tables_in_one_process_is_refused(tmp_path):
    from tools.arena_rules import HostError
    rules = load_rules_v2(["l1-floor-v2", "g4v1-a1"])
    for r in rules:
        r.table_mode = "first_print"
    with pytest.raises(HostError):
        HostV2(HostConfig(root=str(tmp_path / "r"), registry=str(tmp_path / "b.json"), frozen_sha=SHA, clock_start_ms=H0), rules, ArenaState(tmp_path, start_ms=0), FakeEngine(tmp_path))


def test_l1_files_and_v1_host_files_are_unchanged():
    pins = {"tools/arena_rule_l1.py": "de6e371e9479f28dbb610895e2cffb7a6704a762d3f02d3a9c49a08e3019500c", "tools/arena_pool_table.py": "78955dacb9f8c041cace52119ddea6758570b3b9425b37ee68b0c7c6487c7692",
            "tools/arena_rules.py": "048dd78901c52e0c14fe9b1611f354c49ac6d0bb37fad8e5be3e27c756e3e2af", "tools/arena_paper.py": "9d2fdb04a3da9bb38de9e30c91154f5a9ab2de513e6f4d7936c1eb1a54863514"}
    for rel, sha in pins.items():
        assert hashlib.sha256((REPO / rel).read_bytes()).hexdigest() == sha, rel


def test_rule_source_has_no_banned_family_and_uses_the_shared_base_exit():
    src = (REPO / "tools" / "arena_rule_g4v1.py").read_text()
    code = src.split('"""', 2)[2]
    for bad in ("r4b", "laya", "lightgbm", "anthropic", "/data/mal", "blocks"):
        assert bad not in code.lower(), bad
    assert "L1Rule._tp_hit" in code and G.TP == 1.5 and G.HOLD_MS == 86_400_000 + 1_900 and G.REASON_TP == "tp_1.5x"


# ---------------------------------------------------------------------------------------------------------------- equivalence tool (counts only)
def test_equiv_compare_counts_and_csv(tmp_path):
    from tools import arena_g4v1_equiv as E
    f = tmp_path / "kit.csv"
    f.write_text("pool,T\nA,3600\nB,7200.0\nC,7200\n")
    kit = E.read_kit(str(f))
    assert kit == {("A", 3600), ("B", 7200), ("C", 7200)}
    r = E.compare(kit, {("A", 3600), ("B", 7200), ("D", 10800)})
    assert (r["n_kit"], r["n_port"], r["matched"], r["only_kit"], r["only_port"]) == (3, 3, 2, 1, 1) and r["first_only_kit"] == [{"hour_s": 7200, "pool": "C"}]


def test_equiv_refuses_a_forbidden_path_and_a_sealed_hour(tmp_path):
    from tools import arena_g4v1_equiv as E
    assert E.main(["--hours", "/data/mal/blocks/forward-1002/tape-2026-10-03T00.jsonl.zst", "--kit-csv", str(tmp_path / "k.csv"), "--out", str(tmp_path / "o")]) == 2
