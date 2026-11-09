"""Adversarial review of FLOOR live pin f68cf56f (full-wipe presend) vs live e8790f93.

Synthetic only: throwaway Keypair, probe FakeRpc, no network, no host, no wallet file.
Does not edit production executors. Run against the f68c tree:

  python3 -m pytest ARTIFACTS/lab/floor-live-f68cf56f/test_floor_live_f68c_presend.py -q
"""

from __future__ import annotations

import ast
import collections
import dataclasses
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools import arena_live_bridge as B
from tools import floor_arena_executor as F
from tools import floor_lane_executor as X
from tools import h5_lane_executor as H
from tools import probe_live as pl
from tools.test_floor_arena_executor import Clock, Log
from tools.test_floor_lane_executor import FakeInner, lane_raw, write_live_ok
from tools.test_probe_executor import MINT
from tools.test_probe_executor import Clock as PClock
from tools.test_probe_live import LiveRpc

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SHA_F68C = "f68cf56fcce8b1127e7159d8069506f0988eb95d"
SHA_E879 = "e8790f9342df6c59a849f2ed0897b59ad5441015"
BLOB_F68C = "556d15623459d56c8db706e2a2a5b2b2257cd577"
BLOB_E879 = "6f353716e29f72c8f92699310d44f21ce23dd0dd"
BLOB_HOST = "a4387c24135f198f8b3ce7d75f810170fbace297"
FIRST_SEND_MS = 1_794_148_248_897  # 2026-11-08 06:30:48.897 PST; never reset
END_MS = 1_796_688_000_000  # 2026-12-07 16:00 PST / 2026-12-08 00:00Z
T0 = FIRST_SEND_MS
STAKE = 30_000_000
BUDGET = 300_000_000
RESERVE = 3_860_000
LIVE_CASH = -139_591_048  # parent/PR #1303 live fold cash at the 16-refusal window
REALIZED = 18_778_152  # parent: 1 take-profit sell
FAIL_ERR = {"InstructionError": [3, {"Custom": 6004}]}
E879_ROOT = Path(os.environ.get("E879_ROOT", "/tmp/mal-e879-live"))


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    monkeypatch.setattr(
        "tools.arena_paper.shutil.disk_usage",
        lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500),
    )


