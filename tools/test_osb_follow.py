"""osb_follow: chunked 1-hour follow == one single replay (decision md5 and ledger md5), manifest pin, restart md5 check, override whitelist, stakes, path guard.
Synthetic fixtures only (tools/test_floor_arena_executor._host_log_scenarios); no real tape, ledger, registry or key is opened. Nothing here says any book makes money."""
import collections
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "research"))
import osb_follow as O  # noqa: E402
from tools import arena_rule_l1 as L  # noqa: E402
from tools import floor_lane_host as H  # noqa: E402
from tools.arena_declog import DecisionLog  # noqa: E402
from tools.arena_rules_testkit import hour_name, write_tape  # noqa: E402
from tools.test_arena_rule_l1 import H0, HOUR  # noqa: E402
from tools.test_floor_arena_executor import _host_log_scenarios  # noqa: E402
from tools.test_floor_lane_host import gap_row, two_trigger_rows  # noqa: E402
from tools.test_floor_lane_host_stage1 import DROWN, _prints, status_row  # noqa: E402
from tools.test_arena_rule_l1 import row as trow  # noqa: E402
import tools.test_arena_rule_l1 as T  # noqa: E402

N_HOURS = 60
T0 = H.parse_hour("2026-11-01T00")  # first hour of the fixture tape
S0 = 26  # the book starts 26 h into the tape: the earlier hours are seed + warm-up (no ticks), which keeps the replay short
START = T0 + S0 * HOUR
START_HOUR = "2026-11-02T02"
SEED = "2026-10-22T00"  # >= 201 h before START_HOUR; seeds the table from fixture hours 0 .. 20 (to the warm-up start)
EK = dict(sps_fn=lambda: 0.4)  # the fixture is too sparse for the engine slot clock; its slots run at 0.4 s
RT = dict(_table_mode="first_print", _engine_kw=EK)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))
    monkeypatch.setattr(T, "SHA", H.FROZEN_SHA)


def shifted_rows(tmp, rows=None):
    """_host_log_scenarios rows (3 pools: one take-profit, two time exits), moved onto the calendar hour T0 so the hour keys match start_hour."""
    d = T0 - H0
    rows = _host_log_scenarios(tmp) if rows is None else rows
    for r in rows:
        r["t_recv_ms"] += d
        r["slot"] += d // 400
        for k in ("from_ms", "to_ms"):
            if k in r:
                r[k] += d
        if "event_ts" in r:
            r["event_ts"] += d // 1000
    return rows


def gap_rows():
    """Guard fixture (H0-relative, shifted by shifted_rows): pool Q's drained-pool trigger at 30 h 15 min has a data_loss gap (hour 27, an earlier file) inside its 6 h lookback: refused,
    pool blocked; Q's second trigger at 38 h is refused as pool_blocked_after_gap. Pool Z triggers at 36 h with only a writer status span (no row lost, hour 35) in its lookback:
    refused as feed_gap, or with status_span_no_loss an entry_gap_span_ignored row and an entry."""
    rows = two_trigger_rows()
    rows.append(gap_row(H0 + 27 * HOUR, H0 + 27 * HOUR + 1_000))
    _prints(rows, "Z", H0 + 4 * HOUR, H0 + 35 * HOUR + 1_800_000)
    rows.append(trow(H0 + 36 * HOUR, "Z", "sell", **DROWN))
    rows.append(status_row(H0 + 35 * HOUR, H0 + 35 * HOUR + 30_000))
    return sorted(rows, key=lambda r: (r["t_recv_ms"], r.get("pool", "")))


@pytest.fixture
def world(tmp_path):
    return make_world(tmp_path, shifted_rows(tmp_path))


@pytest.fixture
def gworld(tmp_path):
    return make_world(tmp_path, shifted_rows(tmp_path, gap_rows()))


def make_world(tmp_path, rows):
    """src = every hour file of the fixture (an empty file for a quiet hour); tape = what the keeper has linked so far (empty at first)."""
    src = tmp_path / "src"
    write_tape(src, rows)
    for i in range(N_HOURS):
        (src / hour_name(T0 + i * HOUR)).touch()
    names = sorted(p.name for p in src.glob("tape-*"))
    assert len(names) == N_HOURS
    (tmp_path / "tape").mkdir()
    return tmp_path, src, names


