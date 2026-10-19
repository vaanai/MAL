"""The J3 W_e explorer: its refusals (hours outside W_e, the open hour, paths under /data/mal/arena), its statistics helpers, and one small end-to-end replay of a
SYNTHETIC tape. Nothing here opens the real arena tape, a ledger or bots.json, and nothing here says any J3 cell makes money."""

from __future__ import annotations

import collections
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tools import arena_j3_explore as E
from tools import arena_rule_j3 as J
from tools import arena_stack as F
from tools.arena_j3_testkit import B, HOUR, HOUR0, MINT, OTHER, POOL, SEC, V, World, scenario, write_hours
from tools.arena_paper import Ledger, book_fail_legs, utc_day
from tools.arena_rules import stack_config

REPO = Path(__file__).resolve().parent.parent
RENT = 2_039_280
SHA = "abcdef1234567"


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp() * 1000)


@pytest.fixture(autouse=True)
def _plenty_of_disk(monkeypatch):
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


def fake_hour(d: Path, key: str, suffix: str = ".jsonl.zst") -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"tape-{key}{suffix}"
    p.write_bytes(b"not read by the validation")
    return p


# -------------------------------------------------------------------------------------------------------------------------- refusals
@pytest.fixture
def we(tmp_path, monkeypatch):
    """A temp arena tree (ARENA_ROOT points at tmp/arena, the tape lives in tmp/arena/tape) and a W_e shortened to two hours (08 and 09) so that a test needs two files,
    not 24. `window(n)` moves the end of W_e to n hours after 08:00."""
    monkeypatch.setattr(E, "ARENA_ROOT", str(tmp_path / "arena"))
    monkeypatch.setattr(E, "W_E_END_MS", HOUR0 + 2 * HOUR)
    return SimpleNamespace(tape=tmp_path / "arena" / "tape", window=lambda n: monkeypatch.setattr(E, "W_E_END_MS", HOUR0 + n * HOUR))


def test_the_w_e_of_the_spec():
    assert E.W_E_START_MS == B == _ms("2026-10-19T08:05:20")  # the tape's first receive time, not the hour start
    assert E.W_E_END_MS == _ms("2026-10-20T08:00:00")
    hours = E.w_e_hours()
    assert len(hours) == 24 and hours[0] == HOUR0 and hours[-1] == _ms("2026-10-20T07:00:00") and all(b - a == HOUR for a, b in zip(hours, hours[1:]))


def test_exactly_the_w_e_hours_are_accepted_and_sorted(we):
    a, b = (fake_hour(we.tape, k) for k in ("2026-10-19T09", "2026-10-19T08"))
    got = E.validate_inputs([a, b])
    assert [h.start_ms for h in got] == [HOUR0, HOUR0 + HOUR] and got[0].path == b


def test_the_full_24_hours_are_accepted_and_any_other_list_is_refused(we):
    we.window(24)  # the real W_e
    keys = [E._iso(h)[:13] for h in E.w_e_hours()]
    files = [fake_hour(we.tape, k) for k in keys]
    assert [h.start_ms for h in E.validate_inputs(list(reversed(files)))] == E.w_e_hours()
    with pytest.raises(E.ExploreRefused, match="not exactly the 24 W_e hours"):
        E.validate_inputs(files[:-1])  # one hour short
    with pytest.raises(E.ExploreRefused, match="not exactly"):
        E.validate_inputs(files[:1])  # a partial W_e is a different exploration
    with pytest.raises(E.ExploreRefused, match="not exactly"):
        E.validate_inputs(files[:3] + files[4:])  # a hole in the middle: refused, not read as a feed gap


@pytest.mark.parametrize("key", ["2026-10-19T07", "2026-10-19T10", "2026-10-18T23", "2026-10-21T00", "2026-09-25T06"])
def test_an_hour_outside_w_e_is_refused_and_so_is_a_list_that_holds_one(we, key):
    bad, good = fake_hour(we.tape, key), fake_hour(we.tape, "2026-10-19T08")
    with pytest.raises(E.ExploreRefused, match="outside W_e"):
        E.validate_inputs([bad])
    with pytest.raises(E.ExploreRefused, match="outside W_e"):
        E.validate_inputs([good, bad])


def test_the_hour_after_w_e_and_the_hour_before_it_are_refused_at_the_real_edges(we):
    we.window(24)
    for key in ("2026-10-20T08", "2026-10-19T07"):
        with pytest.raises(E.ExploreRefused, match="outside W_e"):
            E.validate_inputs([fake_hour(we.tape, key)])


