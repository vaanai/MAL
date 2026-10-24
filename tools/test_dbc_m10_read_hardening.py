"""Tests for the DBC m10 read hardening (QP-RULING-1024-read-hardening.md): tools/dbc_m10_read_ops.py, tools/dbc_m10_read_launch.py, scripts/research/dbc-m10-read-job.sh,
tools/dbc_m10_gapfix.py, tools/dbc_m10_decode_check.py, tools/dbc_m10_engine_equiv.py. SYNTHETIC fixtures only (temp dirs, scratch markers, fake fetchers): no real tape,
no fwdv1 dir, no network, no real marker. The job-script end-to-end case reaches the tagged tool only before 2026-10-31T00:00Z, where its time gate refuses first (rc 6).
Run: python3 -m tools.test_dbc_m10_read_hardening"""
from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from tools import dbc_fwd as W
from tools import dbc_fwd_read as R
from tools import dbc_fwd_read_am1 as A
from tools import dbc_m10_decode_check as DC
from tools import dbc_m10_engine_equiv as EQ
from tools import dbc_m10_gapfix as G
from tools import dbc_m10_read_launch as LA
from tools import dbc_m10_read_ops as O
from tools import dbc_price as L
from tools import test_dbc_fwd as T

REPO = Path(__file__).resolve().parent.parent
JOB = REPO / "scripts/research/dbc-m10-read-job.sh"
LATE = datetime(2026, 11, 2, tzinfo=timezone.utc)
SCRATCH = tempfile.mkdtemp(prefix="dbc-m10-rh-")


def _tmp() -> Path:
    return Path(tempfile.mkdtemp(dir=SCRATCH))


def _fwd_root(age_s: int = 3600, drop: str | None = None, extra: str | None = None) -> Path:
    d = _tmp() / "meteora_dbc"
    d.mkdir()
    t = time.time() - age_s
    for n in O.expected_names():
        if n != drop:
            (d / n).write_bytes(b"")
            os.utime(d / n, (t, t))
    if extra:
        (d / extra).write_bytes(b"")
        os.utime(d / extra, (t, t))
    os.utime(d, (t, t))
    return d


def _dbc_dir(readme: bool = True, lock: bool = True, am1_file: bool = False) -> Path:
    d = _tmp()
    if readme:
        old = d / O.OLD_OUT
        old.mkdir()
        (old / O.README_NAME).write_text(O.README_TEXT)
        if lock:
            os.chmod(old / O.README_NAME, 0o444)
            os.chmod(old, 0o555)
    if am1_file:
        (d / O.AM1_OUT).mkdir()
        (d / O.AM1_OUT / "x").write_text("x")
    return d


_WT: dict = {}


def tag_worktree() -> Path:
    if "wt" not in _WT:
        wt = Path(SCRATCH) / "tag-wt"
        subprocess.run(["git", "-C", str(REPO), "worktree", "add", "--detach", str(wt), O.TAG_COMMIT], check=True, capture_output=True)
        _WT["wt"] = wt
    return _WT["wt"]


def cleanup() -> None:
    if "wt" in _WT:
        subprocess.run(["git", "-C", str(REPO), "worktree", "remove", "--force", str(_WT["wt"])], capture_output=True)
    for p in Path(SCRATCH).rglob("*"):
        with contextlib.suppress(OSError):
            os.chmod(p, 0o755 if p.is_dir() else 0o644)
    subprocess.run(["rm", "-rf", SCRATCH])


# ------------------------------------------------------------------------------------------------------------------------------ ops: completeness
def test_expected_names_match_tagged_tool():
    assert O.expected_names() == R.expected_names() and len(O.expected_names()) == 168


def test_completeness_ok_prints_only_168_of_168():
    ok, why, age = O.completeness(str(_fwd_root()), pgrep=lambda: [])
    assert ok and why == "168/168" and age >= 3500


