"""osb_follow_oq + tools/arena_oq_engine: an other-quote pool from trigger to entry to exit inside the follower with hand-computed SOL P&L; a WSOL pool refused; the default (WSOL) book
byte-equal to osb_follow's. Synthetic rows only; no tape, ledger, mark or book is opened. Nothing here says any book makes money."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "research"))
import osb_follow as O  # noqa: E402
import osb_follow_oq as OQ  # noqa: E402
from tools import arena_oq_engine as E  # noqa: E402
from tools import arena_rules as R  # noqa: E402
from tools import arena_rule_l1_oq as Q  # noqa: E402
from tools import test_arena_rule_l1_oq as TQ  # noqa: E402
from tools.arena_declog import DecisionLog  # noqa: E402
from tools.arena_rules_testkit import hour_name, write_tape  # noqa: E402
from tools.test_osb_follow import HOUR, SEED, START_HOUR, T0, _env, shifted_rows, make_world  # noqa: E402,F401  (_env: autouse env fixture of that module)
import tools.test_arena_rule_l1 as T  # noqa: E402

N = 60
STAKES = [0.10, 0.25]
SEND = 160000
D = T0 - TQ.H0


def shift(rows):
    out = []
    for r in rows:
        r = dict(r)
        r["t_recv_ms"] += D
        r["slot"] += D // 400
        r["event_ts"] += D // 1000
        r["signature"] = r["signature"] + "x"
        out.append(r)
    return out


def oq_rows(with_wsol_pool=False):
    quote_until = TQ.H0 + 59 * HOUR
    rows = TQ.stream(quote_until=quote_until)
    drop_t = TQ.H0 + 30 * HOUR + 900_000
    rows.append(TQ.row(drop_t + 5_000, "P", "sell", q=500_000_000, sol=1_000_000))                 # the entry landing print (>= decision + 1.9 s)
    rows.append(TQ.row(TQ.H0 + 54 * HOUR + 1_800_000, "P", "buy", q=2_000_000_000, sol=1_000_000, b=800_000_000_000))  # the exit landing print, ~24 h later
    if with_wsol_pool:  # a WSOL pool with the same drained pattern: the rule and the fence refuse it
        for r in TQ.stream(drop_quote=T.WSOL, quote_rows=False, p="W"):
            if r["pool"] == "W":
                rows.append(dict(r, quote_mint=T.WSOL, signature=r["signature"] + "w"))
    return sorted(rows, key=lambda r: (r["t_recv_ms"], r["pool"]))


def world(tmp_path, rows):
    src = tmp_path / "src"
    write_tape(src, shift(rows))
    for i in range(N):
        (src / hour_name(T0 + i * HOUR)).touch()
    tape = tmp_path / "tape"
    tape.mkdir()
    for p in src.glob("tape-*"):
        (tape / p.name).write_bytes(p.read_bytes())
    return tmp_path


def manifest(tmp, out="out", **over):
    m = {"book_id": "oq-a", "title": "oq test", "host": "hostv2", "rule_overrides": {}, "stakes_sol": STAKES, "send_lamports": SEND, "start_hour": START_HOUR, "seed_from_hour": SEED,
         "tape_dir": str(tmp / "tape"), "out_root": str(tmp / out), "universe": "pumpswap_other_quote"}
    m.update(over)
    p = tmp / ("m-%s.json" % out)
    p.write_text(json.dumps(m))
    return p


def ledger_rows(out):
    f = Path(out) / "book" / "ledger" / "oq-rows.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def expected(stake_sol):
    """hand arithmetic: entry at the landing print (q 0.5e9, b 1e12, V, pool fee 1%), quote pool Qw = Bw = 100e9 fee 0.25%; exit at the 54.5 h print (q 2e9, b 0.8e12)"""
    S, fw, fp, V = stake_sol * 1e9, 0.0025, 0.01, TQ.V
    net = S * (1 - fw)
    qu = 100e9 * net / (100e9 + net)
    Qe, Be = 0.5e9 + V, 1e12
    tok = Be * (qu * (1 - fp)) / (Qe + qu * (1 - fp))
    Qe2, Be2 = 2e9 + V, 0.8e12
    qo = Qe2 * tok / (Be2 + tok) * (1 - fp)
    sol = 100e9 * qo / (100e9 + qo) * (1 - fw)
    return {"tokens": tok, "quote_units": qu, "sol_out": sol, "pnl": sol - S - 2 * SEND, "entry_conv": S - qu, "exit_conv": qo - sol}


def test_other_quote_pool_trigger_entry_exit_end_to_end(tmp_path):
    tmp = world(tmp_path, oq_rows())
    mp = manifest(tmp)
    rc = OQ.follow(str(mp), once=True, _roots=(str(tmp),), _table_mode="other_quote")
    assert rc == 0
    rows = ledger_rows(tmp / "out")
    opens = {r["stake_sol"]: r for r in rows if r["kind"] == "open"}
    trades = {r["stake_sol"]: r for r in rows if r["kind"] == "trade"}
    assert set(opens) == set(trades) == set(STAKES) and not [r for r in rows if r["kind"] == "refused"]
    for s in STAKES:
        e = expected(s)
        assert opens[s]["tokens"] == pytest.approx(e["tokens"], rel=1e-6) and opens[s]["quote_units"] == pytest.approx(e["quote_units"], rel=1e-6)
        assert opens[s]["conv_cost_lamports"] == pytest.approx(e["entry_conv"], rel=1e-4)
        t = trades[s]
        assert t["sol_out_lamports"] == pytest.approx(e["sol_out"], rel=1e-6) and t["pnl_lamports"] == pytest.approx(e["pnl"] - 2 * SEND, rel=1e-6)
        assert t["exit_conv_cost_lamports"] == pytest.approx(e["exit_conv"], rel=1e-4) and t["reason"] == Q.REASON_TIME and t["quote_mint"] == TQ.Q1
        assert t["decision_ms"]["exit"] - t["decision_ms"]["entry"] == 86_400_000 + Q.LEG_MS or t["decision_ms"]["exit"] >= t["decision_ms"]["entry"] + 86_400_000
    dec = DecisionLog(str(tmp / "out" / "book")).rows(Q.BOT_ID)
    assert [r["kind"] for r in dec if r["kind"] in ("entry", "exit")] == ["entry", "exit"] and dec[0]["pool"] == "P"
    st = json.loads((tmp / "out" / "status.json").read_text())
    assert st["n_entries"] == 1 and st["n_exits"] == 1


def test_wsol_pool_is_refused_and_never_priced(tmp_path):
    tmp = world(tmp_path, oq_rows(with_wsol_pool=True))
    assert OQ.follow(str(manifest(tmp)), once=True, _roots=(str(tmp),), _table_mode="other_quote") == 0
    dec = DecisionLog(str(tmp / "out" / "book")).rows(Q.BOT_ID)
    assert {r["pool"] for r in dec if r.get("kind") == "entry"} == {"P"}
    assert all(r.get("pool") != "W" for r in ledger_rows(tmp / "out"))


def test_fence_admits_other_quote_only_and_restores_the_pinned_function():
    assert E.oq_in_universe(SimpleNamespace(prog="pumpswap", quote_mint=TQ.Q1))
    for qm in (T.WSOL, None, ""):
        assert not E.oq_in_universe(SimpleNamespace(prog="pumpswap", quote_mint=qm))
    assert not E.oq_in_universe(SimpleNamespace(prog="pumpfun", quote_mint=TQ.Q1)) and not E.oq_in_universe(None)
    orig = R.in_universe
    h = E.OQHost.__new__(E.OQHost)
    rule = SimpleNamespace(universe="pumpswap_wsol")
    try:
        E.OQHost._buy(h, rule, None, SimpleNamespace(pool="x"), 0)
    except Exception:
        pass
    assert R.in_universe is orig  # the swap only happens for the OQ universe and is always undone


def test_oq_manifest_refusals(tmp_path):
    tmp = world(tmp_path, oq_rows())
    for over in ({"host": "lane"}, {"rule_overrides": {"TP": 1.3}}, {"universe": "bogus"}, {"gap_exclusion": "bogus"}):
        assert OQ.follow(str(manifest(tmp, **over)), once=True, _roots=(str(tmp),), _table_mode="other_quote") == 2


def test_default_universe_is_byte_equal_to_osb_follow(tmp_path):
    """a manifest without `universe` through osb_follow_oq runs the ORIGINAL build_book: same ledger tree md5 as osb_follow itself (osb_follow.py is not edited either)"""
    from tools.test_osb_follow import EK
    tmp, src, names = make_world(tmp_path, shifted_rows(tmp_path))
    for nm in names[:34]:
        (tmp / "tape" / nm).write_bytes((src / nm).read_bytes())
    base = {"book_id": "t-a", "title": "x", "host": "hostv2", "rule_overrides": {}, "stakes_sol": [0.10, 0.25], "send_lamports": 160000, "start_hour": START_HOUR, "seed_from_hour": SEED,
            "tape_dir": str(tmp / "tape")}
    md5 = {}
    for name, fn, extra in (("orig", O.follow, {}), ("oq", OQ.follow, {})):
        p = tmp / ("m-%s.json" % name)
        p.write_text(json.dumps(dict(base, out_root=str(tmp / name), **extra)))
        assert fn(str(p), once=True, _roots=(str(tmp),), _table_mode="first_print", _engine_kw=EK) == 0
        md5[name] = O.tree_md5(tmp / name / "book" / "ledger")
    assert md5["orig"] == md5["oq"]


# ------------------------------------------------------------------------------------------------ engine: sell before the entry lands (QP M1 / reviewer B1), refusal at landing (QP M2)
def ledger_rows_dir(d):
    f = Path(d) / "oq-rows.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def _engine(tmp_path):
    tab = Q.OQTable()
    for r in shift(oq_rows())[:3]:
        tab.update(r)
    eng = E.OQEngine(tmp_path / "led", (0.25,), SEND)
    eng.table = tab
    return eng, tab


def _buy(eng, t=0):
    return eng.buy(Q.BOT_ID, "P", 0.25, t, 1, {"quote_mint": TQ.Q1})


def test_sell_before_entry_lands_is_refused_early_and_a_time_exit_cancels_cleanly(tmp_path):
    eng, tab = _engine(tmp_path)
    pid = _buy(eng, 1_000)
    with pytest.raises(R.ArenaRefused) as ei:
        eng.sell(Q.BOT_ID, pid, 1.0, 1_000 + 5_000, 2, reason="tp_1.5x")
    assert R.SELL_EARLY in str(ei.value) and eng.pending[pid]["state"] == "entry"
    eng.sell(Q.BOT_ID, pid, 1.0, 1_000 + Q.HOLD_MS, 3, reason=Q.REASON_TIME)  # no print for 24 h: the time exit closes the unfilled entry
    assert pid not in eng.pending and pid not in eng.positions and eng.counters["entry_unfilled_cancelled"] == 1
    eng.on_row(shift([TQ.row(TQ.H0 + 40 * HOUR, "P", "buy")])[0])  # the next print of the pool no longer raises (2bb40157: KeyError 'legs')
    rows = ledger_rows_dir(tmp_path / "led")  # item 1: the unfilled entry is SCORED (no state at the buy here: the sends alone, flagged), not dropped
    assert [(r["kind"], r["reason"], r["unfilled_roundtrip"], r["unfilled_priced"]) for r in rows] == [("trade", "entry_unfilled_at_exit", True, False)]
    assert rows[0]["pnl_lamports"] == -4 * SEND and eng.counters["entry_unfilled_unpriced"] == 1


def test_second_sell_uses_the_hosts_drop_text(tmp_path):
    eng, tab = _engine(tmp_path)
    pid = _buy(eng)
    eng.pending[pid].update(state="exit")
    with pytest.raises(R.ArenaRefused) as ei:
        eng.sell(Q.BOT_ID, pid, 1.0, 10, 2)
    assert any(k in str(ei.value) for k in R.SELL_ENGINE_DROPS)


def test_entry_refused_at_landing_leaves_no_position_in_the_host_book(tmp_path):
    """the quote's WSOL pool is fresh at the trigger but stale at the landing print (25 min later): the engine refuses the entry and the host drops it from its book"""
    rows = TQ.stream(quote_until=TQ.H0 + 30 * HOUR + 900_000)
    rows.append(TQ.row(TQ.H0 + 30 * HOUR + 900_000 + 25 * 60_000, "P", "sell", q=500_000_000, sol=1_000_000))
    for k in range(1, 30):  # a busy feed (no silence gap) whose quote pool is silent: only the quote state goes stale
        rows.append(TQ.row(TQ.H0 + 30 * HOUR + 900_000 + k * 50_000, "FILL", "buy", quote=Q.WSOL, mint="MFILL"))
    tmp = world(tmp_path, sorted(rows, key=lambda r: (r["t_recv_ms"], r["pool"])))
    m = OQ.load_manifest(str(manifest(tmp)))
    host, eng, _ = OQ.build_book(m, tmp / "run", table_mode="other_quote")
    host.run(T0 + 26 * HOUR, T0 + 32 * HOUR)
    assert host.books[Q.BOT_ID].open == {} and eng.positions == {} and eng.pending == {}
    assert host.counters.get("entries_refused_at_landing") == 1 and eng.counters.get("entry_refused_stale_quote_price") == 1
    log = DecisionLog(str(tmp / "run")).rows(Q.BOT_ID)
    assert any(r.get("kind") == "order_refused" and r["reason"].startswith("entry_refused_at_landing") for r in log) and not host.hook_errors
    assert [r["kind"] for r in ledger_rows_dir(tmp / "run" / "ledger")] == ["refused"]