def manifest(tmp, **over):
    m = {"book_id": "t-a", "title": "test book", "host": "lane", "rule_overrides": {}, "stakes_sol": [0.10, 0.25], "send_lamports": 160000, "start_hour": START_HOUR,
         "seed_from_hour": SEED, "tape_dir": str(tmp / "tape"), "out_root": str(tmp / "out"), "lane": {"gap_lookback_s": 21600, "status_span_no_loss": False, "cheap_restart": False}}
    m.update(over)
    p = tmp / ("manifest-%d.json" % len(list(tmp.glob("manifest-*"))))
    p.write_text(json.dumps(m))
    return p, m


def link(src, tape, names, n):
    for nm in names[:n]:
        if not (tape / nm).exists():
            shutil.copy(src / nm, tape / nm)


def run_once(tmp, mp, **kw):
    return O.follow(str(mp), once=True, _roots=(str(tmp),), **RT, **kw)


def follow_all(tmp, src, names, mp, n=N_HOURS):
    """One process; reveal one more hour at every sleep, like the keeper."""
    tape = tmp / "tape"
    shown = [S0 + 1]
    link(src, tape, names, S0 + 1)

    def reveal(_s):
        shown[0] += 1
        link(src, tape, names, shown[0])
    return O.follow(str(mp), max_hours=n - S0, sleep_fn=reveal, _roots=(str(tmp),), **RT)


def one_shot(tmp, m, n=N_HOURS, run="oneshot"):
    """The reference: the same host built by the driver's own builder, ONE host.run over the whole range."""
    link(tmp / "src", tmp / "tape", sorted(p.name for p in (tmp / "src").glob("tape-*")), n)
    restore = O.apply_overrides(m["rule_overrides"])
    try:
        host, eng, cfg = O.build_book(m, tmp / run, table_mode="first_print", engine_kw=EK)
        host.run(START, T0 + n * HOUR)
    finally:
        restore()
    return tmp / run


def tree_diff(a, b):
    """first differing ledger row's keys, for a readable assertion message"""
    ra, rb = O.ledger_rows(a), O.ledger_rows(b)
    for i, (u, v) in enumerate(zip(ra, rb)):
        if u != v:
            return "row %d %s: differing keys %s" % (i, u[0], sorted(k for k in set(u[1]) | set(v[1]) if u[1].get(k) != v[1].get(k)))
    return "row counts %d vs %d" % (len(ra), len(rb))


def test_chunked_follow_equals_one_single_replay(world):
    tmp, src, names = world
    mp, m = manifest(tmp)
    assert follow_all(tmp, src, names, mp) == 0
    follow_dir = (tmp / "out" / "book").resolve()
    ref = one_shot(tmp, m)
    rows = [r for r in DecisionLog(str(ref)).rows(L.BOT_ID) if r["kind"] in ("entry", "exit")]
    n_ent, n_ex = sum(r["kind"] == "entry" for r in rows), sum(r["kind"] == "exit" for r in rows)
    assert n_ent >= 3 and n_ex >= 3  # non-vacuous: entries and time/take-profit exits happened in the window
    assert O.decision_md5(follow_dir) == O.decision_md5(ref)
    assert any(r["kind"] in ("open", "trade") for _, r in O.ledger_rows(ref / "ledger"))  # priced rows exist: the ledger comparison is not empty
    assert O.tree_md5(follow_dir / "ledger") == O.tree_md5(ref / "ledger"), tree_diff(follow_dir / "ledger", ref / "ledger")
    st = json.loads((tmp / "out" / "status.json").read_text())
    assert st["last_hour"] == "2026-11-03T11" and st["decision_md5"] == O.decision_md5(ref) and st["manifest_sha256"] == (tmp / "out" / "manifest.sha256").read_text().strip()
    assert st["n_entries"] == n_ent and st["n_exits"] == n_ex and st["active"] is True
    assert len((tmp / "out" / "md5-by-hour.jsonl").read_text().splitlines()) == N_HOURS - S0


FAST = {"HOLD_S": 43200}  # 12 h hold: the shorter window the non-central tests replay (entries at about 30 h, exits by 42 h)