def test_completeness_refusals():
    ok, why, _ = O.completeness(str(_fwd_root(drop="tape-2026-10-31T23.jsonl.zst")), pgrep=lambda: [])
    assert not ok and "first missing tape-2026-10-31T23" in why and "167" not in why
    ok, why, _ = O.completeness(str(_fwd_root(extra="tape-2026-10-31T23.jsonl.part")), pgrep=lambda: [])
    assert not ok and "partial" in why
    ok, why, age = O.completeness(str(_fwd_root(age_s=100)), pgrep=lambda: [])
    assert not ok and "mtime" in why and 50 < age < 200
    ok, why, _ = O.completeness(str(_fwd_root()), pgrep=lambda: [123])
    assert not ok and "writer" in why
    ok, why, _ = O.completeness(str(_tmp() / "nope"))
    assert not ok and "cannot list" in why


def test_procs_matching_finds_a_child_and_not_itself():
    p = subprocess.Popen(["sleep", "30", "dbc-m10-rh-marker-arg"][:2])
    try:
        assert p.pid in O.procs_matching("sleep 30")
        assert O.procs_matching("no-such-process-dbc-m10-rh") == []
    finally:
        p.kill()


# ------------------------------------------------------------------------------------------------------------------------------ ops: guards
def test_guards():
    ok, why = O.guards(str(_dbc_dir()), [str(JOB), str(REPO / "tools/dbc_m10_read_launch.py")])
    assert ok, why
    assert not O.guards(str(_dbc_dir(readme=False)), [])[0]
    assert "writable" in O.guards(str(_dbc_dir(lock=False)), [])[1]
    assert "not empty" in O.guards(str(_dbc_dir(am1_file=True)), [])[1]
    for txt in ("python3 -m tools.dbc_fwd_read --confirm-one-look\n", "cd x && python3 tools/dbc_fwd_read.py --confirm-one-look", "x=dbc_fwd_read.py", 'run "tools.dbc_fwd_read"'):
        f = _tmp() / "cmd.sh"
        f.write_text(txt)
        ok, why = O.guards(str(_dbc_dir()), [str(f)])
        assert not ok and "unamended" in why, (txt, why)
    f = _tmp() / "cmd.sh"
    f.write_text("python3 -m tools.dbc_fwd_read_am1 --confirm-one-look\npython3 tools/dbc_fwd_read_am1.py\n")
    assert O.guards(str(_dbc_dir()), [str(f)])[0]


def test_old_tool_refuses_into_readme_dir_without_claim():
    """Item 2a proof without the window: the unamended run() with its out dir holding the README returns rc 3 and never creates the (scratch) marker."""
    d = _dbc_dir()
    empty = _tmp() / "empty"
    empty.mkdir()
    mk = _tmp() / "MARKER_v3_scratch"
    with contextlib.redirect_stderr(io.StringIO()) as err:
        rc = R.run(d / O.OLD_OUT, root=str(empty), now=LATE, marker=str(mk))
    assert rc == 3 and not mk.exists() and "output already exists" in err.getvalue()


# ------------------------------------------------------------------------------------------------------------------------------ ops: pins
def test_pins_on_the_tag_worktree():
    wt = tag_worktree()
    ok, why = O.check_pins(str(wt))
    assert ok, why
    for rel, want in O.PINS.items():  # the pinned values are the files AT THE TAG
        assert subprocess.run(["git", "-C", str(REPO), "show", f"{O.TAG_COMMIT}:{rel}"], capture_output=True).stdout and O.sha256_file(wt / rel) == want
    am = (REPO / "docs/research/dbc-m10-forward/AMENDMENT-1.md").read_text()
    for rel, v in O.PINS.items():
        if rel not in ("tools/venue_backfill_dbc.py", "tools/venue_backfill.py"):
            assert f"{v}  {rel}" in am, rel