def test_the_open_uncompacted_hour_is_refused(we):
    with pytest.raises(E.ExploreRefused, match="open"):
        E.validate_inputs([fake_hour(we.tape, "2026-10-19T08", ".jsonl")])
    closed = fake_hour(we.tape, "2026-10-19T09")
    fake_hour(we.tape, "2026-10-19T09", ".jsonl")  # compaction not finished: both files exist
    with pytest.raises(E.ExploreRefused, match="still exists"):
        E.validate_inputs([closed])
    with pytest.raises(E.ExploreRefused, match="unfinished"):
        E.validate_inputs([fake_hour(we.tape, "2026-10-19T08", ".jsonl.zst.tmp")])


def test_other_bad_lists_are_refused(we):
    a = fake_hour(we.tape, "2026-10-19T08")
    with pytest.raises(E.ExploreRefused, match="no tape hour"):
        E.validate_inputs([])
    with pytest.raises(E.ExploreRefused, match="twice"):
        E.validate_inputs([a, a])
    with pytest.raises(E.ExploreRefused, match="no such file"):
        E.validate_inputs([we.tape / "tape-2026-10-19T09.jsonl.zst"])
    with pytest.raises(E.ExploreRefused, match="not a closed tape hour"):
        E.validate_inputs([we.tape / "arena-feed-status.json"])
    with pytest.raises(E.ExploreRefused, match="not exactly"):
        E.validate_inputs([a])


def test_only_files_directly_in_the_arena_tape_dir_are_read(we, tmp_path):
    """R9(d): the input is the arena tape. The same name anywhere else (a shakedown copy, a sub directory, another arena dir) is refused."""
    ok = [fake_hour(we.tape, "2026-10-19T08"), fake_hour(we.tape, "2026-10-19T09")]
    assert len(E.validate_inputs(ok)) == 2
    for other in (tmp_path / "arena" / "_shakedown-x", tmp_path / "arena" / "tape" / "sub", tmp_path / "arena" / "ledger" / "bot", tmp_path / "elsewhere", tmp_path / "arena"):
        bad = fake_hour(other, "2026-10-19T09")
        with pytest.raises(E.ExploreRefused, match="not located directly in"):
            E.validate_inputs([ok[0], bad])


def test_a_symlinked_hour_path_is_refused(we, tmp_path):
    """The state reads the resolved path, so a link named for a W_e hour must not point at another hour or another file."""
    real9 = fake_hour(we.tape, "2026-10-19T09")
    link8 = we.tape / "tape-2026-10-19T08.jsonl.zst"
    link8.symlink_to(real9)  # named for hour 08, really hour 09, in the tape dir
    with pytest.raises(E.ExploreRefused, match="symlink"):
        E.validate_inputs([link8, real9])
    outside = tmp_path / "outside" / "tape-2026-10-19T08.jsonl.zst"
    outside.parent.mkdir()
    outside.write_bytes(b"x")
    link8.unlink()
    link8.symlink_to(outside)  # right name, wrong place
    with pytest.raises(E.ExploreRefused, match="symlink"):
        E.validate_inputs([link8, real9])
    link8.unlink()
    assert len(E.validate_inputs([fake_hour(we.tape, "2026-10-19T08"), real9])) == 2


def test_scratch_inside_the_arena_root_is_refused_and_nothing_is_created():
    for p in ("/data/mal/arena/_j3_scratch_never_created", "/data/mal/arena", "/data/mal/arena/tape/x/y"):
        with pytest.raises(E.ExploreRefused, match="inside"):
            E.check_scratch(p)
    assert not Path("/data/mal/arena/_j3_scratch_never_created").exists()
    with pytest.raises(E.ExploreRefused, match="contains"):
        E.check_scratch("/data/mal")


def test_scratch_checks_against_a_moved_arena_root(we, tmp_path):
    with pytest.raises(E.ExploreRefused, match="inside"):
        E.check_scratch(tmp_path / "arena" / "scratch")
    (tmp_path / "arena").mkdir(exist_ok=True)
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "arena")
    with pytest.raises(E.ExploreRefused, match="inside"):
        E.check_scratch(link / "scratch")  # a symlink into the tree is resolved
    ok = E.check_scratch(tmp_path / "scratch")
    assert ok == (tmp_path / "scratch").resolve() and not ok.exists()  # checking creates nothing
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "x").write_text("a")
    with pytest.raises(E.ExploreRefused, match="empty directory"):
        E.check_scratch(tmp_path / "full")


