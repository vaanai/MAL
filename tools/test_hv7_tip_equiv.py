"""Tests for tools/hv7_tip_equiv.py (the HV7 F1 proof legs on the tip source). SYNTHETIC walker rows only (built here from the runner tests' tape), a fake tip dir, the pinned Writer in process,
StubModel, no API, no network, no sealed path, no exploration file. Nothing here says HV7 makes money."""
from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path

import pytest

import tools.haiku_caller as hcall
from tools import hv4_model as m4
from tools import hv7_runner as R
from tools import hv7_tip_equiv as E
from tools import test_arena_tip_adapter as TA
from tools.test_hv4_runner import MIN, T0
from tools.test_hv7_runner import HOUR, POOLS, T_E1, tape_rows

pytestmark = pytest.mark.skipif(shutil.which("zstd") is None, reason="the zstd CLI is required (the writer's closing)")

FIRST_HOUR = "2026-09-25T04"                       # the hour of the first trade of the synthetic tape (T0 - 70 min)
LAST_HOUR = "2026-09-25T09"
START, END = T0 + HOUR, T_E1 + 5 * MIN             # one screen at 07:02, entries at 07:02:45
SHA = "abcdef1234567"
PRE_U2 = "4c374db3^"                                  # main before #1170: the last runner without U2 (EQUIV_RC reference in the fixture tests; production uses c8fcf761)


@pytest.fixture(autouse=True)
def _env(tmp_path_factory, monkeypatch):
    base = str(tmp_path_factory.getbasetemp())
    monkeypatch.setattr(R, "READ_ROOTS", (base,))
    monkeypatch.setattr(R, "WRITE_ROOTS", (base,))
    monkeypatch.setenv("MAL_ARENA_TEST", "1")

    def no_net(*a, **k):
        raise AssertionError("network used in a test")

    monkeypatch.setattr(socket.socket, "connect", no_net)

    def no_api(*a, **k):
        raise AssertionError("HaikuCaller used in a test")

    monkeypatch.setattr(hcall.HaikuCaller, "__init__", no_api)
    monkeypatch.setattr(m4, "HaikuCaller", hcall.HaikuCaller)