def test_pins_refusals():
    wt = tag_worktree()
    ok, why = O.check_pins(str(REPO)) if subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() != O.TAG_COMMIT else (False, "HEAD")
    assert not ok and "HEAD" in why
    p = wt / "tools/venue_backfill.py"
    orig = p.read_bytes()
    try:
        p.write_bytes(orig + b"\n")
        ok, why = O.check_pins(str(wt))
        assert not ok and "not clean" in why
    finally:
        p.write_bytes(orig)
    (wt / "stray.txt").write_text("x")
    try:
        assert "not clean" in O.check_pins(str(wt))[1]
    finally:
        (wt / "stray.txt").unlink()
    ok, why = O.check_pins(str(wt), {**O.PINS, "tools/venue_backfill.py": "0" * 64})
    assert not ok and "sha256 mismatch tools/venue_backfill.py" in why
    assert O.check_pins(str(wt))[0]


def test_job_script_embeds_current_shas():
    s = JOB.read_text()
    assert f"OPS_SHA256={O.sha256_file(REPO / 'tools/dbc_m10_read_ops.py')}\n" in s
    assert f"LAUNCHER_SHA256={O.sha256_file(REPO / 'tools/dbc_m10_read_launch.py')}\n" in s
    assert "TAG_COMMIT=" + O.TAG_COMMIT in s


def test_job_script_is_posix():
    assert subprocess.run(["dash", "-n", str(JOB)]).returncode == 0
    for bad in ("PIPESTATUS", "[[", "function ", "local ", "$'", "<<<", "pipefail"):
        assert bad not in JOB.read_text(), bad


# ------------------------------------------------------------------------------------------------------------------------------ launcher
class FakeF:
    def __init__(self, fail_first=0, exc=RuntimeError, empty=False):
        self.calls, self.fail_first, self.exc, self.empty = [], fail_first, exc, empty

    def __call__(self, keys):
        self.calls.append(list(keys))
        if len(self.calls) <= self.fail_first:
            raise self.exc("getMultipleAccounts gave up (http 503)")
        return {} if self.empty else {k: {"data_b64": "AA=="} for k in keys}


def test_warm_up_three_calls_20s_apart_on_base_map_keys():
    f, sl = FakeF(), []
    LA.warm_up(f, R.BASE_MAP, sleep=sl.append, log=lambda s: None)
    keys = sorted(json.loads(Path(R.BASE_MAP).read_text()))[:100]
    assert f.calls == [keys] * 3 and sl == [20, 20] and len(keys) == 100


def test_warm_up_raises_on_empty_or_error():
    for f in (FakeF(empty=True), FakeF(fail_first=1)):
        try:
            LA.warm_up(f, R.BASE_MAP, sleep=lambda s: None, log=lambda s: None)
        except RuntimeError:
            continue
        raise AssertionError("warm-up must raise")


def test_retrying_policy():
    f, sl = FakeF(fail_first=59), []
    out = LA.retrying(f, sleep=sl.append, log=lambda s: None)(["k2", "k1"])
    assert len(f.calls) == 60 and sl == [60] * 59 and set(out) == {"k1", "k2"} and all(c == ["k2", "k1"] for c in f.calls)
    f, sl = FakeF(fail_first=60), []
    try:
        LA.retrying(f, sleep=sl.append, log=lambda s: None)(["k"])
        raise AssertionError("must raise after 60 attempts")
    except RuntimeError:
        assert len(f.calls) == 60 and len(sl) == 59
    f = FakeF(fail_first=1, exc=KeyError)  # only RuntimeError is retried
    try:
        LA.retrying(f, sleep=lambda s: None, log=lambda s: None)(["k"])
        raise AssertionError("KeyError must propagate")
    except KeyError:
        assert len(f.calls) == 1


def test_null_accounts_are_not_retried():
    f = FakeF(empty=True)
    assert LA.retrying(f, sleep=lambda s: None, log=lambda s: None)(["k"]) == {} and len(f.calls) == 1