def _sha1_blob(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def _ledger(tmp: Path) -> X.FloorLaneLedger:
    lc = H.LaneConfig(tmp / "l.jsonl", tmp / "STOP", X.LANE_BOT_ID, STAKE, 8, BUDGET, 100_000_000, RESERVE, 60.0)
    return X.FloorLaneLedger(lc)


def _open(n: int, filled: bool = True, bot: str | None = None) -> dict:
    bot = bot or X.LANE_BOT_ID
    return {f"{bot}:m{i}": {"stake": STAKE, "filled": filled, "ts_ms": T0, "signature": f"s{i}"} for i in range(n)}


def _view(cash: int, n: int, pnl: int | None = None, filled: bool = True, snaps: dict | None = None) -> H.LaneView:
    op = _open(n, filled)
    cost = cash + n * STAKE if filled else cash
    return H.LaneView(cash=cash, pnl=cash if pnl is None else pnl, cost=cost, open=op, snaps=snaps or {})


def _stop_halt(cfg, tmp):
    return dataclasses.replace(cfg, stop_file=str(tmp / "nstop"), halt_file=str(tmp / "nhalt"), end_ms=END_MS)


def live_bridge(tmp, clk, inner=None):
    write_live_ok(tmp)
    raw = lane_raw(tmp)
    raw["end_ms"] = END_MS
    cfg, lane, live_ok = X.make_config(raw)
    cfg = _stop_halt(cfg, tmp)
    inner = inner or FakeInner()
    port = X.FloorLanePort(inner, cfg)
    br = X.FloorLaneBridge(cfg, port, lane, now_ms=clk, live_ok_path=live_ok, root_checks=False)
    return br, inner


# -- pins / closure --------------------------------------------------------------------------------


def test_imported_floor_lane_executor_is_the_f68c_blob():
    data = Path(X.__file__).read_bytes()
    assert _sha1_blob(data) == BLOB_F68C
    assert X.LANE_BUDGET_MAX_LAMPORTS == BUDGET
    assert X.LANE_MAX_OPEN == 8
    assert hasattr(X.FloorLaneLedger, "wipe_floor")
    assert X.LANE_PROBE_MAX_ATTEMPTS == 100_000
    assert X.LANE_PROBE_MAX_DAYS == 60.0


def test_host_blob_is_byte_identical_e879_to_f68c():
    a = subprocess.check_output(["git", "show", f"{SHA_E879}:tools/floor_lane_host.py"], cwd=REPO)
    b = subprocess.check_output(["git", "show", f"{SHA_F68C}:tools/floor_lane_host.py"], cwd=REPO)
    assert _sha1_blob(a) == _sha1_blob(b) == BLOB_HOST
    assert a == b


def test_production_tools_other_than_floor_lane_executor_match_e879():
    for rel in (
        "tools/floor_arena_executor.py",
        "tools/probe_live.py",
        "tools/arena_live_bridge.py",
        "tools/h5_lane_executor.py",
        "tools/floor_lane_host.py",
        "scripts/mal-fast/floor-lane-live.json.example",
    ):
        a = subprocess.check_output(["git", "show", f"{SHA_E879}:{rel}"], cwd=REPO)
        b = subprocess.check_output(["git", "show", f"{SHA_F68C}:{rel}"], cwd=REPO)
        assert a == b, rel


def test_e879_to_f68c_runtime_delta_is_only_wipe_floor_presend():
    diff = subprocess.check_output(["git", "diff", SHA_E879, SHA_F68C, "--", "tools/floor_lane_executor.py"], cwd=REPO).decode()
    plus = "\n".join(l for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
    assert "def wipe_floor" in plus
    assert "open_stakes" in diff
    assert "first_send_ms" not in plus
    assert "CANARY" not in plus
    assert "abandoned" not in plus
    assert "_start_sell" not in plus
    assert "LANE_PROBE_MAX" not in plus
    assert "max_open" not in plus or "object.__setattr__" not in plus


def test_floor_live_import_closure_only_floor_lane_executor_differs_from_e879():
    repo = Path(X.__file__).resolve().parents[1]

    def children(mod: str):
        p = repo / (mod.replace(".", "/") + ".py")
        if not p.exists():
            return []
        tree = ast.parse(p.read_text())
        out = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                out.extend(a.name for a in n.names if a.name.startswith("tools"))
            elif isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("tools"):
                out.append(n.module)
                for a in n.names:
                    if a.name == "*":
                        continue
                    cand = n.module + "." + a.name
                    if (repo / cand.replace(".", "/")).with_suffix(".py").exists():
                        out.append(cand)
        return out

    seen, q, files = set(), ["tools.floor_lane_executor"], []
    while q:
        name = q.pop()
        if name in seen:
            continue
        seen.add(name)
        rel = name.replace(".", "/") + ".py"
        if (repo / rel).exists():
            files.append(rel)
        q.extend(children(name))
    changed = []
    for rel in files:
        a = subprocess.check_output(["git", "show", f"{SHA_E879}:{rel}"], cwd=REPO)
        b = (repo / rel).read_bytes()
        if a != b:
            changed.append(rel)
    assert changed == ["tools/floor_lane_executor.py"], changed
    assert "tools/forge/dsl_lane_host.py" not in files
    assert "tools/lane_daily_report.py" not in files
    assert "tools/floor_lane_host.py" not in files  # host is a separate process; not imported by the executor


def test_host_is_in_appendix_a_closure_and_unchanged_so_not_needed_for_this_pin():
    host = Path(X.__file__).resolve().parents[1] / "tools" / "floor_lane_host.py"
    src = host.read_text()
    assert "floor_lane_executor" not in src
    assert "wipe_floor" not in src


# -- (1) double-count reproduction against e879 ----------------------------------------------------


def test_live_shaped_e879_double_counts_open_stake_and_refuses():
    """Parent: 6 buys of 0.03, 1 TP +0.018778152, 5 open, stale marks, cash -139591048.
    User: -0.1396 - 5×(0.03+0.00386) - (0.03+0.00386) = -0.3428.
    That is cash minus open_stakes minus new stake+reserve — the open cost is already in cash."""
    assert LIVE_CASH - 5 * (STAKE + RESERVE) - (STAKE + RESERVE) == -342_751_048
    assert LIVE_CASH - STAKE - 6 * RESERVE == -192_751_048
    # user's "true worst case if all 6 wipe" ignores fees already in cash
    assert 6 * (STAKE + RESERVE) - REALIZED == 184_381_848
    assert 0 < (abs(LIVE_CASH - STAKE - 6 * RESERVE) - (6 * (STAKE + RESERVE) - REALIZED)) < 10_000_000

    code = r"""
from pathlib import Path
from tools import floor_lane_executor as X
from tools import h5_lane_executor as H
STAKE=30_000_000; RES=3_860_000; CASH=-139_591_048; n=5
op={f"{X.LANE_BOT_ID}:m{i}":{"stake":STAKE,"filled":True,"ts_ms":0,"signature":f"s{i}"} for i in range(n)}
view=H.LaneView(cash=CASH,pnl=CASH,cost=CASH+n*STAKE,open=op,snaps={})
lc=H.LaneConfig(Path("/tmp/e879-live-l.jsonl"), Path("/tmp/e879-live-STOP"), X.LANE_BOT_ID, STAKE, 8, 300_000_000, 100_000_000, RES, 60.0)
led=X.FloorLaneLedger(lc); led._marks={}
why=led.evaluate(view, 1794148248897, STAKE)
old=led.capped_pnl(view, 1794148248897)-(view.open_stakes+STAKE+RES*(n+1))
print(why, old, led.capped_pnl(view, 1794148248897), view.open_stakes, hasattr(led,"wipe_floor"))
"""
    env = {**os.environ, "PYTHONPATH": str(E879_ROOT)}
    out = subprocess.check_output([sys.executable, "-c", code], cwd=str(E879_ROOT), env=env, text=True).strip()
    why, old, capped, ost, has_wipe = out.split()
    assert why == "lane_budget_presend"
    assert int(old) == -342_751_048
    assert int(capped) == LIVE_CASH
    assert int(ost) == 5 * STAKE
    assert has_wipe == "False"


def test_lane_fold_stale_marks_write_opens_to_zero_so_pnl_equals_cash(tmp_path):
    bot = X.LANE_BOT_ID
    now = T0 + 3_600_000
    rows = []
    for i in range(6):
        rows.append({"ev": "send_intent", "bot_id": bot, "mint": f"m{i}", "stake_lamports": STAKE, "ts_ms": now, "sol_in_lamports": 0, "sol_out_lamports": 0, "signature": f"i{i}"})
        rows.append({"ev": "fill", "ok": True, "bot_id": bot, "mint": f"m{i}", "ts_ms": now + 1, "sol_in_lamports": 0, "sol_out_lamports": STAKE, "signature": f"f{i}"})
    rows.append({"ev": "exit", "bot_id": bot, "mint": "m5", "ts_ms": now + 2, "sol_in_lamports": LIVE_CASH + 6 * STAKE, "sol_out_lamports": 0})
    stale = {f"m{i}": (2 * STAKE, now - 61_000) for i in range(5)}
    folded = H.lane_fold(rows, stale, now, 60.0, bot)
    assert folded.cash == LIVE_CASH
    assert folded.pnl == LIVE_CASH  # stale mark counts 0, same as a missing mark
    assert len(folded.open) == 5 and folded.open_stakes == 5 * STAKE
    led = _ledger(tmp_path)
    led._marks = stale
    # e879 formula (still computable from capped_pnl + open_stakes):
    old = led.capped_pnl(folded, now) - (folded.open_stakes + STAKE + RESERVE * 6)
    assert old == -342_751_048
    assert led.evaluate(folded, now, STAKE) is None  # f68c allows
    assert led.wipe_floor(folded, now) - (STAKE + RESERVE * 6) == -192_751_048 > -BUDGET


def test_f68c_allows_the_live_book_that_e879_refused(tmp_path):
    led = _ledger(tmp_path)
    view = _view(LIVE_CASH, 5)
    led._marks = {}
    assert led.evaluate(view, T0, STAKE) is None
    assert led.wipe_floor(view, T0) == LIVE_CASH


# -- (3) full-wipe bound still refuses a book that would cross -0.30 --------------------------------


def test_presend_refuses_when_full_wipe_of_open_plus_new_crosses_030(tmp_path):
    led = _ledger(tmp_path)
    # 7 filled at cost, cash already has the buys plus 0.06 realised loss: same 3710 QP scenario
    cash = -7 * (STAKE + 3_000_000) - 60_000_000
    view = _view(cash, 7)
    led._marks = {f"m{i}": (2 * STAKE, T0) for i in range(7)}  # +100% marks must not help
    assert led.evaluate(view, T0, STAKE) == "lane_budget_presend"
    led._marks = {f"m{i}": (STAKE, T0) for i in range(7)}
    assert led.evaluate(view, T0, STAKE) == "lane_budget_presend"
    led._marks = {}  # stale
    assert led.evaluate(view, T0, STAKE) == "lane_budget_presend"


def test_unrealized_gains_never_add_headroom(tmp_path):
    led = _ledger(tmp_path)
    cash = -BUDGET + STAKE + RESERVE * 4  # 3 open: exactly the refuse boundary
    results = []
    for mk in (0, STAKE, 3 * STAKE, 100 * STAKE, None):
        view = _view(cash, 3)
        led._marks = {} if mk is None else {f"m{i}": (mk, T0) for i in range(3)}
        results.append((led.wipe_floor(view, T0), led.evaluate(view, T0, STAKE)))
    assert all(r == (cash, "lane_budget_presend") for r in results)


def test_realized_gains_do_add_headroom_then_later_losses_can_refuse(tmp_path):
    led = _ledger(tmp_path)
    # realized +18.778M, no open: far inside
    view0 = H.LaneView(cash=REALIZED, pnl=REALIZED, cost=0, open={}, snaps={})
    led._marks = {}
    assert led.evaluate(view0, T0, STAKE) is None
    # realized then a later realized loss large enough that a new full wipe would cross -0.30
    cash_loss = REALIZED - 290_000_000
    view_l = H.LaneView(cash=cash_loss, pnl=cash_loss, cost=cash_loss, open={}, snaps={})
    assert led.wipe_floor(view_l, T0) - (STAKE + RESERVE) == cash_loss - STAKE - RESERVE
    assert cash_loss - STAKE - RESERVE <= -BUDGET
    assert led.evaluate(view_l, T0, STAKE) == "lane_budget_presend"


def test_stale_marks_and_fresh_zero_marks_agree_on_wipe_floor(tmp_path):
    led = _ledger(tmp_path)
    view = _view(LIVE_CASH, 5)
    led._marks = {}
    a = led.wipe_floor(view, T0)
    led._marks = {f"m{i}": (0, T0) for i in range(5)}
    b = led.wipe_floor(view, T0)
    led._marks = {f"m{i}": (STAKE, T0 - 61_000) for i in range(5)}
    c = led.wipe_floor(view, T0)
    assert a == b == c == LIVE_CASH


def test_unfilled_intent_is_charged_once_not_via_cash(tmp_path):
    led = _ledger(tmp_path)
    op = _open(2, filled=True)
    op[f"{X.LANE_BOT_ID}:pending"] = {"stake": STAKE, "filled": False, "ts_ms": T0, "signature": "p"}
    cash = -2 * STAKE  # two fills in cash; unfilled send_intent sol_out 0
    view = H.LaneView(cash=cash, pnl=cash, cost=cash + 2 * STAKE, open=op, snaps={})
    led._marks = {f"m{i}": (STAKE, T0) for i in range(2)}
    assert led.wipe_floor(view, T0) == cash - STAKE
    # n_open=3 → reserve x4; worst = cash - unfilled - new_stake - reserve
    worst = cash - STAKE - STAKE - RESERVE * 4
    assert (led.evaluate(view, T0, STAKE) is None) == (worst > -BUDGET)


def test_accepted_entry_full_wipe_stays_inside_budget_property(tmp_path):
    import random

    led = _ledger(tmp_path)
    rng = random.Random(2)
    accepted = refused = 0
    for _ in range(3000):
        n = rng.randint(0, 7)
        filled = [rng.random() < 0.85 for _ in range(n)]
        stakes = [rng.choice([20_000_000, 25_000_000, 30_000_000]) for _ in range(n)]
        cash = -sum(s for s, f in zip(stakes, filled) if f) - rng.randint(0, 250_000_000) + rng.randint(0, 80_000_000)
        op = {f"{X.LANE_BOT_ID}:m{i}": {"stake": st, "filled": f, "ts_ms": T0, "signature": f"s{i}"} for i, (st, f) in enumerate(zip(stakes, filled))}
        marks_v = [rng.choice([None, 0, s // 2, s, 2 * s, 20 * s]) for s in stakes]
        md = {f"m{i}": (v, T0) for i, v in enumerate(marks_v) if v is not None}
        view = H.LaneView(cash=cash, pnl=cash + sum(md.get(f"m{i}", (0, 0))[0] for i in range(n) if filled[i]), cost=cash + sum(s for s, f in zip(stakes, filled) if f), open=op, snaps={})
        led._marks = md
        stake = rng.choice([20_000_000, 25_000_000, 30_000_000])
        why = led.evaluate(view, T0, stake)
        unfilled = sum(s for s, f in zip(stakes, filled) if not f)
        worst = cash - unfilled - stake - RESERVE * (n + 1)
        if why is None:
            accepted += 1
            assert worst > -BUDGET
            # unrealized marks must not have been the reason it passed
            led._marks = {}
            assert led.evaluate(view, T0, stake) is None
        elif why == "lane_budget_presend":
            refused += 1
            assert worst <= -BUDGET
        else:
            assert why in ("lane_budget_stop", "lane_max_open", "lane_stake_out_of_bounds")
    assert accepted > 100 and refused > 50


def test_eight_full_wipes_at_max_stake_still_fit_030():
    assert 8 * (STAKE + RESERVE) == 270_880_000 < BUDGET


def test_daily_010_preentry_still_binds(tmp_path):
    led = _ledger(tmp_path)
    now = T0
    day = H.day_of(now)
    view = _view(-40_000_000, 2, pnl=-110_000_000, snaps={day: (0, 0)})
    led._marks = {f"m{i}": (STAKE, now) for i in range(2)}
    assert led.evaluate(view, now, STAKE) == "lane_daily_stop"


def test_max_open_8_still_before_presend(tmp_path):
    led = _ledger(tmp_path)
    view = _view(-100_000_000, 8)
    led._marks = {f"m{i}": (STAKE, T0) for i in range(8)}
    assert led.evaluate(view, T0, STAKE) == "lane_max_open"


# -- (4) unchanged guards --------------------------------------------------------------------------


def test_canary_end_ms_wallet_a3_live_ok_constants_unchanged():
    assert F.CANARY_MAX_MS == 14 * 86_400_000
    assert FIRST_SEND_MS + F.CANARY_MAX_MS == 1_795_357_848_897  # 2026-11-22 06:30:48.897 PST
    assert END_MS == 1_796_688_000_000
    assert X.LANE_DAILY_MAX_LAMPORTS == 100_000_000
    assert X.LANE_MAX_OPEN == 8
    assert X.LIVE_OK_DEFAULT.endswith("LIVE_OK")
    assert F.PRIORITY_LAMPORTS == 155_000
    assert F.SELL_RESERVE_SENDS == pl.SELL_MAX_ATTEMPTS == 10
    assert callable(F.wallet_floor_lamports)
    src = Path(X.__file__).read_text()
    assert "a3_pause" in src and "LIVE_OK" in src
    assert "object.__setattr__(ex.limits, \"max_open\", int(cfg.max_open))" in src


def test_first_send_write_once_and_canary_at_plus_14d(tmp_path):
    clk = Clock(T0)
    br, inner = live_bridge(tmp_path, clk)
    br.fs["first_send_ms"] = FIRST_SEND_MS
    br._save()
    br.release_lane_lock()
    br2, inner2 = live_bridge(tmp_path, Clock(T0 + 60_000))
    assert br2.fs["first_send_ms"] == FIRST_SEND_MS
    br2.release_lane_lock()

    clk3 = Clock(FIRST_SEND_MS + F.CANARY_MAX_MS + 1)
    br3, inner3 = live_bridge(tmp_path, clk3)
    br3.fs["first_send_ms"] = FIRST_SEND_MS
    br3._save()
    lg = Log(tmp_path)
    lg.start(T0 - 1000)
    lg.entry(int(clk3.t) - 50, "o-late", pool="P")
    for r in br3.read_new_rows():
        clk3.t = r["ms"] + 50
        br3.handle_row(r)
        br3._poll_ports()
    why = [json.loads(x)["why"] for x in br3.log_path.read_text().splitlines() if '"entry_refused"' in x]
    assert "canary_expired" in why
    assert inner3.buys == []
    assert br3.fs["first_send_ms"] == FIRST_SEND_MS
    br3.release_lane_lock()
    assert inner.buys == []


def test_end_ms_still_refuses_after_dec7(tmp_path):
    clk = Clock(END_MS)
    br, inner = live_bridge(tmp_path, clk)
    br.fs["first_send_ms"] = FIRST_SEND_MS
    lg = Log(tmp_path)
    lg.start(T0 - 1000)
    lg.entry(END_MS - 50, "o-end", pool="P")
    for r in br.read_new_rows():
        clk.t = END_MS
        br.handle_row(r)
        br._poll_ports()
    why = [json.loads(x)["why"] for x in br.log_path.read_text().splitlines() if '"entry_refused"' in x]
    assert "end_ms" in why and inner.buys == []
    br.release_lane_lock()


def test_cursor_and_first_send_survive_restart_with_five_open_slots_in_sidecar(tmp_path):
    clk = Clock(T0)
    br, inner = live_bridge(tmp_path, clk)
    br.fs["first_send_ms"] = FIRST_SEND_MS
    br.state.start_seen = True
    br.state.seq = 8
    br.state.prev = "seq8prev"
    br.state.cursor = {"2026-11-09.jsonl": 4096}
    br._save()
    snap = (br.state.seq, br.state.prev, dict(br.state.cursor), br.fs["first_send_ms"])
    br.release_lane_lock()
    br2, _ = live_bridge(tmp_path, clk)
    assert (br2.state.seq, br2.state.prev, br2.state.cursor, br2.fs["first_send_ms"]) == snap
    br2.release_lane_lock()
    assert inner.buys == []


# -- FakeRpc: 10-sell abandonment still at p155 ----------------------------------------------------


@pytest.fixture
def hermetic_live_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.probe_executor.LIVE_DIR", tmp_path / "probe-live-dir")
    return tmp_path


def _cfg(tmp: Path):
    write_live_ok(tmp)
    raw = {
        "mode": "live",
        "bot_id": "l1-floor-v2",
        "frozen_sha": F.FROZEN_SHA,
        "root": str(tmp / "rules"),
        "state_dir": str(tmp / "state"),
        "end_ms": END_MS,
        "live_ok_path": str(tmp / "LIVE_OK"),
        "lane": {
            "bot_id": "floor-lane-1",
            "ledger_path": str(tmp / "lane" / "ledger.jsonl"),
            "stop_path": str(tmp / "lane" / "STOP"),
            "stake_lamports": STAKE,
            "max_open": 8,
            "budget_lamports": BUDGET,
            "daily_loss_lamports": 100_000_000,
            "fee_reserve_lamports": RESERVE,
            "mark_stale_s": 60,
        },
    }
    cfg, lane, live_ok = X.make_config(raw)
    cfg = dataclasses.replace(cfg, stop_file=str(tmp / "STOP"), halt_file=str(tmp / "HALT"), end_ms=END_MS)
    return cfg, lane, live_ok


@pytest.mark.skipif(pytest.importorskip("solders", reason="solders required") is None, reason="solders")
def test_ten_failed_sells_still_abandon_at_p155(hermetic_live_dir):
    from solders.keypair import Keypair
    from solders.transaction import VersionedTransaction
    import base64
    from tools.test_probe_live import land_buy, land_sell

    clock = PClock(T0)
    rpc = LiveRpc(clock)
    cfg, _, _ = _cfg(hermetic_live_dir)
    inner = B.make_live_port(cfg, rpc, Keypair(), now_ms=clock)
    wrapped = X.FloorLanePort(inner, cfg)
    wrapped.pre_buy = lambda _o: None
    ok, why = wrapped.buy({"order_id": "o1", "mint": MINT, "decision_ms": clock(), "stake_lamports": cfg.stake_lamports})
    assert ok, why
    land_buy(inner.ex, rpc)
    wrapped.poll()
    ok, why = wrapped.sell({"mint": MINT}, "time_24h")
    assert ok, why
    fee = F.SEND_FEE_LAMPORTS
    for _ in range(99):
        if MINT in inner.ex.state.pending:
            land_sell(inner.ex, rpc, proceeds=0, err=FAIL_ERR, fee=fee)
        clock.t += 11 * 60_000
        wrapped.poll()
    sigs = []
    for _t, b64 in rpc.sent:
        s = str(VersionedTransaction.from_bytes(base64.b64decode(b64)).signatures[0])
        if s not in sigs:
            sigs.append(s)
    assert len(sigs) - 1 == 10
    assert inner.ex.state.open[MINT]["abandoned"] is True
    for _t, b64 in rpc.sent[1:]:
        assert B.signed_priority_lamports(b64) == F.PRIORITY_LAMPORTS


@pytest.mark.skipif(pytest.importorskip("solders", reason="solders required") is None, reason="solders")
def test_probe_open_survives_same_config_restart_and_lift_is_reapplied(hermetic_live_dir):
    from solders.keypair import Keypair
    from tools.test_probe_live import land_buy

    clock = PClock(T0)
    rpc = LiveRpc(clock)
    cfg, _, _ = _cfg(hermetic_live_dir)
    inner = B.make_live_port(cfg, rpc, Keypair(), now_ms=clock)
    wrapped = X.FloorLanePort(inner, cfg)
    wrapped.pre_buy = lambda _o: None
    ok, why = wrapped.buy({"order_id": "o1", "mint": MINT, "decision_ms": clock(), "stake_lamports": cfg.stake_lamports})
    assert ok, why
    land_buy(inner.ex, rpc)
    wrapped.poll()
    first = inner.ex.state.first_attempt_ms
    attempts = inner.ex.state.attempts
    saved = json.loads(Path(inner.ex.state_path).read_text())
    assert MINT in saved["open"] and saved["first_attempt_ms"] == first

    clock2 = PClock(T0 + 5_000)
    inner2 = B.make_live_port(cfg, LiveRpc(clock2), Keypair(), now_ms=clock2)
    wrapped2 = X.FloorLanePort(inner2, cfg)
    wrapped2.pre_buy = lambda _o: None
    assert MINT in inner2.ex.state.open
    assert inner2.ex.state.first_attempt_ms == first
    assert inner2.ex.state.attempts == attempts
    assert inner2.ex.limits.max_open == 8
    assert inner2.ex.limits.max_attempts == 100_000
    assert inner2.ex.limits.max_days == 60.0