def test_once_with_all_hours_present_equals_the_same_replay(world):
    tmp, src, names = world
    mp, m = manifest(tmp, rule_overrides=FAST)
    link(src, tmp / "tape", names, 48)
    assert run_once(tmp, mp) == 0
    ref = one_shot(tmp, m, 48)
    assert O.decision_md5((tmp / "out" / "book").resolve()) == O.decision_md5(ref)
    assert O.tree_md5((tmp / "out" / "book").resolve() / "ledger") == O.tree_md5(ref / "ledger"), tree_diff((tmp / "out" / "book").resolve() / "ledger", ref / "ledger")


def test_changed_manifest_is_refused_and_the_same_one_resumes(world):
    tmp, src, names = world
    mp, m = manifest(tmp)
    link(src, tmp / "tape", names, S0 + 3)
    assert run_once(tmp, mp) == 0
    sha = (tmp / "out" / "manifest.sha256").read_text()
    mp2, _ = manifest(tmp, stakes_sol=[0.10, 0.25, 0.5])
    assert run_once(tmp, mp2) == 2
    assert (tmp / "out" / "manifest.sha256").read_text() == sha
    assert not (tmp / "out" / "runs" / "0002").exists()  # refused before any run directory
    assert run_once(tmp, mp) == 0


def test_restart_reproduces_md5_by_hour_and_a_tampered_md5_is_exit_4(world):
    tmp, src, names = world
    mp, m = manifest(tmp, rule_overrides=FAST)
    link(src, tmp / "tape", names, S0 + 12)
    assert run_once(tmp, mp) == 0
    f = tmp / "out" / "md5-by-hour.jsonl"
    before = f.read_text()
    assert len(before.splitlines()) == 12
    link(src, tmp / "tape", names, 48)  # the restart covers the 12 stored hours again, then follows the rest
    assert run_once(tmp, mp) == 0
    after = f.read_text()
    assert after.startswith(before) and len(after.splitlines()) == 48 - S0
    assert (tmp / "out" / "book").resolve().name == "0002"
    assert O.decision_md5((tmp / "out" / "book").resolve()) == O.decision_md5(one_shot(tmp, m, 48))
    # tamper with one stored md5: the next start exits 4 and overwrites nothing
    lines = after.splitlines()
    d = json.loads(lines[10])
    d["decision_md5"] = "0" * 32
    lines[10] = json.dumps(d)
    f.write_text("\n".join(lines) + "\n")
    tampered = f.read_text()
    assert run_once(tmp, mp) == 4
    assert f.read_text() == tampered and (tmp / "out" / "book").resolve().name == "0002"


def test_override_whitelist_and_hold_s_moves_hold_ms_and_block_ms_and_restores():
    hold0, block0, tp0, lhold0 = L.HOLD_MS, H.BLOCK_MS, L.TP, H.L_HOLD_MS
    assert hold0 == 86_400_000 + 1_900 and block0 == hold0 + 60_000 + 1_000 + 5_000  # the import-time derivations the override reproduces
    for bad in ({"STAKE_SOL": 1.0}, {"COOLDOWN_MS": 1}, {"HOLD_MS": 5}, {"TP": -1}):
        with pytest.raises(O.Refused):
            O.apply_overrides(bad)
    assert (L.HOLD_MS, H.BLOCK_MS, L.TP) == (hold0, block0, tp0)
    restore = O.apply_overrides({"HOLD_S": 43200, "TP": 1.3, "Q_STAR": 1.0e9})
    try:
        assert L.HOLD_S == 43200.0 and L.HOLD_MS == 43_200_000 + L.LEG_MS
        assert H.BLOCK_MS == 43_200_000 + 1_900 + 60_000 + 1_000 + 5_000
        assert L.TP == 1.3 and L.Q_STAR == 1.0e9
    finally:
        restore()
    assert (L.HOLD_MS, H.BLOCK_MS, L.TP, H.L_HOLD_MS, L.HOLD_S, L.Q_STAR) == (hold0, block0, tp0, lhold0, 86400.0, 0.5e9)