def test_launch_calls_tagged_run_with_defaults_and_make_fetch_only():
    seen = {}

    def run(out, **kw):
        seen["out"], seen["kw"] = out, kw
        fetch = kw["make_fetch"]()
        seen["fetched"] = fetch(["a"])
        return 0

    f = FakeF()
    fakeA = SimpleNamespace(DEFAULT_OUT="/x/fwd-read-v1-am1", run=run, R=SimpleNamespace(helius_fetch=lambda: f, BASE_MAP=R.BASE_MAP))
    assert LA.launch(fakeA, sleep=lambda s: None, log=lambda s: None) == 0
    assert seen["out"] == Path("/x/fwd-read-v1-am1") and set(seen["kw"]) == {"make_fetch"} and len(f.calls) == 4 and seen["fetched"] == {"a": {"data_b64": "AA=="}}


def test_failed_warm_up_inside_tagged_run_is_rc7_and_no_claim():
    """The real tagged am1 run() on a synthetic 168-name root: the launcher's make_fetch fails its warm-up in the pre-claim setup block -> rc 7, scratch marker absent."""
    root, out, mk = _fwd_root(), _tmp() / "out", _tmp() / "MARKER_v3_scratch"
    fakeA = SimpleNamespace(R=SimpleNamespace(helius_fetch=lambda: FakeF(empty=True), BASE_MAP=R.BASE_MAP))
    with contextlib.redirect_stderr(io.StringIO()) as err, contextlib.redirect_stdout(io.StringIO()) as so:
        rc = A.run(out, root=str(root), now=LATE, marker=str(mk), make_fetch=LA.make_fetch_factory(fakeA, sleep=lambda s: None, log=lambda s: None))
    assert rc == 7 and not mk.exists() and "marker NOT claimed" in err.getvalue() and "LOOK CONSUMED" not in so.getvalue()


def test_launcher_main_refuses_without_mode_and_text_is_clean():
    assert subprocess.run(["python3", str(REPO / "tools/dbc_m10_read_launch.py"), "--tag-root", "/nonexistent"], capture_output=True).returncode == 2
    assert O.OLD_TOOL_RE.search((REPO / "tools/dbc_m10_read_launch.py").read_text()) is None