def test_main_returns_2_on_a_refusal_and_writes_nothing(we, tmp_path, capsys):
    bad = fake_hour(we.tape, "2026-10-20T08")
    assert E.main(["--hours", str(bad), "--scratch", str(tmp_path / "s")]) == 2
    assert "outside W_e" in capsys.readouterr().err and not (tmp_path / "s").exists()
    assert E.main(["--hours", str(fake_hour(we.tape, "2026-10-19T08")), "--scratch", "/data/mal/arena/_never"]) == 2
    assert not Path("/data/mal/arena/_never").exists()


def test_module_names_no_arena_path_of_its_own():
    code = [ln for ln in (REPO / "tools" / "arena_j3_explore.py").read_text().splitlines() if not ln.lstrip().startswith(("#", '"""'))]
    assert not any('"/data/' in ln or "'/data/" in ln for ln in code)  # the arena root comes from arena_rules, every other path from --scratch or --hours
    assert E.ARENA_ROOT == "/data/mal/arena"


# -------------------------------------------------------------------------------------------------------------------------- statistics helpers
def test_date_cluster_bootstrap():
    x = np.array([1.0, 1.0, 1.0, -1.0, 2.0])
    days = ["d1", "d1", "d1", "d2", "d2"]
    lo = E.date_cluster_boot_lo(x, days)
    assert lo == E.date_cluster_boot_lo(x, days)  # seed 1: deterministic
    # two dates: a draw is AA, AB, BA or BB; BB is a quarter of the draws, so the 5th percentile is the worse date's own mean
    assert lo == pytest.approx(0.5)  # d1 mean 1.0, d2 mean 0.5
    assert E.date_cluster_boot_lo([1, 2, 3], ["d", "d", "d"]) is None  # one date: no clusters to resample
    assert E.date_cluster_boot_lo([], []) is None
    pos = E.date_cluster_boot_lo([0.1] * 6, ["a", "a", "b", "b", "c", "c"])
    assert pos == pytest.approx(0.1)
    assert E.date_cluster_boot_lo([-1, 3, 3, 3], ["a", "b", "c", "c"]) <= 2.5  # never above the sample mean here


def test_kill_rule():
    ok = {"n": 5, "mean_sol": 0.01, "ex_top3_sol": 0.005, "ex_best_day_sol": 0.002}
    assert E.kill_check(ok) == {"killed": False, "why": []}
    for key, label in (("mean_sol", "mean <= 0"), ("ex_top3_sol", "ex-top-3 <= 0"), ("ex_best_day_sol", "ex-best-day <= 0")):
        for bad in (0.0, -0.1, None):
            assert E.kill_check({**ok, key: bad}) == {"killed": True, "why": [label]}
    assert E.kill_check({"n": 0}) == {"killed": True, "why": ["no priced trades"]}


def test_split_stake_mirrors_the_scorers_exclusions():
    def tr(pid, kind="trade", stake=0.25, **kw):
        return {"kind": kind, "position_id": pid, "stake_sol": stake, **kw}

    rows = [tr("a"), tr("b", v_src={"entry": "row", "exit": "none", "span": {}}), tr("c", v_src={"entry": "row", "exit": "row", "span": {"none": 1}}), tr("d"),
            {"kind": "late_print", "position_id": "d", "gap_overlap": True}, {"kind": "late_print", "position_id": "a", "gap_overlap": False},
            tr("e", kind="excluded", exclusion_reason="engine_restart"), tr("f", stake=1.0)]
    trades, excluded = E.split_stake(rows, 0.25)
    assert [r["position_id"] for r in trades] == ["a"]
    assert [(r["position_id"], why) for r, why in excluded] == [("b", "missing_v"), ("c", "missing_v"), ("d", "late_print_gap"), ("e", "engine_restart")]
    assert [r["position_id"] for r in E.split_stake(rows, 1.0)[0]] == ["f"]


def test_entry_impact_is_net_over_effective_quote_reserve():
    row = {"stake_sol": 1.0, "calc": {"1p9": {"Qe": 80e9, "Be": 8e14}}}
    fee = float(F.fee_ppm(80e9, 8e14))
    assert E.entry_impact_bps(row) == pytest.approx(1e4 * 1e9 * (1 - fee / 1e6) / 80e9)
    assert E.entry_impact_bps({"stake_sol": 1.0, "calc": {"1p9": {"Qe": 0.0, "Be": 8e14}}}) is None
    assert E.entry_impact_bps({"stake_sol": 1.0, "calc": {"1p9": {"Qe": float("nan"), "Be": 8e14}}}) is None


