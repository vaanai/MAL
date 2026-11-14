"""osb_twin_check: first-run identity checks of pricing-only twin books (gap-in-hold C2-C5, latency L2-L5). Synthetic run directories in tmp dirs (hand-built, plus one real follow pair from the
tools/test_osb_follow fixture world); no real tape, ledger, book or host path is opened. Nothing here says any book makes money."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "research"))
sys.path.insert(0, str(REPO / "scripts" / "ops"))
import osb_follow as O  # noqa: E402
import osb_twin_check as T  # noqa: E402
from tools.test_osb_follow import EK, FAST, _env, link, manifest, run_once, world  # noqa: E402,F401  (_env: the autouse env fixture of that module)

HOURS = ["2026-11-05T%02d" % h for h in range(6)]
BASE = {"book_id": "strict-1", "title": "t", "host": "lane", "rule_overrides": {}, "stakes_sol": [0.10, 0.25], "send_lamports": 160000, "start_hour": "2026-11-05T00", "seed_from_hour": "2026-10-20T00",
        "tape_dir": "/x/tape", "out_root": "/x/out"}
ENTRY_MS = 1793844000000  # 2026-11-05T02Z, inside the fixture window
GOOD = {h: ("L" + h, "D" + h) for h in HOURS}


@pytest.fixture(autouse=True)
def _arena_env(monkeypatch):
    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    monkeypatch.setenv(T.TEST_FLAG, "1")
    monkeypatch.delenv("MISCUSI_JOB_ID", raising=False)


def trade(pid, stake, **kw):
    r = {"kind": "trade", "position_id": pid, "stake_sol": stake, "legs": {"total": {"flat_1p3": "f", "press_1p3": "p", "flat_1p9": 1.0}}, "calc": {"1p3": "c"}, "written_ms": 5, "exit_reason": "x", "decision_ms": {"entry": ENTRY_MS}}
    r.update(kw)
    return r


def excl(pid, stake, why):
    return {"kind": "excluded", "position_id": pid, "stake_sol": stake, "exclusion_reason": why, "written_ms": 6, "decision_ms": {"entry": ENTRY_MS}}


def make_run(tmp, name, manifest_dict, rows, hours=HOURS, md5s=None, dmd5="d" * 32, code="code1", last=None, head="h-new", dirty=False):
    out = tmp / name
    led = out / "runs" / "0001" / "ledger" / "botA"
    led.mkdir(parents=True)
    ident = [{"kind": "start", "pid": 77, "engine_id": head + "-eng", "code_head": head, "code_dirty": dirty, "written_ms": 1},
             {"kind": "open", "position_id": "p0", "engine_id": head + "-eng", "code_head": head, "code_dirty": dirty, "written_ms": len(head), "decision_ms": {"entry": ENTRY_MS}}] if head else []
    (led / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in ident + list(rows)))
    (out / "book").symlink_to("runs/0001")
    md5s = md5s or {h: ("L" + h, "D" + h) for h in hours}
    (out / "md5-by-hour.jsonl").write_text("".join(json.dumps({"hour": h, "end_ms": 0, "ledger_md5": md5s[h][0], "decision_md5": md5s[h][1]}) + "\n" for h in hours))
    (out / "status.json").write_text(json.dumps({"last_hour": last or max(hours)}))
    (out / "decisions.md5").write_text(dmd5 + "\n")
    (out / "manifest.sha256").write_text(O.manifest_sha256(manifest_dict) + "\n")
    (out / "code.sha256").write_text(code + "\n")
    mp = tmp / (name + ".manifest.json")
    mp.write_text(json.dumps(manifest_dict))
    return out, mp


def mans(kind):
    s = dict(BASE)
    t = dict(BASE, book_id="twin-1", out_root="/x/outt", twin_of="strict-1")
    if kind == "gap-hold":
        t["gap_exclusion"] = "hold"
    else:
        t["pricing_lat_entry_s"] = 4.085
    return s, t


GAP_STRICT = [trade("p1", 0.1), trade("p1", 0.25), trade("p2", 0.1), trade("p2", 0.25), excl("p3", 0.1, "feed_gap"), excl("p3", 0.25, "feed_gap"), excl("p4", 0.1, "feed_gap"), excl("p4", 0.25, "feed_gap")]
GAP_TWIN = [trade("p1", 0.1), trade("p1", 0.25), trade("p2", 0.1), trade("p2", 0.25), excl("p3", 0.1, "feed_gap_entry"), excl("p3", 0.25, "feed_gap_entry"),
            trade("p4", 0.1, gap_in_hold=True, blind_s=0.8, gap_count=1), trade("p4", 0.25, gap_in_hold=True, blind_s=0.8, gap_count=1)]
LAT_STRICT = [trade("p1", 0.1), trade("p1", 0.25), trade("p2", 0.1), trade("p2", 0.25), excl("p3", 0.1, "feed_gap"), excl("p3", 0.25, "feed_gap")]
LAT_TWIN = [trade("p1", 0.1), trade("p1", 0.25), excl("p2", 0.1, "exit_before_entry_landing"), excl("p2", 0.25, "exit_before_entry_landing"), excl("p3", 0.1, "feed_gap"), excl("p3", 0.25, "feed_gap")]


def setup(tmp, kind, srows=None, trows=None, with_oncopy=True, twin_kw=None, strict_kw=None, oc_kw=None, oc_rows=None):
    sm, tm = mans(kind)
    srows = srows if srows is not None else (GAP_STRICT if kind == "gap-hold" else LAT_STRICT)
    trows = trows if trows is not None else (GAP_TWIN if kind == "gap-hold" else LAT_TWIN)
    s, smp = make_run(tmp, "strict", sm, srows, **dict({"code": "code0", "head": "h-old"}, **(strict_kw or {})))
    t, tmp_ = make_run(tmp, "twin", tm, trows, **(twin_kw or {}))
    args = ["--kind", kind, "--strict-run", str(s), "--twin-run", str(t), "--strict-manifest", str(smp), "--twin-manifest", str(tmp_), "--out", str(tmp / "out.json")]
    if with_oncopy:
        oc, _ = make_run(tmp, "oncopy", sm, srows if oc_rows is None else oc_rows, **dict({"md5s": GOOD}, **(oc_kw or {})))
        args += ["--on-copy-run", str(oc)]
    return args


def sub(tmp, name):
    d = tmp / name
    d.mkdir()
    return d


def run(args, capsys):
    code = T.main(args)
    cap = capsys.readouterr()
    return code, (json.loads(cap.out) if cap.out else None), cap.err


KINDS = ("gap-hold", "latency")
NAMES = {"gap-hold": ("C2r", "C3", "C4", "C5"), "latency": ("L2r", "L3", "L4", "L5")}
OLD = {"gap-hold": "C2", "latency": "L2"}


@pytest.mark.parametrize("kind", KINDS)
def test_correct_twin_passes_every_condition(tmp_path, capsys, kind):
    code, res, err = run(setup(tmp_path, kind), capsys)
    assert code == 0 and res["overall"] == "PASS" and set(res["conditions"]) == set(NAMES[kind]) | {OLD[kind]} and all(c["status"] == "PASS" for k, c in res["conditions"].items() if k != OLD[kind])
    assert res["conditions"][OLD[kind]]["status"] == "NOT_COMPARABLE_ACROSS_COMMITS"  # strict is at another commit: a per-hour ledger_md5 cannot be equal
    c2 = res["conditions"][NAMES[kind][0]]
    assert c2["window"] == {"first_hour": HOURS[0], "last_hour": HOURS[-1], "n_hours": 6} and c2["per_hour_decision_md5"]["n_hours_compared"] == 6 and c2["differing_key_names"] == []
    assert c2["projection_md5"]["on_copy"] == c2["projection_md5"]["strict"] and c2["projection_multiset_md5"]["on_copy"] == c2["projection_multiset_md5"]["strict"] and "open" in c2["row_counts_by_kind"]["strict"]
    assert json.loads((tmp_path / "out.json").read_text()) == res
    txt = json.dumps(res)
    assert "p1" not in txt and "p4" not in txt  # no position ids anywhere


@pytest.mark.parametrize("kind", KINDS)
def test_without_on_copy_the_required_c2_l2_is_not_run_and_the_result_is_not_pass(tmp_path, capsys, kind):
    code, res, _ = run(setup(tmp_path, kind, with_oncopy=False), capsys)
    assert code == 3 and res["overall"] == "NOT_MET" and res["conditions"][NAMES[kind][0]]["status"] == "NOT_RUN"
    assert all(c["status"] == "PASS" for k, c in res["conditions"].items() if k != NAMES[kind][0])


@pytest.mark.parametrize("kind", KINDS)
def test_c2r_fails_on_one_tampered_hour_decision_md5_and_ignores_ledger_md5_across_commits(tmp_path, capsys, kind):
    md5s = dict(GOOD)
    md5s[HOURS[3]] = (GOOD[HOURS[3]][0], "X")
    code, res, _ = run(setup(tmp_path, kind, oc_kw={"md5s": md5s}), capsys)
    c = res["conditions"][NAMES[kind][0]]
    assert code == 3 and c["status"] == "FAIL" and c["per_hour_decision_md5"]["n_mismatching_hours"] == 1 and c["reason"] == "per-hour decision_md5 differs" and res["overall"] == "NOT_MET"
    md5s = dict(GOOD)
    md5s[HOURS[3]] = ("X", GOOD[HOURS[3]][1])  # only a per-hour ledger_md5 differs, across commits: the old condition is informational, C2r PASSes, the overall result stands
    code, res, _ = run(setup(sub(tmp_path, "b"), kind, oc_kw={"md5s": md5s}), capsys)
    assert code == 0 and res["conditions"][NAMES[kind][0]]["status"] == "PASS" and res["conditions"][OLD[kind]]["status"] == "NOT_COMPARABLE_ACROSS_COMMITS" and res["conditions"][OLD[kind]]["n_mismatching_hours"] == 1


@pytest.mark.parametrize("kind", KINDS)
def test_old_c2_is_compared_and_counted_at_the_same_commit(tmp_path, capsys, kind):
    same = {"code": "code1", "head": "h-new"}
    code, res, _ = run(setup(tmp_path, kind, strict_kw=same), capsys)
    assert code == 0 and res["conditions"][OLD[kind]]["status"] == "PASS" and res["conditions"][OLD[kind]]["n_hours_compared"] == 6
    md5s = dict(GOOD)
    md5s[HOURS[3]] = ("X", GOOD[HOURS[3]][1])
    code, res, _ = run(setup(sub(tmp_path, "b"), kind, strict_kw=same, oc_kw={"md5s": md5s}), capsys)
    assert code == 3 and res["conditions"][OLD[kind]]["status"] == "FAIL" and res["conditions"][OLD[kind]]["first_mismatching_hour"] == HOURS[3] and res["conditions"][NAMES[kind][0]]["status"] == "PASS"
    code, res, _ = run(setup(sub(tmp_path, "c"), kind, strict_kw={"code": "code1", "head": "h-old"}), capsys)  # same code.sha256 but another code_head: still another commit
    assert res["conditions"][OLD[kind]]["status"] == "NOT_COMPARABLE_ACROSS_COMMITS"


@pytest.mark.parametrize("kind", KINDS)
def test_c2r_fails_when_one_priced_field_differs_and_prints_key_names_only(tmp_path, capsys, kind):
    base = GAP_STRICT if kind == "gap-hold" else LAT_STRICT
    rows = copy.deepcopy(base)
    rows[0]["exit_reason"] = "SECRET-VALUE"
    code, res, _ = run(setup(tmp_path, kind, srows=base, oc_rows=rows, trows=GAP_TWIN if kind == "gap-hold" else LAT_TWIN), capsys)
    c = res["conditions"][NAMES[kind][0]]
    assert code == 3 and c["status"] == "FAIL" and c["reason"] == "ledger projection differs" and c["differing_key_names"] == ["exit_reason"] and "SECRET-VALUE" not in json.dumps(res)
    assert c["projection_md5"]["on_copy"] != c["projection_md5"]["strict"]


@pytest.mark.parametrize("kind", KINDS)
def test_c2r_order_only_is_a_fail(tmp_path, capsys, kind):
    base = GAP_STRICT if kind == "gap-hold" else LAT_STRICT
    rows = copy.deepcopy(base)
    rows[0], rows[1] = rows[1], rows[0]
    code, res, _ = run(setup(tmp_path, kind, srows=base, oc_rows=rows, trows=GAP_TWIN if kind == "gap-hold" else LAT_TWIN), capsys)
    c = res["conditions"][NAMES[kind][0]]
    assert code == 3 and c["status"] == "FAIL" and c["reason"] == "order only" and c["projection_multiset_md5"]["on_copy"] == c["projection_multiset_md5"]["strict"]


@pytest.mark.parametrize("which", ["on_copy", "strict"])
@pytest.mark.parametrize("dirty", [True, "missing"])
@pytest.mark.parametrize("kind", KINDS)
def test_c2r_code_dirty_true_or_missing_fails(tmp_path, capsys, kind, dirty, which):
    kw = {"dirty": dirty is True}
    a = setup(tmp_path, kind, **({"oc_kw": kw} if which == "on_copy" else {"strict_kw": kw}))
    if dirty == "missing":
        f = tmp_path / ("oncopy" if which == "on_copy" else "strict") / "runs" / "0001" / "ledger" / "botA" / "rows.jsonl"
        f.write_text("".join(json.dumps({k: v for k, v in json.loads(ln).items() if k != "code_dirty"}) + "\n" for ln in f.read_text().splitlines()))
    code, res, _ = run(a, capsys)
    c = res["conditions"][NAMES[kind][0]]
    assert code == 3 and c["status"] == "FAIL" and "code_dirty" in c["reason"] and c["code_dirty"][which]["all_false"] is False


@pytest.mark.parametrize("kind", KINDS)
def test_c2r_compares_every_row_kind(tmp_path, capsys, kind):
    base = (GAP_STRICT if kind == "gap-hold" else LAT_STRICT) + [{"kind": "late_print", "position_id": "p1", "written_ms": 3, "n": 1}]
    code, res, _ = run(setup(tmp_path, kind, srows=base, trows=GAP_TWIN if kind == "gap-hold" else LAT_TWIN), capsys)
    c = res["conditions"][NAMES[kind][0]]
    assert code == 0 and c["row_counts_by_kind"]["strict"]["late_print"] == 1 and c["row_counts_by_kind"]["on_copy"]["late_print"] == 1 and "start" not in c["row_counts_by_kind"]["strict"]
    rows = copy.deepcopy(base)
    rows[-1]["n"] = 2  # a difference in an otherwise uncompared kind
    code, res, _ = run(setup(sub(tmp_path, "b"), kind, srows=base, oc_rows=rows, trows=GAP_TWIN if kind == "gap-hold" else LAT_TWIN), capsys)
    assert code == 3 and res["conditions"][NAMES[kind][0]]["status"] == "FAIL" and res["conditions"][NAMES[kind][0]]["differing_key_names"] == ["n"]


@pytest.mark.parametrize("kind", KINDS)
def test_decisions_per_hour_mismatch_fails_c3_l3(tmp_path, capsys, kind):
    md5s = dict(GOOD)
    md5s[HOURS[1]] = ("L" + HOURS[1], "other")
    code, res, _ = run(setup(tmp_path, kind, twin_kw={"md5s": md5s}), capsys)
    c = res["conditions"][NAMES[kind][1]]
    assert code == 3 and c["status"] == "FAIL" and c["per_hour_decision_md5"]["n_mismatching_hours"] == 1 and c["decisions_md5"]["status"] == "PASS"


@pytest.mark.parametrize("kind", KINDS)
def test_decisions_md5_mismatch_at_the_same_last_hour_fails(tmp_path, capsys, kind):
    code, res, _ = run(setup(tmp_path, kind, twin_kw={"dmd5": "e" * 32}), capsys)
    c = res["conditions"][NAMES[kind][1]]
    assert code == 3 and c["status"] == "FAIL" and c["decisions_md5"]["status"] == "FAIL" and c["per_hour_decision_md5"]["status"] == "PASS"
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("strict_longer", [True, False])
def test_unequal_windows_are_refused_not_failed_in_both_directions(tmp_path, capsys, kind, strict_longer):
    more = HOURS + ["2026-11-05T06", "2026-11-05T07"]
    md5s = dict(GOOD, **{h: ("L" + h, "D" + h) for h in more[6:]})
    kw = {"hours": more, "md5s": md5s}
    code, res, err = run(setup(tmp_path, kind, with_oncopy=False, **({"strict_kw": kw} if strict_longer else {"twin_kw": kw})), capsys)
    assert code == 2 and res is None and "row conditions need identical windows" in err and "2026-11-05T00..2026-11-05T05 (6 h)" in err and "2026-11-05T00..2026-11-05T07 (8 h)" in err
    assert not (tmp_path / "out.json").exists()


def test_pending_when_last_hours_differ_is_never_a_comparison():
    """C3/L3 on its own (the CLI refuses unequal windows first): different last hours -> PENDING, the decisions md5 not compared"""
    mk = lambda last, d: {"hours": {h: ("L", "D") for h in HOURS}, "last": last, "decisions_md5": d}  # noqa: E731
    c = T.cond_decisions("C3", mk("2026-11-05T05", "a" * 32), mk("2026-11-05T07", "b" * 32))
    assert c["status"] == "PENDING" and c["decisions_md5"]["status"] == "PENDING" and c["per_hour_decision_md5"]["status"] == "PASS"




@pytest.mark.parametrize("kind", KINDS)
def test_derived_proof_legs_differing_in_tape_dir_and_out_root_pass(tmp_path, capsys, kind):
    sm, tm = mans(kind)
    sm.update(tape_dir="/data2/mal/scratch/floor-open-sim/proof-x/M1/tape", out_root="/data2/mal/scratch/floor-open-sim/proof-x/M1/out")
    tm.update(tape_dir="/data2/mal/scratch/floor-open-sim/proof-x/M2/tape", out_root="/data2/mal/scratch/floor-open-sim/proof-x/M2/out")
    s, smp = make_run(tmp_path, "strict", sm, GAP_STRICT if kind == "gap-hold" else LAT_STRICT)
    t, tmp_ = make_run(tmp_path, "twin", tm, GAP_TWIN if kind == "gap-hold" else LAT_TWIN)
    oc, _ = make_run(tmp_path, "oncopy", sm, GAP_STRICT if kind == "gap-hold" else LAT_STRICT, md5s=GOOD)
    a = ["--kind", kind, "--strict-run", str(s), "--twin-run", str(t), "--strict-manifest", str(smp), "--twin-manifest", str(tmp_), "--out", str(tmp_path / "o.json"), "--on-copy-run", str(oc)]
    code, res, err = run(a, capsys)
    assert code == 0 and res["tape_dir_differs"] is True and res["overall"] == "PASS", err


@pytest.mark.parametrize("kind", KINDS)
def test_a_third_differing_key_is_refused_with_tape_dir_free(tmp_path, capsys, kind):
    sm, tm = mans(kind)
    sm.update(tape_dir="/data2/mal/scratch/floor-open-sim/proof-x/M1/tape")
    tm.update(tape_dir="/data2/mal/scratch/floor-open-sim/proof-x/M2/tape", send_lamports=1)
    s, smp = make_run(tmp_path, "strict", sm, GAP_STRICT)
    t, tmp_ = make_run(tmp_path, "twin", tm, GAP_TWIN)
    a = ["--kind", kind, "--strict-run", str(s), "--twin-run", str(t), "--strict-manifest", str(smp), "--twin-manifest", str(tmp_), "--out", str(tmp_path / "o.json")]
    code, res, err = run(a, capsys)
    assert code == 2 and "send_lamports" in err


def test_differing_tape_dir_outside_the_scratch_root_is_refused(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(T.TEST_FLAG)  # MAL_ARENA_TEST alone does not lift the scratch-root guard
    sm, tm = mans("latency")
    tm.update(tape_dir="/elsewhere/tape")
    s, smp = make_run(tmp_path, "strict", sm, LAT_STRICT)
    t, tmp_ = make_run(tmp_path, "twin", tm, LAT_TWIN)
    a = ["--kind", "latency", "--strict-run", str(s), "--twin-run", str(t), "--strict-manifest", str(smp), "--twin-manifest", str(tmp_), "--out", str(tmp_path / "o.json")]
    code, res, err = run(a, capsys)
    assert code == 2 and "not under /data2/mal/scratch/floor-open-sim" in err
    monkeypatch.setenv(T.TEST_FLAG, "1")
    monkeypatch.setenv("MISCUSI_JOB_ID", "1")  # the test flag is refused inside a MiScusi job
    (tmp_path / "o.json").unlink(missing_ok=True)
    code, res, err = run(a, capsys)
    assert code == 2 and "refuses to run inside a MiScusi job" in err


def test_no_row_in_common_is_no_data_not_fail_and_not_pass(tmp_path, capsys):
    code, res, _ = run(setup(tmp_path, "latency", srows=[], trows=[]), capsys)
    assert code == 3 and res["overall"] == "NOT_MET" and res["conditions"]["L4"]["status"] == "NO_DATA" and res["conditions"]["L5"]["status"] == "NO_DATA" and "never a waiver" in res["remedy_for_NO_DATA"]
    code, res, _ = run(setup(sub(tmp_path, "g"), "gap-hold", srows=[], trows=[]), capsys)
    assert code == 3 and {res["conditions"][c]["status"] for c in ("C4", "C5")} == {"NO_DATA"}

def test_c4_fails_on_one_tampered_unflagged_row_and_ignores_written_ms(tmp_path, capsys):
    rows = copy.deepcopy(GAP_TWIN)
    rows[0]["written_ms"] = 999  # removed from the canonical line: still PASS
    code, res, _ = run(setup(tmp_path, "gap-hold", trows=rows), capsys)
    assert code == 0 and res["conditions"]["C4"]["n_twin_unflagged_rows"] == 4 and res["conditions"]["C4"]["n_twin_flagged_rows"] == 2
    rows[2]["exit_reason"] = "other"
    code, res, _ = run(setup(sub(tmp_path, "b"), "gap-hold", trows=rows), capsys)
    c4 = res["conditions"]["C4"]
    assert code == 3 and c4["status"] == "FAIL" and c4["twin_unflagged_trade_rows_md5"] != c4["strict_trade_rows_md5"] and res["conditions"]["C5"]["status"] == "PASS"  # only C4 broke


def test_c4_flagged_rows_may_differ_from_strict_and_a_missing_twin_row_fails(tmp_path, capsys):
    rows = copy.deepcopy(GAP_TWIN)
    rows[6]["legs"]["total"]["flat_1p3"] = "different"  # a flagged row: outside C4 (strict never priced it)
    assert run(setup(tmp_path, "gap-hold", trows=rows), capsys)[0] == 0
    rows = [r for r in GAP_TWIN if not (r["kind"] == "trade" and r["position_id"] == "p2" and r["stake_sol"] == 0.25)]
    code, res, _ = run(setup(sub(tmp_path, "b"), "gap-hold", trows=rows), capsys)
    assert code == 3 and res["conditions"]["C4"]["status"] == "FAIL"


def test_c5_fails_when_one_exclusion_is_unaccounted(tmp_path, capsys):
    rows = [r for r in GAP_TWIN if not (r["kind"] == "excluded" and r["stake_sol"] == 0.25)]
    code, res, _ = run(setup(tmp_path, "gap-hold", trows=rows), capsys)
    c5 = res["conditions"]["C5"]
    assert code == 3 and c5["status"] == "FAIL" and c5["per_stake"]["0.25"] == {"strict_feed_gap": 2, "twin_feed_gap_entry": 0, "twin_feed_gap_exit": 0, "twin_gap_in_hold": 1, "equal": False}
    assert c5["per_stake"]["0.1"]["equal"] is True


def test_c5_counts_the_exit_reason(tmp_path, capsys):
    rows = [r for r in GAP_TWIN if r["position_id"] != "p4"] + [excl("p4", 0.1, "feed_gap_exit"), excl("p4", 0.25, "feed_gap_exit")]
    code, res, _ = run(setup(tmp_path, "gap-hold", trows=rows), capsys)
    assert code == 0 and res["conditions"]["C5"]["per_stake"]["0.1"]["twin_feed_gap_exit"] == 1 and res["conditions"]["C4"]["status"] == "PASS"
    rows = [r for r in GAP_TWIN if r["position_id"] != "p4"] + [excl("p4", 0.1, "feed_gap_exit"), excl("p4", 0.25, "missing_v")]  # one exclusion under another reason: C5 breaks
    code, res, _ = run(setup(sub(tmp_path, "b"), "gap-hold", trows=rows), capsys)
    assert code == 3 and res["conditions"]["C5"]["status"] == "FAIL"


def test_l4_passes_with_1p9_differences_and_fails_on_each_tampered_field(tmp_path, capsys):
    rows = copy.deepcopy(LAT_TWIN)
    rows[0]["legs"]["total"]["flat_1p9"] = 123.0  # the 1.9 leg may differ: not part of L4
    rows[0]["written_ms"] = 77
    code, res, _ = run(setup(tmp_path, "latency", trows=rows), capsys)
    l4 = res["conditions"]["L4"]
    assert code == 0 and l4["n_rows_priced_in_both"] == 2 and l4["n_rows_priced_only_in_strict"] == 2 and l4["n_rows_priced_only_in_twin"] == 0 and l4["twin_md5"] == l4["strict_md5"]
    for i, (path, v) in enumerate(((("legs", "total", "press_1p3"), "P"), (("legs", "total", "flat_1p3"), "F"), (("calc", "1p3"), "C"))):
        rows = copy.deepcopy(LAT_TWIN)
        t = rows[1]
        for k in path[:-1]:
            t = t[k]
        t[path[-1]] = v
        code, res, _ = run(setup(sub(tmp_path, "x%d" % i), "latency", trows=rows), capsys)
        assert code == 3 and res["conditions"]["L4"]["status"] == "FAIL", path
    rows = copy.deepcopy(LAT_TWIN)
    rows[1]["stake_sol"] = 0.3  # a row that no longer matches by (position, stake): leaves L4's common set, and L5's per-stake conservation breaks
    code, res, _ = run(setup(sub(tmp_path, "xs"), "latency", trows=rows), capsys)
    assert code == 3 and res["conditions"]["L4"]["n_rows_priced_only_in_strict"] == 3 and res["conditions"]["L5"]["status"] == "FAIL"


def test_l5_counts_by_reason_and_fails_when_a_row_is_lost(tmp_path, capsys):
    code, res, _ = run(setup(tmp_path, "latency"), capsys)
    l5 = res["conditions"]["L5"]
    assert code == 0 and l5["twin"]["0.1"] == {"priced": 1, "excluded": 2, "total": 3, "excluded_by_reason": {"exit_before_entry_landing": 1, "feed_gap": 1}}
    assert l5["strict"]["0.1"]["priced"] == 2 and l5["strict"]["0.1"]["total"] == 3 and l5["n_positions_closed_in_both"] == 3
    rows = [r for r in LAT_TWIN if not (r["kind"] == "excluded" and r["position_id"] == "p2" and r["stake_sol"] == 0.25)]
    code, res, _ = run(setup(sub(tmp_path, "b"), "latency", trows=rows), capsys)
    assert code == 3 and res["conditions"]["L5"]["status"] == "FAIL"


def test_l5_only_counts_position_ids_closed_in_both(tmp_path, capsys):
    rows = LAT_TWIN + [trade("p9", 0.1), trade("p9", 0.25)]  # a position closed only in the twin (strict still open): reported, not counted
    code, res, _ = run(setup(tmp_path, "latency", trows=rows), capsys)
    assert code == 0 and res["conditions"]["L5"]["n_positions_closed_only_in_twin"] == 1


# ------------------------------------------------------------------------------------------------------------------------------ refusals
def refused(args, capsys, frag=None):
    code, res, err = run(args, capsys)
    assert code == 2 and res is None and "refused" in err and (frag is None or frag in err), err
    assert not Path(args[args.index("--out") + 1]).exists()


@pytest.mark.parametrize("kind", KINDS)
def test_refusal_missing_files_and_dirs(tmp_path, capsys, kind):
    a = setup(tmp_path, kind)
    (tmp_path / "twin" / "md5-by-hour.jsonl").unlink()
    refused(a, capsys, "unreadable")
    a = setup(sub(tmp_path, "n"), kind)
    i = a.index("--strict-manifest")
    refused(a[:i + 1] + [str(tmp_path / "nope.json")] + a[i + 2:], capsys)
    refused(["--kind", kind, "--strict-run", str(tmp_path / "zz"), "--twin-run", str(tmp_path / "yy"), "--strict-manifest", str(tmp_path / "a"), "--twin-manifest", str(tmp_path / "b"),
             "--out", str(tmp_path / "o")], capsys, "not a directory")


@pytest.mark.parametrize("kind", KINDS)
def test_refusal_different_windows_and_gaps_in_hours(tmp_path, capsys, kind):
    refused(setup(tmp_path, kind, with_oncopy=False, twin_kw={"hours": [h for h in HOURS if h != HOURS[2]]}), capsys, "identical windows")  # a missing hour inside the window
    new = ["2026-11-09T00", "2026-11-09T01"]
    refused(setup(sub(tmp_path, "d"), kind, with_oncopy=False, twin_kw={"hours": new, "md5s": {h: ("l", "d") for h in new}}), capsys, "identical windows")
    refused(setup(sub(tmp_path, "e"), kind, oc_kw={"hours": HOURS[:4]}), capsys, "same hours")  # on-copy over another window than the twin


@pytest.mark.parametrize("kind", KINDS)
def test_refusal_inconsistent_status_torn_line_and_duplicate_hour(tmp_path, capsys, kind):
    refused(setup(tmp_path, kind, twin_kw={"last": HOURS[3]}), capsys, "last_hour")
    d = sub(tmp_path, "d")
    a = setup(d, kind)
    p = d / "twin" / "md5-by-hour.jsonl"
    p.write_text(p.read_text() + '{"hour": "2026')
    refused(a, capsys, "torn last line")
    e = sub(tmp_path, "e")
    a = setup(e, kind)
    p = e / "twin" / "md5-by-hour.jsonl"
    p.write_text(p.read_text() + p.read_text().splitlines()[0] + "\n")
    refused(a, capsys, "duplicate")


def test_refusal_on_copy_at_another_commit(tmp_path, capsys):
    refused(setup(tmp_path, "latency", oc_kw={"code": "other"}), capsys, "code.sha256")


@pytest.mark.parametrize("kind,mut", [("latency", {"stakes_sol": [0.1]}), ("latency", {"send_lamports": 1}), ("latency", {"gap_exclusion": "hold"}), ("latency", {"twin_of": "somebody-else"}),
                                      ("gap-hold", {"stakes_sol": [0.1]}), ("gap-hold", {"gap_exclusion": "on"}), ("gap-hold", {"pricing_lat_entry_s": 4.085}), ("gap-hold", {"twin_of": "somebody-else"})])
def test_refusal_manifest_not_a_twin(tmp_path, capsys, kind, mut):
    sm, tm = mans(kind)
    tm.update(mut)
    s, smp = make_run(tmp_path, "strict", sm, GAP_STRICT)
    t, tmp_ = make_run(tmp_path, "twin", tm, GAP_TWIN)
    a = ["--kind", kind, "--strict-run", str(s), "--twin-run", str(t), "--strict-manifest", str(smp), "--twin-manifest", str(tmp_), "--out", str(tmp_path / "o.json")]
    code, res, err = run(a, capsys)
    assert code == 2 and "refused" in err and res is None


def test_refusal_kind_mismatch(tmp_path, capsys):
    sm, tm = mans("gap-hold")  # a gap twin handed to --kind latency
    s, smp = make_run(tmp_path, "strict", sm, GAP_STRICT)
    t, tmp_ = make_run(tmp_path, "twin", tm, GAP_TWIN)
    a = ["--kind", "latency", "--strict-run", str(s), "--twin-run", str(t), "--strict-manifest", str(smp), "--twin-manifest", str(tmp_), "--out", str(tmp_path / "o.json")]
    code, _, err = run(a, capsys)
    assert code == 2 and "refused" in err


def test_refusal_manifest_file_not_the_runs_manifest(tmp_path, capsys):
    a = setup(tmp_path, "latency")
    m = json.loads((tmp_path / "twin.manifest.json").read_text())
    m["title"] = "changed"
    (tmp_path / "twin.manifest.json").write_text(json.dumps(m))
    refused(a, capsys, "manifest.sha256")


def test_refusal_out_exists_and_nothing_is_overwritten(tmp_path, capsys):
    a = setup(tmp_path, "latency")
    (tmp_path / "out.json").write_text("keep")
    code, res, err = run(a, capsys)
    assert code == 2 and "O_EXCL" in err and (tmp_path / "out.json").read_text() == "keep"


def test_read_only_the_run_dirs_are_unchanged(tmp_path, capsys):
    a = setup(tmp_path, "gap-hold")

    def snap():
        return sorted((str(p.relative_to(tmp_path)), p.stat().st_mtime_ns, p.stat().st_size) for p in tmp_path.rglob("*") if p.is_file() and p.name != "out.json")
    before = snap()
    assert run(a, capsys)[0] == 0
    assert snap() == before


# ------------------------------------------------------------------------------------------------------------------------------ real driver layout, and the CLI under python -O
def test_real_follow_pair_layout_is_read(world, capsys):
    tmp, src, names = world
    link(src, tmp / "tape", names, 48)
    mp_s, m_s = manifest(tmp, book_id="strict-1", rule_overrides=FAST, out_root=str(tmp / "out_s"))
    mp_t, m_t = manifest(tmp, book_id="twin-1", rule_overrides=FAST, out_root=str(tmp / "out_t"), twin_of="strict-1", pricing_lat_entry_s=4.085)
    mp_c, _ = manifest(tmp, book_id="strict-copy", rule_overrides=FAST, out_root=str(tmp / "out_c"))
    assert run_once(tmp, mp_s) == 0 and O.follow(str(mp_t), once=True, _roots=(str(tmp),), _table_mode="first_print", _engine_kw=EK, strict_manifest=str(mp_s)) == 0 and run_once(tmp, mp_c) == 0
    capsys.readouterr()  # the driver prints its seed lines
    code, res, err = run(["--kind", "latency", "--strict-run", str(tmp / "out_s"), "--twin-run", str(tmp / "out_t"), "--on-copy-run", str(tmp / "out_c"), "--strict-manifest", str(mp_s),
                          "--twin-manifest", str(mp_t), "--out", str(tmp / "check.json")], capsys)
    assert err == "", err
    cs = res["conditions"]
    assert cs["L2"]["status"] == "PASS" and cs["L2"]["n_hours_compared"] > 0 and cs["L2r"]["per_hour_decision_md5"]["n_hours_compared"] > 0 and cs["L2r"]["projection_md5"]["on_copy"] == cs["L2r"]["projection_md5"]["strict"] and cs["L3"]["status"] == "PASS" and cs["L3"]["per_hour_decision_md5"]["n_hours_compared"] > 0, res
    # the fixture world closes no position inside its window: nothing to compare is NO_DATA, never a PASS
    assert cs["L4"]["n_rows_priced_in_both"] == 0 and cs["L4"]["status"] == "NO_DATA" and cs["L5"]["n_positions_closed_in_both"] == 0 and cs["L5"]["status"] == "NO_DATA" and code == 3


def test_cli_under_python_dash_O_refuses_the_same_way(tmp_path):
    r = subprocess.run([sys.executable, "-O", str(REPO / "scripts" / "ops" / "osb_twin_check.py"), "--kind", "latency", "--strict-run", str(tmp_path / "a"), "--twin-run", str(tmp_path / "b"),
                        "--strict-manifest", str(tmp_path / "c"), "--twin-manifest", str(tmp_path / "d"), "--out", str(tmp_path / "o.json")], capture_output=True, text=True,
                       env={"MAL_ARENA_TEST": "1", "PATH": "/usr/bin"}, cwd=str(REPO))
    assert r.returncode == 2 and "refused" in r.stderr and not (tmp_path / "o.json").exists()


# ------------------------------------------------------------------------------------------------------------------------------ reviewer round: half-written runs, window guard
def test_fixture_entry_ms_is_inside_the_window():
    assert O.hour_key(ENTRY_MS) in HOURS


def test_c4_with_no_unflagged_twin_row_is_no_data(tmp_path, capsys):
    rows = [trade("p4", 0.1, gap_in_hold=True, blind_s=0.8, gap_count=1), excl("p3", 0.1, "feed_gap_entry")]
    code, res, _ = run(setup(tmp_path, "gap-hold", trows=rows), capsys)
    c4 = res["conditions"]["C4"]
    assert code == 3 and c4["status"] == "NO_DATA" and c4["n_twin_unflagged_rows"] == 0


@pytest.mark.parametrize("who", ["twin", "strict", "oncopy"])
def test_a_held_lock_is_refused(tmp_path, capsys, who):
    import fcntl
    a = setup(tmp_path, "latency")
    fh = open(tmp_path / who / ".osb.lock", "a")
    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        refused(a, capsys, "still being written")
    finally:
        fh.close()
    assert run(a, capsys)[0] == 0  # the lock file left behind with no holder: fine


@pytest.mark.parametrize("who", ["twin", "strict"])
def test_a_torn_ledger_line_and_an_unparseable_line_are_refused(tmp_path, capsys, who):
    a = setup(tmp_path, "gap-hold")
    f = tmp_path / who / "runs" / "0001" / "ledger" / "botA" / "rows.jsonl"
    good = f.read_text()
    f.write_text(good + '{"kind": "trade", "position_id": "p')
    refused(a, capsys, "torn last line")
    f.write_text("not json\n" + good)
    refused(a, capsys, "unparseable")


def test_a_torn_md5_by_hour_line_is_refused_as_torn(tmp_path, capsys):
    a = setup(tmp_path, "latency")
    p = tmp_path / "twin" / "md5-by-hour.jsonl"
    p.write_text(p.read_text().rstrip("\n"))
    refused(a, capsys, "torn last line")


def test_rows_outside_the_window_are_not_read_and_a_row_without_entry_ms_is_refused(tmp_path, capsys):
    late = ENTRY_MS + 3_600_000 * 40
    rows = LAT_TWIN + [dict(trade("p7", 0.1), decision_ms={"entry": late}), dict(trade("p7", 0.25), decision_ms={"entry": late})]
    code, res, _ = run(setup(tmp_path, "latency", trows=rows), capsys)
    assert code == 0 and res["n_rows_outside_window_not_read"] == {"twin": 2, "strict": 0} and res["conditions"]["L5"]["n_positions_closed_only_in_twin"] == 0
    bad = copy.deepcopy(LAT_TWIN)
    del bad[0]["decision_ms"]
    refused(setup(sub(tmp_path, "b"), "latency", trows=bad), capsys, "entry decision_ms")


def test_must_cover_window(tmp_path, capsys):
    a = setup(tmp_path, "latency")
    code, res, _ = run(a + ["--must-cover-from", "2026-11-05T01", "--must-cover-hours", "4"], capsys)
    assert code == 0 and res["compared_window"] == {"first_hour": HOURS[0], "last_hour": HOURS[-1], "n_hours": 6}
    for extra in (["--must-cover-from", "2026-11-05T03", "--must-cover-hours", "6"], ["--must-cover-from", "bad", "--must-cover-hours", "6"], ["--must-cover-from", "2026-11-05T03"]):
        (tmp_path / "out.json").unlink(missing_ok=True)
        code, res, err = run(a + extra, capsys)
        assert code == 2 and res is None and "refused" in err


def test_a_symlinked_plain_file_is_refused(tmp_path, capsys):
    a = setup(tmp_path, "latency")
    p = tmp_path / "twin" / "status.json"
    real = tmp_path / "status.real"
    real.write_text(p.read_text())
    p.unlink()
    p.symlink_to(real)
    refused(a, capsys, "unreadable")