def test_launcher_loads_only_from_tag_root():
    wt = tag_worktree()
    code = ("import sys; sys.path.insert(0, %r); import dbc_m10_read_launch as LA; A = LA.load_am1(%r); print(A.__file__); print(A.R.__file__)"
            % (str(REPO / "tools"), str(wt)))
    p = subprocess.run(["python3", "-c", code], capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert p.returncode == 0, p.stderr
    assert all(ln.startswith(str(wt.resolve())) for ln in p.stdout.split()), p.stdout


# ------------------------------------------------------------------------------------------------------------------------------ job script end to end
def _job(mode="A", mem="0", wt=None, root=None, dbc=None, ops=None):
    env = {**os.environ, "TEST_FWD_ROOT": str(root or _fwd_root()), "TEST_DBC_DIR": str(dbc or _dbc_dir()), "TEST_OPS_DIR": str(ops or _tmp() / "ops"),
           "TEST_NOT_BEFORE": "0", "TEST_PGREP_PAT": "no-such-process-dbc-m10-rh", "DBC_READ_WT": str(wt or tag_worktree()), "MISCUSI_JOB_ID": "t1"}
    return subprocess.run(["sh", str(JOB), mode, mem], capture_output=True, text=True, env=env, timeout=600)


def test_job_refusals_are_logged_as_not_a_look():
    ops = _tmp() / "ops"
    assert _job(mode="X", ops=ops).returncode == 12
    assert _job(mem="x", ops=ops).returncode == 12
    assert _job(wt=REPO, ops=ops).returncode == 10 if subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() != O.TAG_COMMIT else True
    assert _job(mem="100000", ops=ops).returncode == 13
    assert _job(root=_fwd_root(drop="tape-2026-10-25T00.jsonl.zst"), ops=ops).returncode == 11
    assert _job(dbc=_dbc_dir(readme=False), ops=ops).returncode == 12
    assert _job(dbc=_dbc_dir(am1_file=True), ops=ops).returncode == 12
    lines = (ops / "READ-ATTEMPTS.log").read_text().splitlines()
    assert len(lines) == 7 and all("not a look" in ln and ln.startswith("job t1 | rc ") for ln in lines), lines


def test_job_reaches_tagged_time_gate_before_the_window_end():
    if datetime.now(timezone.utc) >= datetime(2026, 10, 31, tzinfo=timezone.utc):
        r = _job()
        assert r.returncode == 12 and "TEST_* overrides" in r.stdout  # the test can never reach the claim
        return
    ops = _tmp() / "ops"
    r = _job(ops=ops)
    assert r.returncode == 6, (r.stdout, r.stderr)
    assert "PINS OK" in r.stdout and "168/168" in r.stdout and "GUARDS OK" in r.stdout and "launcher sha256" in r.stdout
    assert "LOOK CONSUMED" not in r.stdout and "REFUSED (time gate, marker NOT claimed)" in r.stdout
    att = (ops / "READ-ATTEMPTS.log").read_text()
    assert "rc 6 | pre-claim refusal by the tool (not a look)" in att
    assert not [p for p in (tag_worktree()).rglob("__pycache__")], "the tag worktree must stay clean"


# ------------------------------------------------------------------------------------------------------------------------------ procedure G
H1, H2, H3 = "2026-10-25T03", "2026-10-25T04", "2026-10-26T10"


def _g_main():
    d = _tmp() / "fwdv1"
    (d / G.VENUE).mkdir(parents=True)
    cp = {"version": 1, "credits_used": 1000, "hours": {H1: {"gap": True, "credits": 100}, H2: {"txs": 5, "pages": 1, "rows": 9, "credits": 50},
                                                     H3: {"gap": True, "credits": 100}, "2026-10-24T23": {"gap": True}}}
    (d / G.CP_NAME).write_text(json.dumps(cp))
    for h in (H1, H2, H3):
        (d / G.VENUE / f"tape-{h}.jsonl.zst").write_text("GAP " + h if h != H2 else "OK")
    return d


def _g_runner(ok_hours, calls, credits=70, rc_for=None):
    def run(args):
        calls.append(args)
        h, out = args[args.index("--start") + 1], Path(args[args.index("--out-dir") + 1])
        assert args[args.index("--end") + 1] == G.next_hour(h)
        cpf = out / G.CP_NAME
        cp = json.loads(cpf.read_text()) if cpf.exists() else {"credits_used": 0, "hours": {}}
        cp["credits_used"] += credits
        cp["hours"][h] = {"txs": 1, "credits": credits} if h in ok_hours else {"gap": True}
        (out / G.VENUE).mkdir(parents=True, exist_ok=True)
        (out / G.VENUE / f"tape-{h}.jsonl.zst").write_text("NEW " + h)
        cpf.write_text(json.dumps(cp))
        return (rc_for or {}).get(h, 0)

    return run


def _pass(n, main, stage, ops, runner, now=time.time, budget=300_000, writers=lambda: []):
    out: list = []
    rc = G.run_pass(n, main_out=str(main), stage_root=str(stage), ops=str(ops), budget=budget, job="g1", runner=runner, pins_ok=lambda: (True, "ok"), writers=writers,
                    now=now, log=out.append)
    return rc, "\n".join(out)


def test_g_identify_prints_only_window_gap_labels():
    main = _g_main()
    assert G.gap_labels(str(main / G.CP_NAME)) == [H1, H3]
    p = subprocess.run(["python3", str(REPO / "tools/dbc_m10_gapfix.py"), "identify", "--main-out", str(main)], capture_output=True, text=True)
    assert p.returncode == 0 and p.stdout == f"{H1}\n{H3}\n"


def test_g_pass_replaces_only_successful_gap_hours_by_rename():
    main, base, calls = _g_main(), _tmp(), []
    stage, ops = base / "fwdv1-gapfix", base / "ops"
    rc, out = _pass(1, main, stage, ops, _g_runner({H1}, calls))
    assert rc == G.EXIT_SOME_FAILED
    assert [c[c.index("--start") + 1] for c in calls] == [H1, H3] and all(c[c.index("--credit-cap") + 1] == "300000" for c in calls)
    assert all(c[c.index("--out-dir") + 1] == f"{stage}-p1" for c in calls)
    assert (main / G.VENUE / f"tape-{H1}.jsonl.zst").read_text() == "NEW " + H1 and not (Path(f"{stage}-p1") / G.VENUE / f"tape-{H1}.jsonl.zst").exists()
    assert (main / G.VENUE / f"tape-{H3}.jsonl.zst").read_text() == "GAP " + H3 and (main / G.VENUE / f"tape-{H2}.jsonl.zst").read_text() == "OK"
    assert json.loads((main / G.CP_NAME).read_text())["hours"][H1] == {"gap": True, "credits": 100}  # main checkpoint never written
    glog = (ops / "G-LOG.md").read_text()
    assert f"mv {stage}-p1/{G.VENUE}/tape-{H1}.jsonl.zst {main}/{G.VENUE}/tape-{H1}.jsonl.zst" in glog and "G credits this pass 140" in glog and "rc 1" in glog
    assert "txs" not in glog and "rows" not in glog
    st = json.loads((ops / "G-STATE.json").read_text())
    assert st["credits_total"] == 140 and set(st["hour_passes"]) == {H1, H3}
    # same pass number again, or its staging dir: refused
    assert _pass(1, main, stage, ops, _g_runner(set(), []))[0] == G.EXIT_REFUSED
    # pass 2 right away: H3 is < 30 min since its last pass -> skipped; 31 min later it is fetched with the budget that is left
    calls2: list = []
    rc, out = _pass(2, main, stage, ops, _g_runner({H3}, calls2))
    assert rc == 0 and calls2 == [] and "last pass < 30 min ago" in out
    t = time.time() + 1860
    rc, out = _pass(3, main, stage, ops, _g_runner({H3}, calls2), now=lambda: t)
    assert rc == 0 and [c[c.index("--start") + 1] for c in calls2] == [H3] and calls2[0][calls2[0].index("--credit-cap") + 1] == str(300_000 - 140)
    assert (main / G.VENUE / f"tape-{H3}.jsonl.zst").read_text() == "NEW " + H3 and f"{H1} (already replaced in pass 1)" in out
    assert json.loads((ops / "G-STATE.json").read_text())["replaced"] == {H1: 1, H3: 3}
    t2 = t + 4000
    calls3: list = []
    assert _pass(4, main, stage, ops, _g_runner({H1, H3}, calls3), now=lambda: t2)[0] == 0 and calls3 == []  # nothing left to fetch


def test_g_three_passes_max_and_cap_and_writer_guard():
    main, base = _g_main(), _tmp()
    stage, ops = base / "fwdv1-gapfix", base / "ops"
    t = [time.time()]
    for n in (1, 2, 3):
        _pass(n, main, stage, ops, _g_runner(set(), []), now=lambda: t[0])
        t[0] += 1900
    calls: list = []
    rc, out = _pass(4, main, stage, ops, _g_runner({H1, H3}, calls), now=lambda: t[0])
    assert calls == [] and "3 passes done" in out
    main2, base2, calls = _g_main(), _tmp(), []
    rc, out = _pass(1, main2, base2 / "s", base2 / "ops", _g_runner({H1, H3}, calls, rc_for={H1: 5}))
    assert rc == G.EXIT_CAP and len(calls) == 1 and "not attempted (G cap)" in out
    assert _pass(1, _g_main(), base2 / "s2", base2 / "ops2", _g_runner(set(), []), writers=lambda: [42])[0] == G.EXIT_REFUSED
    assert _pass(1, _g_main(), base2 / "s3", base2 / "ops3", _g_runner(set(), []), budget=0)[0] == G.EXIT_CAP


def test_g_never_replaces_a_non_gap_main_hour():
    main, base = _g_main(), _tmp()
    inner = _g_runner({H1, H3}, [])

    def runner(args):  # the main checkpoint entry turns non-gap between identify and rename
        cp = json.loads((main / G.CP_NAME).read_text())
        cp["hours"][H1] = {"txs": 1}
        (main / G.CP_NAME).write_text(json.dumps(cp))
        return inner(args)

    rc, out = _pass(1, main, base / "s", base / "ops", runner)
    assert (main / G.VENUE / f"tape-{H1}.jsonl.zst").read_text() == "GAP " + H1 and "main checkpoint entry is not a gap" in out


# ------------------------------------------------------------------------------------------------------------------------------ decode-only check
def _zst(path: Path, rows):
    raw = path.with_suffix("")
    raw.write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in rows) + "\n")
    subprocess.run(["zstd", "-q", "--rm", "-f", str(raw), "-o", str(path)], check=True)