def test_latency_twin_manifest_is_accepted_and_sets_the_engine_latency(tmp_path):
    tmp = world(tmp_path, oq_rows())
    for lat in (3.010, 5.590):
        m = OQ.load_manifest(str(manifest(tmp, out="tw%s" % lat, book_id="oq-tw", twin_of="oq-a", pricing_lat_entry_s=lat)))
        host, eng, _ = OQ.build_book(m, tmp / ("run%s" % lat), table_mode="other_quote")
        assert eng.lat_ms == int(round(lat * 1000))
    m = OQ.load_manifest(str(manifest(tmp, out="strict")))
    assert OQ.build_book(m, tmp / "runs", table_mode="other_quote")[1].lat_ms == 4085  # the deciding cell
    with pytest.raises(O.Refused):
        OQ.load_manifest(str(manifest(tmp, out="bad", book_id="oq-tw", twin_of="oq-a")))  # twin_of without a latency value
    with pytest.raises(O.Refused):
        OQ.load_manifest(str(manifest(tmp, out="bad2", book_id="oq-tw", twin_of="oq-a", pricing_lat_entry_s=2.0)))


def test_gap_hold_twin_manifest_sets_the_engine_gap_mode(tmp_path):
    tmp = world(tmp_path, oq_rows())
    m = OQ.load_manifest(str(manifest(tmp, out="gh", book_id="oq-gh", twin_of="oq-a", gap_exclusion="hold")))
    assert OQ.build_book(m, tmp / "rgh", table_mode="other_quote")[1].gap_mode == "hold"
    m = OQ.load_manifest(str(manifest(tmp, out="gs")))
    assert OQ.build_book(m, tmp / "rgs", table_mode="other_quote")[1].gap_mode == "on"  # the WSOL default