# -------------------------------------------------------------------------------------------------------------------------- one small end-to-end replay
def two_hour_rows():
    w, _ = scenario()
    w.flow(POOL["PA"], MINT["PA"], B + 3_501 * SEC, B + 3_700 * SEC, v=V)  # the parent keeps trading into the second hour
    return w.rows


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))
        base = tmp_path_factory.mktemp("j3e2e")
        mp.setattr(E, "ARENA_ROOT", str(base / "arena"))
        mp.setattr(E, "W_E_END_MS", HOUR0 + 2 * HOUR)
        files = write_hours(base / "arena" / "tape", two_hour_rows())
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        rc = E.main(["--hours", *map(str, files), "--scratch", str(base / "s1")])
        rep2 = E.run_exploration(files, base / "s2", sha=SHA)
        E.write_report(base / "s2", rep2)
        yield {"base": base, "files": files, "before": before, "rc": rc, "s1": base / "s1", "s2": base / "s2", "rep": json.loads((base / "s1" / "REPORT.json").read_text())}


def test_end_to_end_report_shape_and_integrity(runs):
    rep = runs["rep"]
    assert runs["rc"] == 0 and rep["valid"] and rep["integrity"]["clean"] and rep["schema"] == E.SCHEMA
    assert rep["window"]["hours"] == ["2026-10-19T08:00:00Z", "2026-10-19T09:00:00Z"] and rep["window"]["end_utc"] == "2026-10-19T10:00:00Z"
    assert rep["window"]["start_utc"] == "2026-10-19T08:05:20Z" and rep["window"]["start_ms"] == B  # W_e starts at the tape's first receive time, not at the hour
    assert rep["integrity"]["rows_before_start"] == 0
    assert sorted(rep["cells"]) == sorted(J.bot_id(k, h) for k, h in J.cells()) and [c["bot_id"] for c in rep["spec"]["cells"]] == [J.bot_id(k, h) for k, h in J.cells()]
    assert rep["multiplicity"]["cells_declared"] == 12
    assert [i["sha256"] for i in rep["inputs"]] == [runs["before"][p.name] for p in runs["files"]]
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in runs["files"]} == runs["before"]  # the tape files were only read
    assert any("IN-SAMPLE" in x for x in rep["labels"])
    assert rep["stack"]["config"] == stack_config(7).as_dict() and rep["stack"]["fields"]["rent"] == 0 and rep["stack"]["fields"]["gap_slots"] == 150
    assert rep["stack"]["fields"]["gap_silence_ms"] == 60_000 and rep["stack"]["strand_rent_lamports"] == RENT
    md = (runs["s1"] / "REPORT.md").read_text()
    assert all(b in md for b in rep["cells"]) and "IN-SAMPLE" in md and "Rent-charged leg" in md
    assert sorted(p.name for p in runs["s1"].iterdir()) == ["REPORT.json", "REPORT.md", "bots.json", "bots.json.lock", "ledger", "rules", "tape"]
    assert sorted(p.name for p in runs["base"].iterdir()) == ["arena", "s1", "s2"]  # nothing was written outside the scratch dirs


def test_end_to_end_counts_and_exclusions_by_reason(runs):
    cells = runs["rep"]["cells"]
    for k, attempts, triggers in ((1, 3, 10), (5, 3, 9), (20, 4, 9)):  # triggers include PC: a pool quoted in OTHER is a child whose parent has no pool
        for h in J.HOLDS_S:
            c = cells[J.bot_id(k, h)]
            assert (c["attempts"], c["opened"], c["refused_buys"], c["n_priced"], c["n_excluded"], c["triggers"]) == (attempts, attempts, 0, attempts, 0, triggers)
            assert c["attempts_reconcile"] and c["exclusions"]["unsold_at_window_end"] == 0 and c["utc_days"] == ["2026-10-19"]
    assert cells["j3-k1-h30"]["exclusions"]["no_attempt_by_rule"] == {
        J.R_PARENT_OPEN: 1, J.R_PARENT_V: 1, J.R_PARENT_NO_WSOL: 1, J.R_PARENT_NO_POOL: 1, f"{J.R_PARENT_YOUNG}_first_print": 2,
        f"{J.R_PARENT_SILENT}_first_print": 1, J.R_CHILD_NO_CREATE: 1}
    assert all(c["kill"]["killed"] for c in cells.values())  # a flat synthetic market loses fees: every cell is killed
    assert runs["rep"]["kill_summary"]["alive"] == [] and len(runs["rep"]["kill_summary"]["killed"]) == 12