def test_decode_check_counts_only():
    root = _tmp() / "meteora_dbc_holdout"
    root.mkdir()
    base = {"v": 1, "venue": "meteora_dbc", "slot": 1, "signature": "s"}
    rows = [{**base, "type": "backfill_hour"},
            {**base, "type": "trade", "event": "EvtSwap3", "side": "buy", "fee_on_base_token": True, "quote_mint": T.WSOL, "config": "C", "quote_reserve_post": 5, "next_sqrt_price": "987654321987"},
            {**base, "type": "trade", "event": "EvtSwap3", "side": "sell", "quote_mint": T.WSOL, "config": "C", "quote_reserve_post": None},
            {**base, "type": "trade", "event": "EvtSwap2", "side": "buy", "quote_mint": "X", "ctx_missing": True},
            {**base, "type": "trade", "event": "EvtSwap9", "side": "buy"},
            {**base, "type": "event_undecoded", "reason": "unknown"},
            {**base, "type": "backfill_hour_end"}]
    _zst(root / "tape-2026-10-17T00.jsonl.zst", rows)
    _zst(root / "tape-2026-10-17T01.jsonl.zst", [{**base, "type": "gap", "reason": "backfill_incomplete"}])
    with contextlib.redirect_stdout(io.StringIO()):
        res = DC.run(str(root), DC.parse_h("2026-10-17T00"), DC.parse_h("2026-10-17T03"))
    h0, h1, h2 = (res["hours"][f"2026-10-17T0{i}"] for i in range(3))
    assert h0["incomplete"] is False and h0["row_types"]["trade"] == 4 and h0["trade_events"] == {"EvtSwap2": 1, "EvtSwap3": 2, "EvtSwap9": 1}
    assert h0["unknown_trade_events"] == ["EvtSwap9"] and h0["event_undecoded"] == {"unknown:": 1}
    assert h0["fields"] == {"sw3": 2, "sw3_buy": 1, "sw3_buy_fob_present": 1, "sw3_missing_quote_reserve_post": 1, "sol_quoted": 2, "ctx_missing": 1}
    assert h1["gap_row"] and h1["incomplete"] and h2 == {"present": False}
    assert res["summary"]["missing"] == ["2026-10-17T02"] and res["summary"]["incomplete"] == ["2026-10-17T01"]
    assert "987654321987" not in json.dumps(res)  # no price leaves the check