def _restart_twice(tmp_path, monkeypatch, status_in_ledger):
    tmp = world(tmp_path, oq_rows())
    mp = manifest(tmp)
    if status_in_ledger:  # the pre-fix layout: status.json inside the hashed ledger dir
        orig = E.OQEngine.__init__

        def init(self, *a, **k):
            orig(self, *a, **k)
            self.status_path = self.root / "status.json"
        monkeypatch.setattr(E.OQEngine, "__init__", init)
    ticks = iter(range(1_700_000_000, 1_700_100_000, 997))
    monkeypatch.setattr(E.time, "time", lambda: float(next(ticks)))  # the wall clock differs between the runs, as in production
    assert OQ.follow(str(mp), once=True, _roots=(str(tmp),), _table_mode="other_quote") == 0
    return OQ.follow(str(mp), once=True, _roots=(str(tmp),), _table_mode="other_quote"), tmp


def test_restart_md5_matches_with_status_outside_the_ledger_dir(tmp_path, monkeypatch):
    rc, tmp = _restart_twice(tmp_path, monkeypatch, False)
    assert rc == 0
    run = (tmp / "out" / "book").resolve()
    assert (run / "oq-status.json").exists() and not (run / "ledger" / "status.json").exists()
    assert json.loads((run / "oq-status.json").read_text())["written_unix_s"] > 0


def test_restart_md5_mismatch_reproduced_when_status_is_inside_the_ledger_dir(tmp_path, monkeypatch):
    rc, _ = _restart_twice(tmp_path, monkeypatch, True)
    assert rc == 4  # the old failure: ledger_md5 differs only through the wall-clock status file