def test_a_run_with_hold_s_restores_the_module_globals(world):
    tmp, src, names = world
    hold0, block0 = L.HOLD_MS, H.BLOCK_MS
    mp, m = manifest(tmp, rule_overrides={"HOLD_S": 43200})
    link(src, tmp / "tape", names, S0 + 2)
    assert run_once(tmp, mp) == 0
    assert (L.HOLD_MS, H.BLOCK_MS) == (hold0, block0)
    v = json.loads((tmp / "out" / "runs" / "0001" / "variant.json").read_text())
    assert v["variant"] == {"HOLD_S": 43200.0} and v["frozen_rule"] is False


def test_unknown_override_in_a_manifest_is_refused(world):
    tmp, src, names = world
    mp, _ = manifest(tmp, rule_overrides={"STAKE_SOL": 1.0})
    assert run_once(tmp, mp) == 2
    assert not (tmp / "out" / "manifest.sha256").exists()


def test_stakes_reach_the_engine_and_send_too(world):
    tmp, src, names = world
    mp, m = manifest(tmp, stakes_sol=[0.10, 0.25, 0.5, 1.0], send_lamports=123_456)
    host, eng, cfg = O.build_book(O.load_manifest(mp), tmp / "b", table_mode="first_print", engine_kw=EK)
    assert eng.cfg.stakes_sol == (0.10, 0.25, 0.5, 1.0) and eng.cfg.send == 123_456 and cfg is eng.cfg
    assert isinstance(host, H.LaneHost)


def test_hostv2_book_has_no_lane_guard(world):
    tmp, src, names = world
    mp, m = manifest(tmp, host="hostv2", gap_exclusion="off")
    m.pop("lane")
    mp.write_text(json.dumps(m))
    host, eng, cfg = O.build_book(O.load_manifest(mp), tmp / "b2", table_mode="first_print")
    assert type(host).__name__ == "HostV2" and eng.gap_exclusion is False


@pytest.mark.parametrize("bad", [{"seed_from_hour": "2026-10-30T00"}, {"host": "other"}, {"stakes_sol": []}, {"extra": 1}, {"lane": {"nope": 1}}, {"book_id": "a b"}])
def test_bad_manifests_are_refused(world, bad):
    tmp, src, names = world
    mp, m = manifest(tmp, **bad)
    with pytest.raises(O.Refused):
        O.load_manifest(mp)


def test_path_guard(tmp_path):
    ok_roots = (str(tmp_path / "root"),)
    (tmp_path / "root" / "tape").mkdir(parents=True)
    base = {"tape_dir": str(tmp_path / "root" / "tape"), "out_root": str(tmp_path / "root" / "out")}
    O.guard_paths(base, ok_roots)
    for k, bad in (("tape_dir", "/data2/mal/arena-tip-feed/hv7"), ("out_root", "/data2/mal/scratch/floor-nov-funnel/x"), ("tape_dir", "/var/lib/mal-live/x"), ("out_root", str(tmp_path / "elsewhere")),
                   ("out_root", "/data/mal/arena/x")):
        with pytest.raises(O.Refused):
            O.guard_paths({**base, k: bad}, ok_roots)
    # the CLI's own root: the default, with no override exposed
    assert O.OSB_ROOT == "/data2/mal/scratch/floor-open-sim" and O.OSB_ROOT in O.TP.ALLOWED_ROOTS
    O.guard_paths({"tape_dir": O.OSB_ROOT + "/keep", "out_root": O.OSB_ROOT + "/books/A"}, (O.OSB_ROOT,))
    for bad in ("/data2/mal/arena-tip-feed/hv7", O.OSB_ROOT + "/../floor-forward-ab/t", O.OSB_ROOT + "/keep/probe"):
        with pytest.raises(O.Refused):
            O.guard_paths({"tape_dir": bad, "out_root": O.OSB_ROOT + "/books/A"}, (O.OSB_ROOT,))
    assert "--roots" not in Path(O.__file__).read_text().split("def main")[1]


def test_follow_refuses_a_manifest_outside_the_root_by_default(world):
    tmp, src, names = world
    mp, _ = manifest(tmp)
    assert O.follow(str(mp), once=True) == 2  # no _roots: the real OSB root applies, tmp is outside it
    assert not (tmp / "out").exists()


# ------------------------------------------------------------------------------------------------ round 2 (QP B1-B4, reviewer 1-3, minors)
KINDS_ALL = ("entry", "exit", "entry_refused_gap", "entry_gap_span_ignored")