def walker_stream(drop_migration: set | None = None) -> list[tuple[str, dict]]:
    """The runner tests' four-pool tape in walker form: a migration per pool in the first history hour, then PumpSwap trades with tx_index (distinct within a slot)."""
    seq, per_slot = [], {}
    first_trade = min(r["t_recv_ms"] for r in tape_rows() if r["type"] == "trade")
    for r in tape_rows():
        if r["type"] == "pool_create":
            if drop_migration and r["pool"] in drop_migration:
                continue
            t = first_trade - 10 * MIN                            # inside the first history hour, before the first trade, 2h22m before the first screen
            seq.append(("migration", {"type": "migration", "mint": r["mint"], "pool": r["pool"], "quote_mint": TA.WSOL, "slot": 399_000_000 + len(seq), "signature": r["signature"],
                                      "event_index": 0, "tx_index": 0, "block_time": t // 1000, "trader": "u"}))
            continue
        tx = per_slot.get(r["slot"], 0)
        per_slot[r["slot"]] = tx + 1
        seq.append(("trade", TA.wt(r["slot"], 0, venue="pumpswap", t=r["t_recv_ms"], tx=tx, pool=r["pool"], mint=r["mint"], trader=r["trader"], side=r["side"],
                                   sol_lamports=r["sol_lamports"], token_raw=r["token_raw"], quote_reserve=r["quote_reserve"], base_reserve=r["base_reserve"], signature=r["signature"],
                                   block_time=r["t_recv_ms"] // 1000, event_ts=r["event_ts"], virtual_quote_reserves=None)))
    seq.sort(key=lambda kw: (int(kw[1]["slot"]), 0 if kw[0] == "migration" else 1))
    return seq


# ------------------------------------------------------------------------------------------------------------------------ refusals
def test_scratch_is_a_new_dir_under_the_scratch_parent_or_the_test_tmp_and_never_a_sealed_or_live_path(tmp_path):
    assert E.check_scratch(tmp_path / "new") == tmp_path / "new"
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "x").write_text("1")
    for bad, why in ((tmp_path / "full", "scratch_not_empty"), ("relative/x", "scratch_not_absolute"), ("/data2/mal/arena-tip-feed/hv7/x", "arena-tip-feed"),
                     ("/home/claude/mal-data/arena-tipfeed/x", "arena-tipfeed"), ("/var/lib/mal/sealed/fast-trades-tip/x", "sealed"), ("/data/mal/tip-tape-archive/x", "tip-tape"),
                     ("/data/mal/arena/tape/x", "/data/mal/arena"), ("/home/claude/mal-data/arena-mirror/tape/x", "mirror"), ("/data/mal/hunt-1008/elsewhere", "scratch_not_allowed")):
        with pytest.raises(E.Refused, match=why):
            E.check_scratch(bad)
    assert E.check_scratch(E.SCRATCH_PARENT / "run1") == E.SCRATCH_PARENT / "run1"


def test_the_tape_read_by_the_legs_must_be_the_one_built_under_the_scratch_tree(tmp_path):
    assert E.check_tape(tmp_path / "derived", tmp_path) == (tmp_path / "derived").resolve()
    for bad in ("/data2/mal/arena-tip-feed/hv7", "/var/lib/mal/sealed/fast-trades-tip", "/data/mal/arena/tape", str(tmp_path.parent / "other")):
        with pytest.raises(E.Refused):
            E.check_tape(bad, tmp_path)


def test_the_command_line_refuses_without_a_pinned_floor_and_on_a_dirty_tree_or_bad_hours(tmp_path, capsys):
    assert E.main(["--out", str(tmp_path / "o")]) == 2 and json.loads(capsys.readouterr().err)["why"] == "min_periods_required"
    assert E.main(["--out", str(tmp_path / "o"), "--min-periods", "3", "--hours", "2026-10-02T10..2026-10-02T12"]) == 2           # D2/D3 hours are outside the exploration block
    assert "exploration block" in json.loads(capsys.readouterr().err)["why"]
    assert E.main(["--out", "/data2/mal/arena-tip-feed/hv7/x", "--min-periods", "3"]) == 2


def test_v_is_stamped_only_on_pumpswap_trades_that_carry_none():
    seq = [("trade", {"venue": "pumpswap", "virtual_quote_reserves": None}), ("trade", {"venue": "pumpswap", "virtual_quote_reserves": 5}), ("trade", {"venue": "pump_bonding"}),
           ("migration", {"pool": "p"})]
    out = [r for _k, r in E.with_v(seq)]
    assert out[0]["virtual_quote_reserves"] == E.V_LAMPORTS and out[1]["virtual_quote_reserves"] == 5 and "virtual_quote_reserves" not in out[2] and out[3] == {"pool": "p"}


# ------------------------------------------------------------------------------------------------------------------------ the legs
def built(tmp_path, seq=None):
    scratch = tmp_path / "s"
    scratch.mkdir()
    info = E.build_derived(E.with_v(seq if seq is not None else walker_stream()), scratch, FIRST_HOUR)
    return scratch, info


def test_build_derived_runs_the_pinned_writer_and_writes_the_offline_reference(tmp_path):
    scratch, info = built(tmp_path)
    assert info["derived_hour_files"] >= 3 and info["offline_hour_files"] >= 3 and info["offline_rows"] > 1000
    assert info["derived_zst_files"] >= 3                                                # QP E1: closed hours are compacted as in production, and the legs read that path
    assert any(p.name.endswith(".jsonl.zst") for p in (scratch / "derived").iterdir()) and not any(p.name.endswith(".jsonl.zst") for p in (scratch / "offline").iterdir())
    assert (scratch / "derived" / aw_meta()).is_dir()                                      # the writer's own state dir sits beside the tape files, as on the receiver copy
    assert {p.name for p in (scratch / "derived").iterdir() if p.name.startswith("tape-")}
    assert info["walker_rows_no_tx_index"] == 0                                           # the synthetic stream has tx_index everywhere (the shared-slot cases are T1 to T3)


def aw_meta() -> str:
    from tools import arena_tip_tape_writer as aw

    return aw.META_DIR


def test_equiv_1d_writer_hour_files_and_offline_rows_give_the_same_hv7_decisions(tmp_path):
    scratch, _ = built(tmp_path)
    rep = E.leg_equiv_1d(scratch, ["2026-09-25"], START, END, SHA, min_periods=1)
    assert rep["verdict"] == "PASS" and all(rep["equal"].values()), rep
    assert rep["periods_ge3"] >= 1 and rep["log_md5"]["writer"] == rep["log_md5"]["offline"] and rep["u2_age_unknown"] == 0
    flat = json.dumps(rep)
    for pool in POOLS.values():
        assert pool not in flat


def test_equiv_1d_is_not_a_pass_below_the_pinned_floor_or_when_the_runs_differ(tmp_path):
    scratch, _ = built(tmp_path)
    assert E.leg_equiv_1d(scratch, ["2026-09-25"], START, END, SHA, min_periods=10_000)["verdict"] == "FAIL"           # the floor: an empty-ish run is not a PASS
    # mutation: change one row of the offline reference; the md5 equality must break (the leg can see a difference)
    for h in ("06", "07"):                                    # the hours the 07:02 screen reads: drop every other line of the offline reference
        f = scratch / "offline" / f"tape-2026-09-25T{h}.jsonl"
        f.write_bytes(b"".join(f.read_bytes().splitlines(keepends=True)[::2]))
    rep = E.leg_equiv_1d(scratch, ["2026-09-25"], START, END, SHA + "x", min_periods=1)
    assert rep["verdict"] == "FAIL" and not all(rep["equal"].values())


def test_e1_a_runner_killed_between_t_d_and_t_e_and_resumed_gives_the_uninterrupted_md5(tmp_path):
    scratch, _ = built(tmp_path)
    rep = E.leg_e1(scratch, ["2026-09-25"], START, END, SHA, kill_at=T_E1, min_periods=1, min_side=0)
    assert rep["verdict"] == "PASS" and rep["log_md5"]["whole"] == rep["log_md5"]["resumed"] and rep["periods_ge3"] >= 1 and rep["floor_ok"]
    assert rep["periods_ge3_before_cut"] + rep["periods_ge3_after_cut"] == rep["periods_ge3"]


def test_e1_enforces_the_whole_run_floor_and_the_per_side_floor(tmp_path):
    scratch, _ = built(tmp_path)
    one_side = E.leg_e1(scratch, ["2026-09-25"], START, END, SHA, kill_at=T_E1, min_periods=1, min_side=1)       # one screen only, before the cut: the after-cut side has none
    assert one_side["verdict"] == "FAIL" and one_side["periods_ge3_after_cut"] == 0 and one_side["log_md5"]["whole"] == one_side["log_md5"]["resumed"] and not one_side["floor_ok"]
    assert E.leg_e1(scratch, ["2026-09-25"], START, END, SHA, kill_at=T_E1, min_periods=50, min_side=0)["verdict"] == "FAIL"           # the whole-run floor


def test_f_3d_three_processes_with_different_hash_seeds_pass_the_drills_rule(tmp_path):
    scratch, _ = built(tmp_path)
    rep = E.leg_f_3d(scratch, ["2026-09-25"], START, END, SHA, 1, min_span_ms=0)           # min_span_ms=0 is a function argument for this fixture only; the command line cannot set it
    assert rep["verdict"] == "PASS" and rep["floor_ok"] and rep["periods_ge3_per_run"] == [rep["periods_ge3"]] * 3, rep["detail"]
    assert len(set(rep["log_md5"])) == 1 and len(set(rep["cards_md5"])) == 1 and rep["periods_ge3"] >= 1
    assert rep["detail"]["md5_equal"] and rep["detail"]["invariants_ok"] and rep["detail"]["non_vacuous"]


def test_f_3d_is_a_three_day_leg_and_enforces_the_floor_in_each_run(tmp_path):
    scratch, _ = built(tmp_path)
    with pytest.raises(E.Refused, match="f3d_needs_a_72_hour_span"):
        E.leg_f_3d(scratch, ["2026-09-25"], START, END, SHA, 1)                           # the default span rule: 72 hours, i.e. three days of hourly screens
    assert E.F3D_MIN_SPAN_MS == 72 * 3_600_000
    below = E.leg_f_3d(scratch, ["2026-09-25"], START, END, SHA, 50, min_span_ms=0)         # the md5s and invariants hold, the floor does not
    assert below["verdict"] == "FAIL" and not below["floor_ok"] and below["detail"]["md5_equal"]


def test_u2_is_exercised_a_pool_with_no_migration_is_counted_and_the_legs_still_agree(tmp_path):
    seq = walker_stream(drop_migration={POOLS[3]})
    scratch, _ = built(tmp_path, seq)
    rep = E.leg_equiv_1d(scratch, ["2026-09-25"], START, END, SHA, min_periods=1)
    assert rep["verdict"] == "PASS" and rep["u2_age_unknown"] >= 1                         # the fourth pool is refused as age_unknown in both runs, identically


def test_run_all_reports_md5s_and_counts_only_and_removes_its_scratch(tmp_path):
    rep = E.run_all(walker_stream(), tmp_path / "all", FIRST_HOUR, LAST_HOUR, SHA, start_ms=START, end_ms=END, kill_at=T_E1, min_periods=1, min_side=0, ref_sha=PRE_U2,
                    legs=("equiv", "f3d", "e1", "rc"), f3d_min_span_ms=0)
    assert rep["verdict"] == "PASS" and [r["leg"] for r in rep["legs"]] == ["EQUIV_1D", "F_3D", "E1", "EQUIV_RC"] and rep["v_fixture_lamports"] == E.V_LAMPORTS
    flat = json.dumps(rep, default=str)
    for pool in POOLS.values():
        assert pool not in flat
    for word in ("signature", "price", "pnl", "mint"):
        assert f'"{word}"' not in flat
    assert not (tmp_path / "all" / "derived").exists() and not (tmp_path / "all" / "tip").exists()



# ------------------------------------------------------------------------------------------------------------------------ EQUIV_RC (QP E4)
def test_norm_md5_drops_only_the_pinned_keys(tmp_path):
    def write(root: Path, rows_l: list, rows_s: list) -> Path:
        (root / "decisions" / "L").mkdir(parents=True)
        (root / "decisions" / "SCAN").mkdir(parents=True)
        (root / "decisions" / "L" / "a.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows_l))
        (root / "decisions" / "SCAN" / "a.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows_s))
        return root

    base_l = [{"kind": "rank", "bot_id": "hv7.0-L", "pick": "p1", "request_sha256": "aaa", "sha": "s1", "prev": "p0"}]
    base_s = [{"kind": "start", "frozen_sha": "abc", "sha": "s2", "prev": "p0", "bots": ["hv7.0-L"]}, {"kind": "period", "screen": ["p1"], "no_trade": False, "sha": "s3", "prev": "s2"}]
    ref = write(tmp_path / "ref", base_l, base_s)
    new_l = [{"kind": "rank", "bot_id": "hv7.0-L", "pick": "p1", "request_sha256": "bbb", "sha": "x1", "prev": "y0"}]                  # request hash and chain differ: allowed
    new_s = [{"kind": "start", "frozen_sha": "zzz", "sha": "x2", "prev": "y0", "bots": ["hv7.0-L"]}, {"kind": "period", "screen": ["p1"], "no_trade": False, "sha": "x3", "prev": "x2"}]
    new = write(tmp_path / "new", new_l, new_s)
    assert E.norm_md5(ref) == E.norm_md5(new)
    for mutate in (lambda l, s_: (l[0].update(pick="p2"), l, s_)[1:], lambda l, s_: (s_[1].update(screen=["p9"]), l, s_)[1:], lambda l, s_: (s_[1].update(no_trade=True), l, s_)[1:],
                   lambda l, s_: (l[0].update(extra=1), l, s_)[1:]):
        l2, s2 = json.loads(json.dumps(new_l)), json.loads(json.dumps(new_s))
        mutate(l2, s2)
        assert E.norm_md5(write(tmp_path / f"m{id(mutate)}", l2, s2)) != E.norm_md5(ref)                          # any other difference is seen
    # request_sha256 is dropped on L rank rows only
    s_only = json.loads(json.dumps(new_s))
    s_only[1]["request_sha256"] = "ccc"
    assert E.norm_md5(write(tmp_path / "s_only", new_l, s_only)) != E.norm_md5(ref)
    assert E.RC_DROP_KEYS == ("sha", "prev", "frozen_sha") and E.RC_DROP_L == ("request_sha256",) and E.LAST_PROVEN_SHA == "c8fcf761" and E.RC_PREFIX == () and E.RC_REF_PREFIX == "hv7.0-"
    other_id = json.loads(json.dumps(new_l))
    other_id[0]["bot_id"] = "hv7.0t-L"
    assert E.norm_md5(write(tmp_path / "other_id", other_id, new_s)) != E.norm_md5(ref)               # an id difference is a difference: no silent mapping


def test_equiv_rc_the_runner_with_u2_off_matches_the_runner_before_u2_on_the_writer_built_tape(tmp_path):
    """The reference is main before #1170 (`4c374db3^`): the HV7 runner with no U2, no creation index, `hv7.0-` ids, max_tokens 600. The F1 runner with u2=False must give the same decisions; only the
    chain keys, the frozen sha, L's request hash and the id prefix differ (the names-only rule)."""
    scratch, _ = built(tmp_path)
    rep = E.leg_equiv_rc(scratch, ["2026-09-25"], START, END, SHA, 1, PRE_U2)
    assert rep["verdict"] == "PASS" and all(rep["equal"].values()), rep
    assert rep["periods_ge3"] >= 1 and rep["norm_md5"]["ref"] == rep["norm_md5"]["new"] and rep["ref_sha"] == PRE_U2 and rep["diff_names_only"] == {}
    assert R.BOT_PREFIX == "hv7.0t-" and R.Hv7Config().u2 is True                                   # the leg's harness-only settings are restored
    assert E.leg_equiv_rc(scratch, ["2026-09-25"], START, END, SHA, 10_000, PRE_U2)["verdict"] == "FAIL"        # the floor
    with pytest.raises(E.Refused, match="ref_sha_not_found"):
        E.leg_equiv_rc(scratch, ["2026-09-25"], START, END, SHA, 1, "0000000000000000000000000000000000000000")


def test_equiv_rc_sees_a_real_difference_and_reports_its_key_names_only(tmp_path):
    """Against HEAD (a runner WITH U2, whose SCAN `counts` carry the U2 keys, and the hv7.0t- ids) the u2=False runner under hv7.0- differs: the leg must FAIL and name the keys, never a value."""
    scratch, _ = built(tmp_path)
    rep = E.leg_equiv_rc(scratch, ["2026-09-25"], START, END, SHA, 1, "HEAD")
    assert rep["verdict"] == "FAIL" and not rep["equal"]["decision_logs_norm_md5"] and rep["equal"]["cards_md5"] and rep["equal"]["entries"]
    assert "counts" in {k for v in rep["diff_names_only"].values() for k in v["differing_keys"]}                      # the U2 count keys (the ids differ too: HEAD runs under hv7.0t-)
    assert "p1" not in json.dumps(rep["diff_names_only"]) and all(set(v) == {"rows_ref", "rows_new", "rows_differing", "differing_keys"} for v in rep["diff_names_only"].values())


def test_the_command_line_defaults_are_job_a_and_the_ref_is_the_last_proven_runner():
    import inspect

    src = inspect.getsource(E.main)
    assert '"2026-08-14T12..2026-08-15T14"' in src and 'default="equiv,e1,rc"' in src and "LAST_PROVEN_SHA" in src