def test_decode_check_refuses_window_and_forward_root():
    for root, s, e in (("/x/meteora_dbc_holdout", "2026-10-23T00", "2026-10-25T00"), ("/x/fwdv1/meteora_dbc", "2026-10-17T00", "2026-10-18T00"),
                       ("/x/h", "2026-10-25T00", "2026-10-26T00")):
        try:
            DC.guard(root, DC.parse_h(s), DC.parse_h(e))
            raise AssertionError((root, s))
        except DC.Refused:
            pass
    assert subprocess.run(["python3", str(REPO / "tools/dbc_m10_decode_check.py"), "--root", "/x/fwdv1/meteora_dbc", "--out", str(_tmp() / "o")],
                          capture_output=True).returncode == 3


# ------------------------------------------------------------------------------------------------------------------------------ engine equivalence (Swap3-era check)
SW3_START = datetime(2026, 10, 16, 4, tzinfo=timezone.utc)
SW3_END = datetime(2026, 10, 16, 7, tzinfo=timezone.utc)


@contextlib.contextmanager
def sw3_root():
    """The synthetic 3-hour tape of tools/test_dbc_fwd.py moved to [10-16T04, 10-16T07) under a temp BACKFILL_ROOT."""
    shift = int((SW3_START - T.START).total_seconds())
    rows = []
    for r in T.synth_rows():
        r = dict(r)
        r["slot"] += int(shift / 0.4)
        r["t_recv_ms"] += shift * 1000
        if "block_time" in r:
            r["block_time"] += shift
        rows.append(r)
    old = L.BACKFILL_ROOT
    d = _tmp() / "meteora_dbc"
    d.mkdir()
    t0 = int(SW3_START.timestamp())
    for h in range(3):
        lo, hi = (t0 + 3600 * h) * 1000, (t0 + 3600 * (h + 1)) * 1000
        lines = [{"v": 1, "venue": "meteora_dbc", "type": "backfill_hour", "hour": h}] + [r for r in rows if lo <= r["t_recv_ms"] < hi]
        lines.append({"v": 1, "venue": "meteora_dbc", "type": "backfill_hour_end"})
        _zst(d / f"tape-{(SW3_START + timedelta(hours=h)):%Y-%m-%dT%H}.jsonl.zst", lines)
    L.BACKFILL_ROOT = str(d)
    try:
        yield d
    finally:
        L.BACKFILL_ROOT = old