def all_kinds(d):
    return [r["kind"] for r in DecisionLog(str(d)).rows(L.BOT_ID)]


def equal_runs(follow_dir, ref):
    assert H.decision_digest(follow_dir, kinds=KINDS_ALL) == H.decision_digest(ref, kinds=KINDS_ALL)
    assert O.decision_md5(follow_dir) == O.decision_md5(ref)
    assert O.tree_md5(follow_dir / "ledger") == O.tree_md5(ref / "ledger"), tree_diff(follow_dir / "ledger", ref / "ledger")
    a = [(r["kind"], r.get("pool"), r.get("reason")) for r in DecisionLog(str(follow_dir)).rows(L.BOT_ID)]
    assert a == [(r["kind"], r.get("pool"), r.get("reason")) for r in DecisionLog(str(ref)).rows(L.BOT_ID)]  # every decision-row kind, in order


def test_gap_guard_chunked_equals_one_shot_flag_off(gworld):
    tmp, src, names = gworld
    mp, m = manifest(tmp, rule_overrides=FAST)
    assert follow_all(tmp, src, names, mp, 50) == 0
    ref = one_shot(tmp, m, 50)
    ks = all_kinds(ref)
    assert ks.count("entry_refused_gap") == 3 and "entry_gap_span_ignored" not in ks  # Q at its trigger, Q's second trigger (pool block), Z's status span (flag off)
    reasons = [r["reason"] for r in DecisionLog(str(ref)).rows(L.BOT_ID) if r["kind"] == "entry_refused_gap"]
    assert reasons.count("pool_blocked_after_gap") == 1 and reasons.count("feed_gap") == 2
    equal_runs((tmp / "out" / "book").resolve(), ref)


def test_gap_guard_chunked_equals_one_shot_status_span_no_loss(gworld):
    tmp, src, names = gworld
    mp, m = manifest(tmp, rule_overrides=FAST, lane={"gap_lookback_s": 21600, "status_span_no_loss": True, "cheap_restart": False})
    assert follow_all(tmp, src, names, mp, 50) == 0
    ref = one_shot(tmp, m, 50)
    ks = all_kinds(ref)
    assert ks.count("entry_gap_span_ignored") == 1 and ks.count("entry_refused_gap") == 2 and ks.count("entry") == 1 and "exit" in ks
    equal_runs((tmp / "out" / "book").resolve(), ref)


def test_hostv2_chunked_equals_one_shot(world):
    tmp, src, names = world
    mp, m = manifest(tmp, host="hostv2", gap_exclusion="off", rule_overrides=FAST)
    m.pop("lane")
    mp.write_text(json.dumps(m))
    assert follow_all(tmp, src, names, mp, 48) == 0
    ref = one_shot(tmp, m, 48)
    assert "entry" in all_kinds(ref) and "entry_refused_gap" not in all_kinds(ref)
    equal_runs((tmp / "out" / "book").resolve(), ref)


def test_code_version_pin_and_ledger_md5_per_hour(world):
    tmp, src, names = world
    mp, m = manifest(tmp, rule_overrides=FAST)
    link(src, tmp / "tape", names, S0 + 6)
    assert run_once(tmp, mp) == 0
    out = tmp / "out"
    assert (out / "code.sha256").read_text().strip() == O.code_sha256()
    lines = [json.loads(x) for x in (out / "md5-by-hour.jsonl").read_text().splitlines()]
    assert len(lines) == 6 and all(len(x["ledger_md5"]) == 32 for x in lines)
    st = json.loads((out / "status.json").read_text())
    assert st["code_sha256"] == O.code_sha256() and st["ledger_md5"] == lines[-1]["ledger_md5"] and st["seed_from_hour"] == SEED
    (out / "code.sha256").write_text("0" * 64 + "\n")
    assert run_once(tmp, mp) == 2 and not (out / "runs" / "0002").exists()
    (out / "code.sha256").write_text(O.code_sha256() + "\n")
    lines[3]["ledger_md5"] = "0" * 32
    f = out / "md5-by-hour.jsonl"
    f.write_text("".join(json.dumps(x) + "\n" for x in lines))
    tampered = f.read_text()
    assert run_once(tmp, mp) == 4 and f.read_text() == tampered and (out / "book").resolve().name == "0001"