def test_end_to_end_rent_charged_leg_is_the_exact_transform_and_impact_is_recomputed(runs):
    led = Ledger(runs["s1"] / "ledger")
    for bot in ("j3-k1-h30", "j3-k20-h900"):
        rows = [r for r in led.read_bot(bot) if r["kind"] == "trade" and r["stake_sol"] == 0.25]
        assert len(rows) == runs["rep"]["cells"][bot]["n_priced"] > 0
        for rule in ("total", "sc"):
            g = lambda key: np.array([r["calc"]["1p9"]["by_rule"][rule][key] for r in rows])  # noqa: E731
            pnl, fil, strand = g("pnl_nofail").astype(float), g("filled").astype(bool), g("strand").astype(bool)
            ssb = np.array([r["calc"]["1p9"]["ssb"] for r in rows], float)
            near = np.array([r["calc"]["1p9"]["nearby_lamports"] for r in rows], float)
            flat, press, _, p = book_fail_legs(pnl, fil, strand, ssb, near, 505_000) if rule == "total" else F.fail_legs(pnl, fil, ssb, near, 505_000)
            filled_state = np.array([r["calc"]["1p9"]["by_rule"][rule]["state"] == "filled" for r in rows])
            sub = runs["rep"]["cells"][bot]["subcells"]
            assert sub[f"flat_1p9_{rule}"]["total_sol"] == pytest.approx(flat.sum() / 1e9, abs=1e-12)
            assert sub[f"press_1p9_{rule}"]["total_sol"] == pytest.approx(press.sum() / 1e9, abs=1e-12)
            assert sub[f"flat_1p9_{rule}"]["rent_charged"]["total_sol"] == pytest.approx((flat - 0.85 * RENT * filled_state).sum() / 1e9, abs=1e-12)
            assert sub[f"press_1p9_{rule}"]["rent_charged"]["total_sol"] == pytest.approx((press - (1 - p) * RENT * filled_state).sum() / 1e9, abs=1e-12)
            assert sub[f"flat_1p9_{rule}"]["rent_charged"]["trips_transformed"] == int(filled_state.sum()) == len(rows)
            assert sub[f"flat_1p9_{rule}"]["fills"] == int(fil.sum()) and sub[f"flat_1p9_{rule}"]["n"] == len(rows)
        imp = runs["rep"]["cells"][bot]["entry_impact"]
        for stake in (0.25, 1.0):
            vals = [1e4 * stake * 1e9 * (1 - float(F.fee_ppm(r["calc"]["1p9"]["Qe"], r["calc"]["1p9"]["Be"])) / 1e6) / r["calc"]["1p9"]["Qe"] for r in rows]
            assert imp[str(stake)]["n"] == len(rows)
            assert imp[str(stake)]["mean_bps"] == pytest.approx(sum(vals) / len(vals), rel=1e-3)
        assert imp["1.0"]["mean_bps"] > 3.9 * imp["0.25"]["mean_bps"]


def test_end_to_end_no_child_in_any_ledger_or_decision_row(runs):
    seen = set()
    led = Ledger(runs["s1"] / "ledger")
    for bot in runs["rep"]["cells"]:
        seen |= {r["pool"] for r in led.read_bot(bot) if "pool" in r}
    children = {POOL[k] for k in ("CA1", "CA2", "CA3", "CB", "CC", "CD", "CE", "CE2", "CS", "CF", "PC")}
    assert seen and seen <= {POOL["PA"], POOL["PD"], POOL["PE"]} and not seen & children


def test_end_to_end_two_runs_give_byte_identical_reports(runs):
    a, b = (runs["s1"] / "REPORT.json").read_bytes(), (runs["s2"] / "REPORT.json").read_bytes()
    ja, jb = json.loads(a), json.loads(b)
    assert ja["provenance"]["scratch_sha"] != jb["provenance"]["scratch_sha"]  # the two runs were registered at different shas: that, and only that, may differ
    ja.pop("provenance"), jb.pop("provenance")
    assert ja == jb and len(a) > 1000
    assert ja["digests"] == runs["rep"]["digests"]
    assert (runs["s1"] / "REPORT.md").read_text() == (runs["s2"] / "REPORT.md").read_text()  # the markdown carries no provenance either
    assert str(runs["base"]) not in a.decode()  # no path in the report: it is a function of the tape


