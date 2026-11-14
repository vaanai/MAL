"""floor_nov_paper --pricing-lat-entry-s (QP ruling 2026-11-14 osb replay screen, sections 2 and 6): the option is passed to GapRangeEngine's pricing_lat_entry_s (PR #1390), nothing else changes.
Synthetic fixtures only (tools/test_osb_follow world: three pools, one take-profit, two time exits); no real tape, ledger, book or host path is opened. main() runs in-process with the path guard
and the engine's slot clock / table mode patched the way tools/test_osb_follow does. Nothing here says any book makes money."""
import functools
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "research"))
import floor_nov_paper as NP  # noqa: E402
import floor_gap_range_engine as G  # noqa: E402
import osb_follow as O  # noqa: E402
from tools.test_arena_rule_l1 import H0, row as trow  # noqa: E402
from tools.test_floor_arena_executor import _host_log_scenarios  # noqa: E402
from tools.test_osb_follow import EK, N_HOURS, T0, HOUR, _env, link, make_world, shifted_rows  # noqa: E402,F401  (_env: the autouse env fixture of that module)


@pytest.fixture
def world(tmp_path):
    """tools/test_osb_follow's three-pool fixture (A enters and takes profit, B and C enter and time out) plus filler prints every 15 s (rotating over the three pools) from 30 h 16 min (just after the first trigger) to the end of the 60 h tape for all three pools,
    so the entries and exits are priced (the osb fixture alone leaves them open). The fillers are ordinary buys / sells (no drained-pool sell), so the host's decisions are the fixture's own."""
    rows = _host_log_scenarios(tmp_path)
    for i, t in enumerate(range(H0 + 30 * HOUR + 960_000, H0 + 60 * HOUR, 15_000)):  # one row every 15 s (the engine reads silence over 60 s as a data_loss gap)
        rows.append(trow(t, "ABC"[i % 3], "buy" if (i // 3) % 2 else "sell", q=10 * 10**9 + (i % 7) * 10**8, n=i + 1))  # the pool moves from print to print (0.1 SOL steps on 10 SOL)
    for pool, dt in (("A", 0), ("B", 600_000), ("C", 1_200_000)):  # a print per slot from 2 s to 8 s after each trigger, the pool moving by 0.1 SOL a print: the strict and the later landing slot see different prices
        for j, t in enumerate(range(H0 + 30 * HOUR + 900_000 + dt + 2_000, H0 + 30 * HOUR + 900_000 + dt + 8_001, 400)):
            rows.append(trow(t, pool, "buy" if j % 2 else "sell", q=10 * 10**9 + (j % 11) * 10**8, n=5000 + j))
    return make_world(tmp_path, shifted_rows(tmp_path, sorted(rows, key=lambda r: (r["t_recv_ms"], r.get("pool", "")))))

# md5 of the strict fixture run's ledger projection (osb_follow.tree_md5: no per-process fields). The engine's code state is pinned in run_np (code_state_fn), so the value does not depend on the checkout's git HEAD:
# computed on unmodified origin/main (ade20cd8) and at the PR head, with the same fixture and the same pin; the test must pass at any commit.
FROZEN_STRICT_LEDGER_MD5 = "29e167906262cbf8475464d472e34e3e"
FROZEN_STRICT_VARIANT_JSON = json.dumps({"variant": {}, "frozen_rule": True, "registered_frozen_sha_is_not_the_rule": False, "send": 160000}, indent=1, sort_keys=True) + "\n"  # the header of main, same bytes


def run_np(tmp, name, *extra, monkeypatch):
    src, tape = tmp / "src", tmp / "tape"
    link(src, tape, sorted(p.name for p in src.glob("tape-*")), N_HOURS)
    monkeypatch.setattr(NP, "require_files_ok", lambda d: None)
    orig_rules = NP.load_rules_v2

    def rules(ids):
        rs = orig_rules(ids)
        for r in rs:
            r.table_mode = "first_print"  # the fixture has no create / complete rows
        return rs
    monkeypatch.setattr(NP, "load_rules_v2", rules)
    monkeypatch.setattr(NP, "GapRangeEngine", functools.partial(G.GapRangeEngine, **EK, code_state_fn=lambda: ("0" * 40, False)))
    root = tmp / name
    argv = ["floor_nov_paper.py", "--root", str(root), "--tape-dir", str(tape), "--from-ms", str(T0), "--to-ms", str(T0 + N_HOURS * HOUR), *extra]
    monkeypatch.setattr(sys, "argv", argv)
    NP.main()
    return root


def rows(root, *kinds):
    return [r for _, r in O.ledger_rows(root / "ledger") if r.get("kind") in kinds]


def plain(r):
    r = dict(r)
    r["stack"] = {k: v for k, v in r.get("stack", {}).items() if k not in ("pricing_lat_entry_s", "k_entry_slots")}
    return json.dumps(r, sort_keys=True)


def test_option_absent_output_is_byte_identical_to_main(world, monkeypatch):
    root = run_np(world[0], "strict", monkeypatch=monkeypatch)
    assert len(rows(root, "trade")) > 0 and "pricing_lat_entry_s" not in (root / "variant.json").read_text()
    assert O.tree_md5(root / "ledger") == FROZEN_STRICT_LEDGER_MD5
    assert (root / "variant.json").read_text() == FROZEN_STRICT_VARIANT_JSON
    assert all("pricing_lat_entry_s" not in r["stack"] and "k_entry_slots" not in r["stack"] for r in rows(root, "trade", "excluded"))


def test_latency_run_records_the_value_and_keeps_the_decisions(world, monkeypatch):
    tmp = world[0]
    s = run_np(tmp, "strict", monkeypatch=monkeypatch)
    t = run_np(tmp, "lat", "--pricing-lat-entry-s", "4.085", monkeypatch=monkeypatch)
    assert json.loads((t / "variant.json").read_text())["pricing_lat_entry_s"] == 4.085
    st, tt = rows(s, "trade"), rows(t, "trade")
    assert tt and all(r["stack"]["pricing_lat_entry_s"] == 4.085 and r["stack"]["k_entry_slots"] >= 0 for r in tt + rows(t, "excluded"))
    assert O.decisions_md5_from_rows(rows(s, "open", "trade", "excluded")) == O.decisions_md5_from_rows(rows(t, "open", "trade", "excluded"))  # decisions identical
    assert sorted((r["position_id"], r["decision_ms"]["entry"], r["decision_ms"]["exit"]) for r in st) == sorted((r["position_id"], r["decision_ms"]["entry"], r["decision_ms"]["exit"]) for r in tt)


def test_larger_k_moves_the_entry_fill_and_smaller_or_equal_k_does_not(world, monkeypatch):
    """EK = 0.4 s per slot, d = 7: strict k(1.9 s) = 5, the twin k = ceil(lat / 0.4) - 7: 3.010 -> 1, 4.085 -> 4, 5.590 -> 7 (clipped lower bound 0). Only a larger k than the strict one is priced later."""
    tmp = world[0]
    s = run_np(tmp, "strict", monkeypatch=monkeypatch)
    t = run_np(tmp, "lat", "--pricing-lat-entry-s", "5.590", monkeypatch=monkeypatch)
    st = {(r["position_id"], r["stake_sol"]): r for r in rows(s, "trade")}
    tt = {(r["position_id"], r["stake_sol"]): r for r in rows(t, "trade")}
    both = sorted(set(st) & set(tt))
    assert both and all(tt[k]["stack"]["k_entry_slots"] == 7 for k in both)
    moved = [k for k in both if tt[k]["calc"]["1p9"]["landing_slot"] != st[k]["calc"]["1p9"]["landing_slot"]]
    assert moved and all(tt[k]["calc"]["1p9"]["landing_slot"] == st[k]["calc"]["1p9"]["landing_slot"] + 2 for k in moved)
    assert any(tt[k]["calc"]["1p9"]["Qe"] != st[k]["calc"]["1p9"]["Qe"] for k in moved)  # the entry fill's pool state is the later slot's
    assert all(tt[k]["decision_ms"] == st[k]["decision_ms"] and tt[k]["calc"]["1p3"] == st[k]["calc"]["1p3"] for k in both)  # the 1.3 leg and the decisions are the strict run's
    # a position that left the priced set did so only as exit_before_entry_landing (counted, never priced)
    gone = set(st) - set(tt)
    assert {r["exclusion_reason"] for r in rows(t, "excluded") if (r["position_id"], r["stake_sol"]) in gone} <= {G.EXIT_BEFORE_ENTRY}


def test_strict_k_latency_reproduces_the_strict_rows_apart_from_the_two_stack_fields(world, monkeypatch):
    """at the fixture's 0.4 s per slot the strict k is 5; no public value gives k = 5 (3.010 -> 1, 4.085 -> 4, 5.590 -> 7), so the byte-for-byte leg is run at the engine level below the CLI,
    through the same wiring (engine built as floor_nov_paper builds it, here with the strict-equal total 1.9 s ... 5 + 7 = 12 slots = 4.8 s)"""
    tmp = world[0]
    s = run_np(tmp, "strict", monkeypatch=monkeypatch)
    monkeypatch.setattr(NP, "LAT_CHOICES", (4.8,))  # test-only: widen the public choices to the value whose k equals the strict k (the CLI parser reads LAT_CHOICES at main())
    t = run_np(tmp, "eq", "--pricing-lat-entry-s", "4.8", monkeypatch=monkeypatch)
    assert [plain(r) for r in rows(s, "open", "trade", "excluded")] == [plain(r) for r in rows(t, "open", "trade", "excluded")]
    assert all(r["stack"]["k_entry_slots"] == 5 for r in rows(t, "trade"))


@pytest.mark.parametrize("bad", ["3.0", "4.0", "0", "-4.085", "nan", "inf", "abc", "4.086"])
def test_undeclared_value_is_refused_with_exit_2(world, monkeypatch, bad, capsys):
    with pytest.raises(SystemExit) as e:
        run_np(world[0], "x", "--pricing-lat-entry-s", bad, monkeypatch=monkeypatch)
    assert e.value.code == 2 and "pricing-lat-entry-s" in capsys.readouterr().err
    assert not (world[0] / "x").exists()


def test_declared_values_are_exactly_the_three():
    assert NP.LAT_CHOICES == (3.010, 4.085, 5.590)


def test_a_latency_run_refuses_a_strict_root_and_the_reverse_and_another_value(world, monkeypatch, capsys):
    tmp = world[0]
    run_np(tmp, "strict", monkeypatch=monkeypatch)
    before = (tmp / "strict" / "variant.json").read_text()
    with pytest.raises(SystemExit) as e:
        run_np(tmp, "strict", "--pricing-lat-entry-s", "4.085", monkeypatch=monkeypatch)
    assert e.value.code == 2 and "refusing --root" in capsys.readouterr().err and (tmp / "strict" / "variant.json").read_text() == before
    run_np(tmp, "lat", "--pricing-lat-entry-s", "4.085", monkeypatch=monkeypatch)
    for extra in ((), ("--pricing-lat-entry-s", "5.590")):
        with pytest.raises(SystemExit) as e:
            run_np(tmp, "lat", *extra, monkeypatch=monkeypatch)
        assert e.value.code == 2 and "refusing --root" in capsys.readouterr().err
    assert json.loads((tmp / "lat" / "variant.json").read_text())["pricing_lat_entry_s"] == 4.085


def test_odd_roots_are_refused_cleanly(world, monkeypatch, capsys):
    """reviewer minors: a variant.json that is valid JSON but not an object, and a ledger without variant.json, are refused with exit 2 (the latter only for a latency run)"""
    tmp = world[0]
    (tmp / "odd").mkdir()
    (tmp / "odd" / "variant.json").write_text("[1]")
    for extra in ((), ("--pricing-lat-entry-s", "4.085")):
        with pytest.raises(SystemExit) as e:
            run_np(tmp, "odd", *extra, monkeypatch=monkeypatch)
        assert e.value.code == 2 and "not a JSON object" in capsys.readouterr().err
    run_np(tmp, "nov", monkeypatch=monkeypatch)
    (tmp / "nov" / "variant.json").unlink()
    with pytest.raises(SystemExit) as e:
        run_np(tmp, "nov", "--pricing-lat-entry-s", "4.085", monkeypatch=monkeypatch)
    assert e.value.code == 2 and "no variant.json" in capsys.readouterr().err
    run_np(tmp, "nov", monkeypatch=monkeypatch)  # a strict run into a strict root without the field still works, as before this PR
    assert "pricing_lat_entry_s" not in (tmp / "nov" / "variant.json").read_text()