def test_age_hi_override_needs_a_longer_seed(world):
    tmp, src, names = world
    mp, m = manifest(tmp, rule_overrides={"AGE_HI": 300 * 3600})
    with pytest.raises(O.Refused, match="seed_from_hour"):
        O.load_manifest(mp)
    mp, m = manifest(tmp, rule_overrides={"AGE_HI": 300 * 3600}, seed_from_hour="2026-10-20T00")
    O.load_manifest(mp)
    mp, m = manifest(tmp, rule_overrides={"AGE_LO": 300 * 3600})
    with pytest.raises(O.Refused, match="AGE_LO"):
        O.load_manifest(mp)
    mp, m = manifest(tmp, rule_overrides={"AGE_LO": 100000.5})
    with pytest.raises(O.Refused, match="whole"):
        O.load_manifest(mp)
    mp, m = manifest(tmp, stakes_sol=[0.25, 0.10])
    with pytest.raises(O.Refused):
        O.load_manifest(mp)


def test_status_shows_waiting_for_a_missing_hour(world):
    tmp, src, names = world
    mp, m = manifest(tmp, rule_overrides=FAST)
    link(src, tmp / "tape", names, S0 + 1)
    calls = []

    def sleeper(_s):
        calls.append(1)
        if len(calls) == 2:
            st = json.loads((tmp / "out" / "status.json").read_text())
            assert st["waiting_for_hour"] == "2026-11-02T03" and st["waiting_since_utc"] and st["last_hour"] == "2026-11-02T02"
            link(src, tmp / "tape", names, S0 + 2)
        if len(calls) == 3:
            raise RuntimeError("stop")
    with pytest.raises(RuntimeError, match="stop"):
        O.follow(str(mp), sleep_fn=sleeper, _roots=(str(tmp),), **RT)
    st = json.loads((tmp / "out" / "status.json").read_text())
    assert st["last_hour"] == "2026-11-02T03" and st["waiting_for_hour"] == "2026-11-02T04"  # the second hour was processed; the third is now awaited