def test_engine_equiv_check_on_synthetic_sw3_window():
    orig = W.replay_guard
    with sw3_root(), contextlib.redirect_stdout(io.StringIO()):
        res = EQ.check(W, L, None, SW3_START, SW3_END)
    assert W.replay_guard is orig
    assert res["verdict"] == "PASS", (res["why"], res["error"])
    assert res["replay"]["screen"]["trigger_prints"] > 0 and res["replay"]["variants"]["causal"]["decisions_live_policy"] > 0
    assert res["peak_rss_self_mib"] > 0 and res["wall_s"] >= 0


def test_engine_equiv_guard_refuses_other_windows_and_roots():
    g = EQ.make_guard(W, L, EQ.START, EQ.END)
    g(EQ.START, EQ.END, None)
    for s, e, root in ((EQ.START, EQ.END + timedelta(hours=1), None), (datetime(2026, 10, 14, tzinfo=timezone.utc), EQ.END, None),
                       (EQ.START, EQ.END, "/data2/mal/venue-tape-backfill/fwdv1/meteora_dbc"), (EQ.START, EQ.END, "/tmp/other")):
        try:
            g(s, e, root)
            raise AssertionError((s, e, root))
        except W.ReplayRefused:
            pass
    assert EQ.START == datetime(2026, 10, 16, 4, tzinfo=timezone.utc) and EQ.END == datetime(2026, 10, 17, 0, tzinfo=timezone.utc)


def test_engine_equiv_verdict_rules():
    var = {k: {"decisions_live_policy": 3} for k in ("oracle_rows+oracle_mode", "oracle_mode_only", "oracle_rows_only", "causal")}
    ok = {"equivalence_pass": True, "screen": {"trigger_prints": 5}, "variants": var}
    assert EQ.verdict(ok, None)[0] == "PASS"
    assert EQ.verdict(None, "KeyError: x")[0] == "FAIL"
    assert EQ.verdict({**ok, "equivalence_pass": False}, None)[0] == "FAIL"
    assert EQ.verdict({**ok, "screen": {"trigger_prints": 0}}, None)[0] == "UNINFORMATIVE"
    assert EQ.verdict({**ok, "variants": {**var, "causal": {"decisions_live_policy": 0}}}, None)[0] == "FAIL"
    assert EQ.verdict({**ok, "variants": {k: v for k, v in var.items() if k != "causal"}}, None)[0] == "FAIL"


if __name__ == "__main__":
    try:
        for k, v in list(globals().items()):
            if k.startswith("test_"):
                v()
                print("ok", k, flush=True)
    finally:
        cleanup()