def test_recording_subclass_decides_what_the_plain_rule_decides(tmp_path, monkeypatch):
    """The explorer hosts RecordingJ3; its decision logs equal those of a host of the plain J3Rule on the same tape."""
    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    from tools.arena_rules_testkit import decision_files, make_host

    rows = two_hour_rows()
    logs = {}
    for name, cls in (("plain", J.J3Rule), ("rec", E.RecordingJ3)):
        h = make_host(tmp_path / name, J.make_rules(rule_cls=cls), rows=rows, clock=B)
        h.resume()
        h.run(B, B + 3_590 * SEC)
        logs[name] = {r.bot_id: decision_files(tmp_path / name / "rules", r.bot_id) for r in h.rules}
    assert logs["plain"] == logs["rec"] and all(logs["plain"].values())


# -------------------------------------------------------------------------------------------------------------------------- B1: the rent-charged leg's selector, on hand-built rows
def _hand_state(qe=80e9, be=8e14, qx=80e9, bx=8e14, ref=None, ssb=0.0, nearby=0.0):
    return {"ref": qe / be if ref is None else ref, "Qe": {1.3: qe, 1.9: qe}, "Be": {1.3: be, 1.9: be}, "ssb": {1.3: ssb, 1.9: ssb}, "nearby": {1.3: nearby, 1.9: nearby}, "Qx": qx, "Bx": bx}


def _hand_row(pid: str, ms: int, st: dict, stake: float = 0.25) -> dict:
    """A ledger `trade` row in the shape ArenaEngine.build_rows writes, priced by arena_paper.price_leg on a hand-made state."""
    from tools.arena_paper import LAT_TAG, price_leg

    cfg = stack_config(7)
    legs: dict = {"total": {}, "sc": {}}
    calc: dict = {}
    for lat in F.LATENCIES_S:
        tag = LAT_TAG[lat]
        calc[tag] = {"landing_slot": 1, "Qe": st["Qe"][lat], "Be": st["Be"][lat], "spot": st["ref"], "ssb": st["ssb"][lat], "nearby_lamports": st["nearby"][lat], "Qx": st["Qx"], "Bx": st["Bx"],
                     "by_rule": {}}
        for rule in ("total", "sc"):
            r = price_leg("pumpswap", stake * 1e9, st, lat, cfg, rule)
            legs[rule][f"flat_{tag}"] = {"state": r["state"], "net_lamports": r["flat"]}
            legs[rule][f"press_{tag}"] = {"state": r["state"], "net_lamports": r["press"]}
            calc[tag]["by_rule"][rule] = {"pnl_nofail": r["pnl"], "filled": r["filled"], "state": r["state"], "strand": r["strand"], "e1_extra": r["e1_extra"], "p_press_provisional": r["p"]}
    return {"kind": "trade", "position_id": pid, "trade_id": f"{pid}-s250", "stake_sol": stake, "decision_ms": {"entry": ms, "exit": ms + 60_000}, "legs": legs, "calc": calc,
            "stack": cfg.as_dict(), "state_src": "tape", "v_src": {"entry": "row", "exit": "row", "span": {"row": 3}}}