def test_a_second_process_on_the_same_book_is_refused(world):
    import fcntl
    tmp, src, names = world
    mp, m = manifest(tmp, rule_overrides=FAST)
    (tmp / "out").mkdir()
    link(src, tmp / "tape", names, S0 + 1)
    with open(tmp / "out" / ".osb.lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert run_once(tmp, mp) == 2
    assert not (tmp / "out" / "runs").exists()
    assert run_once(tmp, mp) == 0  # the lock was released with the holder


def test_a_tape_symlink_resolving_outside_the_root_is_refused(world):
    tmp, src, names = world
    mp, m = manifest(tmp, rule_overrides=FAST)
    outside = tmp.parent / ("outside-" + tmp.name)
    outside.mkdir()
    (outside / "x.jsonl").write_text("")
    link(src, tmp / "tape", names, S0 + 1)
    (tmp / "tape" / names[S0 + 5]).symlink_to(outside / "x.jsonl")  # ahead of the cursor, never the hour being read
    assert run_once(tmp, mp) == 2 and not (tmp / "out" / "runs").exists()
    (tmp / "tape" / names[S0 + 5]).unlink()

    def sleeper(_s):  # a link appears as the next hour while following
        (tmp / "tape" / names[S0 + 1]).symlink_to(outside / "x.jsonl")
    assert O.follow(str(mp), sleep_fn=sleeper, _roots=(str(tmp),), **RT) == 2


def test_overlapping_tape_and_out_root_are_refused(tmp_path):
    root = (str(tmp_path),)
    for t, o in ((tmp_path / "a", tmp_path / "a"), (tmp_path / "a", tmp_path / "a" / "out"), (tmp_path / "a" / "keep", tmp_path / "a")):
        t.mkdir(parents=True, exist_ok=True)
        with pytest.raises(O.Refused, match="disjoint"):
            O.guard_paths({"tape_dir": str(t), "out_root": str(o)}, root)


def test_a_torn_last_line_of_md5_by_hour_is_dropped_and_the_restart_goes_on(world, capsys):
    tmp, src, names = world
    mp, m = manifest(tmp, rule_overrides=FAST)
    link(src, tmp / "tape", names, S0 + 5)
    assert run_once(tmp, mp) == 0
    f = tmp / "out" / "md5-by-hour.jsonl"
    good = f.read_text().splitlines()
    f.write_text("\n".join(good) + '\n{"hour": "2026-11-0')
    link(src, tmp / "tape", names, S0 + 8)
    assert run_once(tmp, mp) == 0
    assert "torn last line" in capsys.readouterr().err
    lines = [json.loads(x) for x in f.read_text().splitlines()]
    assert [x["hour"] for x in lines][:5] == [json.loads(x)["hour"] for x in good][:5] and len(lines) == 8


# ------------------------------------------------------------------------------------------------------------------------------ gap_exclusion "hold" twins
def _old_engine_class():
    """the GapRangeEngine of origin/main 8c7061e7 (frozen copy), the 'old code path' of the byte-identity regression"""
    import types
    mod = types.ModuleType("floor_gap_range_engine_old")
    src = (Path(__file__).resolve().parent / "fixtures_osb" / "floor_gap_range_engine_8c7061e7.py.txt").read_text()
    exec(compile(src, "floor_gap_range_engine_8c7061e7.py", "exec"), mod.__dict__)
    return mod.GapRangeEngine


@pytest.mark.parametrize("flag", ["on", "off"])
@pytest.mark.parametrize("use_gap_fixture", [False, True])
def test_on_and_off_are_byte_identical_to_the_old_engine(request, monkeypatch, flag, use_gap_fixture):
    tmp, src, names = request.getfixturevalue("gworld" if use_gap_fixture else "world")
    mp, m = manifest(tmp, rule_overrides=FAST, gap_exclusion=flag)
    new = one_shot(tmp, m, 48, run="new")
    monkeypatch.setattr(O, "GapRangeEngine", _old_engine_class())
    old = one_shot(tmp, m, 48, run="old")
    assert use_gap_fixture or O.ledger_rows(new / "ledger"), "fixture has no ledger rows"  # the gap fixture refuses its entries: an empty ledger on both sides is still compared
    assert O.tree_md5(new / "ledger") == O.tree_md5(old / "ledger"), tree_diff(new / "ledger", old / "ledger")  # the ledger projection (engine_id / start rows dropped) is the contract
    assert O.decision_md5(new) == O.decision_md5(old)


def test_hold_manifest_and_twin_of_validation(world):
    tmp, src, names = world
    mp, m = manifest(tmp, gap_exclusion="hold", twin_of="strict-1")
    assert O.load_manifest(mp)["twin_of"] == "strict-1"
    for bad in ({"gap_exclusion": "hold", "twin_of": "t-a"}, {"gap_exclusion": "on", "twin_of": "x"}, {"twin_of": "x"}, {"gap_exclusion": "hold", "twin_of": "a b"}, {"gap_exclusion": "maybe"}):
        mp, _ = manifest(tmp, **bad)
        with pytest.raises(O.Refused):
            O.load_manifest(mp)


def test_decisions_md5_is_equal_between_a_strict_book_and_its_twin(world):
    tmp, src, names = world
    link(src, tmp / "tape", names, 48)
    mp_s, m_s = manifest(tmp, book_id="strict-1", rule_overrides=FAST, out_root=str(tmp / "out_s"))
    mp_t, m_t = manifest(tmp, book_id="twin-1", rule_overrides=FAST, out_root=str(tmp / "out_t"), gap_exclusion="hold", twin_of="strict-1")
    assert run_once(tmp, mp_s) == 0 and run_once(tmp, mp_t) == 0
    a, b = (tmp / "out_s" / "decisions.md5").read_text(), (tmp / "out_t" / "decisions.md5").read_text()
    assert a == b and len(a.strip()) == 32
    st = json.loads((tmp / "out_t" / "status.json").read_text())
    assert st["decisions_md5"] == a.strip()
    assert O.decisions_md5((tmp / "out_t" / "book").resolve()) == a.strip()
    assert any(r["kind"] == "open" for _, r in O.ledger_rows((tmp / "out_t" / "book").resolve() / "ledger"))  # the md5 is over real decision rows, not an empty list