def test_rent_charged_leg_transforms_only_trips_in_state_filled():
    """DEC-031 Am.2 item 7: the selector is the leg STATE, not the `filled` flag. An exit_invalid trip has filled True (and strand False under 'sc'), but its pnl already carries
    the 2,039,280 strand rent, so charging it again would double count; a guarded trip has filled False. Neither may be transformed, under both strand rules."""
    day2 = B + 24 * HOUR
    guarded = _hand_state(ref=80e9 / 8e14 / 2)  # the decision-time spot is half the landing price: the 1.15 guard reverts the buy
    bad_exit = _hand_state(qx=1e9)  # an exit below the pool's real floor: exit_invalid
    states = [_hand_state(), _hand_state(qe=70e9, ssb=1.0, nearby=2e9), _hand_state(qe=90e9, qx=91e9), _hand_state(qe=75e9, qx=75.2e9, ssb=3.0, nearby=1e9), guarded, bad_exit]
    msx = [B, B + 5 * SEC, B + 9 * SEC, day2, day2 + 5 * SEC, day2 + 9 * SEC]
    trades = [_hand_row(f"p{i}", ms, st) for i, (ms, st) in enumerate(zip(msx, states))]
    subs = E.subcells(trades)
    assert set(subs) == {f"{m}_1p9_{r}" for m in ("flat", "press") for r in ("total", "sc")}
    for rule in ("total", "sc"):
        by = [t["calc"]["1p9"]["by_rule"][rule] for t in trades]
        assert [b["state"] for b in by] == ["filled"] * 4 + ["guarded", "exit_invalid"]
        assert [b["filled"] for b in by] == [True] * 4 + [False, True]  # the flag and the state differ on the exit_invalid trip
        assert by[5]["strand"] is (rule == "total")  # under 'sc' it has filled True and strand False: neither flag selects the state
        g = lambda key: np.array([b[key] for b in by])  # noqa: E731
        pnl, fil, strand = g("pnl_nofail").astype(float), g("filled").astype(bool), g("strand").astype(bool)
        ssb = np.array([t["calc"]["1p9"]["ssb"] for t in trades], float)
        near = np.array([t["calc"]["1p9"]["nearby_lamports"] for t in trades], float)
        flat, press, _, p = book_fail_legs(pnl, fil, strand, ssb, near, 505_000) if rule == "total" else F.fail_legs(pnl, fil, ssb, near, 505_000)
        sel = np.array([b["state"] == "filled" for b in by])
        assert sel.sum() == 4 and fil.sum() == 5
        for model, x, rc in (("flat", flat, flat - 0.85 * RENT * sel), ("press", press, press - (1 - p) * RENT * sel)):
            s = subs[f"{model}_1p9_{rule}"]
            assert s["n"] == 6 and s["fills"] == 5 and s["states"] == {"filled": 4, "guarded": 1, "exit_invalid": 1}
            assert s["rent_charged"]["trips_transformed"] == 4 < s["n"]  # the guarded and the exit_invalid trip are not transformed
            assert s["total_sol"] == pytest.approx(x.sum() / 1e9, abs=1e-12)
            assert s["rent_charged"]["total_sol"] == pytest.approx(rc.sum() / 1e9, abs=1e-12)
            assert s["total_sol"] - s["rent_charged"]["total_sol"] == pytest.approx(((0.85 if model == "flat" else (1 - p)) * RENT * sel).sum() / 1e9, abs=1e-12)
            assert s["rent_charged"]["mean_sol"] == pytest.approx(rc.mean() / 1e9, abs=1e-12)
            assert s["days"] == 2 and s["ci90_lo_date_cluster_sol"] is not None
    assert E.subcells([]) == {}


# -------------------------------------------------------------------------------------------------------------------------- N3: host counters in the integrity verdict
def test_integrity_is_not_clean_when_the_host_blocked_or_dropped_an_order():
    base = {"zst_errors": 0, "bad_lines": 0, "apply_errors": 0, "hook_errors": [], "price_errors": 0, "ledger_incidents": 0, "halted_books": [], "blocked_universe": 0,
            "order_before_clock": 0, "order_bad_type": 0}
    assert E.integrity_clean(base) is True
    for key, bad in (("blocked_universe", 1), ("order_before_clock", 1), ("order_bad_type", 2), ("zst_errors", 1), ("bad_lines", 1), ("apply_errors", 1), ("hook_errors", ["x"]),
                     ("price_errors", 1), ("ledger_incidents", 1), ("halted_books", ["j3-k1-h30"])):
        assert E.integrity_clean({**base, key: bad}) is False, key
    assert E.HOST_ZERO_COUNTERS == ("blocked_universe", "order_before_clock", "order_bad_type")


def test_end_to_end_integrity_reports_the_host_counters_at_zero(runs):
    i = runs["rep"]["integrity"]
    assert i["clean"] is True and (i["blocked_universe"], i["order_before_clock"], i["order_bad_type"]) == (0, 0, 0)


def test_rows_before_the_w_e_start_are_dropped_and_counted(tmp_path, we):
    """The state starts at W_e's start (08:05:20Z), not at the first hour's start: a row before it is never applied, and the count is in the integrity block (expected 0)."""
    w = World()
    w.trade(B - 10 * SEC, POOL["PA"], MINT["PA"], "buy")
    w.trade(B + 10 * SEC, POOL["PA"], MINT["PA"], "buy")
    (f,) = write_hours(we.tape, w.rows)
    h = E.Hour(HOUR0, f)
    st = E.GivenHoursState(tmp_path, [h], B)
    st.poll()
    assert st.counters["rows_before_start"] == 1 and st.pool(POOL["PA"]).n_trades == 1


# -------------------------------------------------------------------------------------------------------------------------- B2: --count-only, and the disclosure lines
def test_count_only_counts_rows_by_type_and_builds_no_state_or_engine(we, tmp_path, monkeypatch, capsys):
    rows = two_hour_rows()
    files = write_hours(we.tape, rows)
    for name in ("ArenaState", "GivenHoursState", "ArenaEngine", "Host", "register_rules"):
        monkeypatch.setattr(E, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError("count-only must not build state, engine or host")))
    listing = sorted(p.name for p in tmp_path.rglob("*"))
    assert E.main(["--count-only", "--hours", *map(str, files)]) == 0
    out = json.loads(capsys.readouterr().out)
    want = collections.Counter(r["type"] for r in rows)
    assert out["rows_by_type"] == dict(sorted(want.items())) and out["rows_total"] == len(rows) and out["bad_lines"] == 0 and out["rows_before_w_e_start"] == 0
    assert set(out["per_hour"]) == {"2026-10-19T08:00:00Z", "2026-10-19T09:00:00Z"}
    assert sum(sum(v.values()) for v in out["per_hour"].values()) == len(rows)
    m = want["trade"] / 1e6
    assert out["trade_rows_million"] == round(m, 3) and out["sizing"]["suggested_memory_max_gb"] == round(1.2 * m + 4.0, 1) and out["sizing"]["engine_gb_per_million_trade_rows"] == 0.96
    assert sorted(p.name for p in tmp_path.rglob("*")) == listing  # nothing written anywhere


def test_count_only_counts_rows_before_the_w_e_start_and_bad_lines(we):
    w = World()
    w.trade(B - 10 * SEC, POOL["PA"], MINT["PA"], "buy")
    w.trade(B + 10 * SEC, POOL["PA"], MINT["PA"], "buy")
    (f,) = write_hours(we.tape, w.rows)
    f.write_bytes(b"")  # replace with a file that has a torn line and a non-object line
    import subprocess

    raw = we.tape / "x.jsonl"
    raw.write_bytes(json.dumps(w.rows[0]).encode() + b"\nnot json\n[1]\n" + json.dumps(w.rows[1]).encode() + b"\n")
    with open(f, "wb") as fh:
        subprocess.run(["zstd", "-q", "-c", str(raw)], stdout=fh, check=True)
    raw.unlink()
    c = E.count_rows([E.Hour(HOUR0, f)])
    assert c["rows_by_type"] == {"trade": 2} and c["bad_lines"] == 2 and c["rows_before_w_e_start"] == 1


def test_count_only_applies_the_same_refusals_and_refuses_a_truncated_hour(we, capsys, tmp_path):
    a, b = (fake_hour(we.tape, k) for k in ("2026-10-19T08", "2026-10-19T09"))
    assert E.main(["--count-only", "--hours", str(a)]) == 2  # not the whole of W_e
    assert "not exactly" in capsys.readouterr().err
    assert E.main(["--count-only", "--hours", str(fake_hour(we.tape, "2026-10-20T08"))]) == 2
    assert E.main(["--count-only", "--hours", str(a), str(b)]) == 2  # the fake files are not zstd: unreadable, refused (not a crash)
    assert "unreadable or truncated" in capsys.readouterr().err
    w = World()
    for i in range(2000):
        w.trade(B + i * 7, POOL["PA"], MINT["PA"], "buy" if i % 2 else "sell")
    (good,) = write_hours(tmp_path / "t", w.rows)
    cut = we.tape / good.name
    cut.write_bytes(good.read_bytes()[: len(good.read_bytes()) // 2])
    with pytest.raises(E.ExploreRefused, match="unreadable or truncated"):
        E.count_rows([E.Hour(HOUR0, cut)])


def test_scratch_is_required_unless_count_only(we):
    with pytest.raises(SystemExit) as e:
        E.main(["--hours", str(fake_hour(we.tape, "2026-10-19T08"))])
    assert e.value.code == 2


def test_the_report_carries_the_two_disclosure_lines(runs):
    rep = runs["rep"]
    assert rep["disclosures"] == ["DEC-031 Am.2 item 6 close-blocking reclassification is not applied in W_e; rent-0 figures are optimistic by that class.",
                                  "W_e's first hour excludes children created before 08:05:20Z; k = 20 is truncated by the 60-min deque."]
    md = (runs["s1"] / "REPORT.md").read_text()
    assert all(x in md for x in rep["disclosures"])
