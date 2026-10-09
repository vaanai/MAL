"""The owner's scale ladder (executor F2, claude/h5-executor 7c4db74): the installer never touches /etc/mal-h5/TIER and refuses a bad one,
the runbook creates it with T0 and steps it up and down, and the daily check and the watchdog check the file, report the tier and the trades
in it, and post tier_change, tier_file_problem and tier_step_down_due. No host, no network, no key."""
from __future__ import annotations

import json
import re
from pathlib import Path

from tools import h5_executor
from tools.test_h5_daily_check import H5, NOW, FakeHost, alerts, dc, go, ledger
from tools.test_h5_executor_pinned import INSTALL, _build, _run
from tools.test_h5_watch import Poster, world
from tools.test_h5_watch import go as watch_go

ROOT = Path(__file__).resolve().parent.parent
RUNBOOK = ROOT / "docs/runbooks/h5-executor.md"
EXECUTOR_SRC = (ROOT / "tools/h5_executor.py").read_text()


def runbook() -> str:
    return RUNBOOK.read_text()


def with_tier(h: FakeHost, text: bytes | None = b"T0\n", mode: str = "root:root:644", age_s: float = 7200) -> FakeHost:
    if text is not None:
        h.files[dc.TIER_FILE] = text
        h.modes[dc.TIER_FILE] = mode
        h.mtimes[dc.TIER_FILE] = NOW - age_s
    return h


def tier_state(tier="T1", since_age_s=7200, tier_attempts=None, **kw) -> bytes:
    body = {"halts": {}, "sells_landed": 3, "sells_late": 0,
            "tier_state": {"tier": tier, "since_ms": int((NOW - since_age_s) * 1000), "wallet_lamports": 250_000_000, **kw}}
    if tier_attempts is not None:
        body["tier_attempts"] = tier_attempts  # the executor's own per-tier count (1866327): top level of the counters file, reset at each tier_change
    return json.dumps(body).encode()


# --- the installer never touches TIER and refuses a bad one ---------------------------------------------------------------------

def test_the_installer_leaves_tier_alone_and_refuses_one_the_executor_would_reject(tmp_path):
    etc = lambda p: p / "etc-mal-h5"  # noqa: E731  the installer copy under test points H5_ETC here

    clone, env = _build(tmp_path / "absent")  # absent TIER is fine: it means T0
    assert _run(clone, env).returncode == 99

    clone, env = _build(tmp_path / "good")
    etc(tmp_path / "good").mkdir()
    (etc(tmp_path / "good") / "TIER").write_text("T1\n")
    r = _run(clone, env)
    assert r.returncode == 99 and (etc(tmp_path / "good") / "TIER").read_text() == "T1\n"  # accepted, and not touched

    for name, mode in (("group-writable", "0:0:664"), ("tight", "0:0:600"), ("world-writable", "0:0:646"), ("exec", "0:0:755"),
                       ("owned-by-mal-live", "999:999:644"), ("group-not-root", "0:999:644")):
        clone, env = _build(tmp_path / name, tier_mode=mode)
        etc(tmp_path / name).mkdir()
        (etc(tmp_path / name) / "TIER").write_text("T0\n")
        r = _run(clone, env)
        assert r.returncode != 0 and "TIER exists but is not a regular file owned root:root with mode 0644" in r.stderr and "FAKE-INSTALL" not in r.stdout, name
        assert (etc(tmp_path / name) / "TIER").read_text() == "T0\n"

    clone, env = _build(tmp_path / "link")
    etc(tmp_path / "link").mkdir()
    (etc(tmp_path / "link") / "TIER").symlink_to(tmp_path / "link" / "elsewhere")  # even a dangling symlink
    r = _run(clone, env)
    assert r.returncode != 0 and "TIER exists but is not" in r.stderr and "FAKE-INSTALL" not in r.stdout

    clone, env = _build(tmp_path / "dir")
    (etc(tmp_path / "dir") / "TIER").mkdir(parents=True)  # a directory is not a file
    r = _run(clone, env)
    assert r.returncode != 0 and "TIER exists but is not" in r.stderr


def test_the_installer_only_tests_for_tier_and_never_writes_it():
    t = INSTALL.read_text()
    code = [l.strip() for l in t.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    for l in code:
        if "TIER" in l and not l.startswith("echo "):
            assert not re.search(r"^(touch|rm|install|cp|mv|ln|tee|chown|chmod)\b|\s>\s*\S*TIER|:\s*>", l), l  # only -e / -L / -f tests and one stat
    assert any(l.startswith("if [ -L \"$H5_ETC/TIER\" ]") for l in code)
    assert "the installer never touches it" in t and "never creates, edits or removes it" in t
    assert t.index("-L \"$H5_ETC/TIER\"") > t.index("-e \"$H5_ETC/LIVE_OK\"") and t.index("-L \"$H5_ETC/TIER\"") < t.index('-e "$LIVE_DROPIN"')  # checked before anything is installed


# --- the executor's names and rules, as the tooling reads them ----------------------------------------------------------------------

def test_the_tooling_matches_the_executors_tier_names_and_rules(tmp_path):
    assert tuple(h5_executor.TIERS) == dc.TIERS == ("T0", "T1", "T2")
    assert str(h5_executor.TIER_FILE_PATH) == dc.TIER_FILE == "/etc/mal-h5/TIER"
    assert "tier_file" in h5_executor.PINNED_PATH_KEYS  # no config override in live
    assert h5_executor.LIVE_OK_MODE == 0o644 and h5_executor.LIVE_OK_UID == 0
    assert "tier_attempts" in h5_executor.H5Counters.__dataclass_fields__ and h5_executor.H5_DEFAULT["max_attempts"] == 150  # 150 per tier
    assert "(lifetime; {c.tier_attempts}/{h5.max_attempts} in {c.tier_state.get('tier', 'T0')})" in EXECUTOR_SRC  # the --status line the runbook describes
    assert h5_executor.T2_IMPACT_OK is True, "T2_IMPACT_OK is False again: the runbook's T2 paragraph (allowed by code, written only on the manager's ask) must be reviewed"
    for name in ("tier_change", "tier_file_problem", "tier_step_down_due"):
        assert f'"{name}"' in EXECUTOR_SRC, name
        if name != "tier_change":
            assert name in dc.ALERT_MEANING
    # the content rule: whitespace around exactly T0/T1/T2, anything else is invalid, and the daily check reads it the same way
    for text, ok in ((b"T0\n", True), (b" T1 ", True), (b"T2", True), (b"T3\n", False), (b"t1\n", False), (b"T0 T1\n", False), (b"", False), (b"SECRETWORD\n", False)):
        f = tmp_path / "TIER"
        f.write_bytes(text)
        exe, problem = h5_executor.read_tier(f, root_checks=False)
        assert (problem is None) == ok, (text, exe, problem)
        rep = dc.Report(lambda s: None)
        got = dc.check_tier_file(with_tier(FakeHost(), text), rep)
        assert (got[0] is not None) == ok and ("h5_tier_file" in [n for n, _ in rep.alert_list]) == (not ok), text


# --- the daily check ---------------------------------------------------------------------------------------------------------------

def test_a_bad_tier_file_is_an_alert_and_a_good_or_absent_one_is_not(tmp_path):
    rc, out = go(FakeHost(), tmp=tmp_path / "absent")
    assert rc == 0 and "/etc/mal-h5/TIER is absent: the executor runs T0" in out
    for tier in ("T0", "T1", "T2"):
        rc, out = go(with_tier(FakeHost(), f"{tier}\n".encode()), tmp=tmp_path / tier)
        assert rc == 0 and f"/etc/mal-h5/TIER says {tier}" in out and "file=" + tier in out
    for name, mut in {
        "group writable": lambda h: h.modes.__setitem__(dc.TIER_FILE, "root:root:664"),
        "other writable": lambda h: h.modes.__setitem__(dc.TIER_FILE, "root:root:646"),
        "tighter than 0644": lambda h: h.modes.__setitem__(dc.TIER_FILE, "root:root:600"),
        "owned by mal-live": lambda h: h.modes.__setitem__(dc.TIER_FILE, "mal-live:mal-live:644"),
        "group not root": lambda h: h.modes.__setitem__(dc.TIER_FILE, "root:mal-live:644"),
        "symlink": lambda h: h.links.add(dc.TIER_FILE),
        "not a regular file": lambda h: h.irregular.add(dc.TIER_FILE),
    }.items():
        h = with_tier(FakeHost(), b"T1\n")
        mut(h)
        rc, out = go(h, tmp=tmp_path / ("m" + name.replace(" ", "_")))
        assert rc == 1 and "ALERT h5_tier_file" in out and "mode exactly 0644" in out, name
    for text in (b"T3\n", b"t1", b"SECRETWORD\n", b"T0\nT1\n"):
        rc, out = go(with_tier(FakeHost(), text), tmp=tmp_path / ("c" + str(len(text))))
        assert rc == 1 and "ALERT h5_tier_file" in out and "exactly T0, T1 or T2" in out and "SECRET" not in out  # the content is never echoed


def test_the_tier_line_reports_the_executors_tier_the_files_and_the_trades_in_it(tmp_path):
    h = with_tier(FakeHost(), b"T1\n")
    h.files[f"{H5}/live/h5-counters.json"] = tier_state("T1", since_age_s=3600)
    rows = [{"kind": "decision", "ts_ms": int((NOW - 7200) * 1000)}, {"kind": "decision", "ts_ms": int((NOW - 1800) * 1000)},
            {"kind": "decision", "ts_ms": int((NOW - 600) * 1000)}, {"kind": "skip", "reason": "max_open", "ts_ms": int((NOW - 500) * 1000)}]
    h.files[f"{H5}/live/h5-ledger.jsonl"] = ledger({"kind": "start", "user": dc.WALLET, "ts_ms": 1}, *rows)
    rc, out = go(h, tmp=tmp_path / "ledger")
    assert "tier: executor=T1 file=T1" in out and "trades_in_tier=2" in out  # an older sha without a count: only the decisions since the tier began
    h.files[f"{H5}/live/h5-counters.json"] = tier_state("T1", since_age_s=3600, tier_attempts=11)  # the executor's own per-tier count wins
    assert "trades_in_tier=11" in go(h, tmp=tmp_path / "own")[1]
    h.files[f"{H5}/live/h5-counters.json"] = tier_state("T1", since_age_s=3600, tier_attempts=0)  # zero right after a tier change is a count, not "missing"
    assert "trades_in_tier=0" in go(h, tmp=tmp_path / "zero")[1]
    h.files[f"{H5}/live/h5-counters.json"] = tier_state("T1", since_age_s=3600, attempts=7)  # the in-state field some shas used is the second choice
    assert "trades_in_tier=7" in go(h, tmp=tmp_path / "legacy")[1]
    assert "tier: executor=unknown file=none since=unknown trades_in_tier=unknown" in go(FakeHost(), tmp=tmp_path / "none")[1]
    h.files[f"{H5}/live/h5-counters.json"] = tier_state("T9")  # an unknown tier name is not echoed
    assert "executor=unknown" in go(h, tmp=tmp_path / "bad")[1]


def test_a_valid_file_the_running_executor_has_not_followed_for_15_minutes_is_an_alert(tmp_path):
    def run(name, file_age, tier_in_file=b"T1\n", ex="T0", mut=None):
        h = with_tier(FakeHost(), tier_in_file, age_s=file_age)
        h.files[f"{H5}/live/h5-counters.json"] = tier_state(ex)
        if mut:
            mut(h)
        return go(h, tmp=tmp_path / name)[1]

    assert "h5_tier_unapplied" in alerts(run("old", 16 * 60)) and "running executor is on T0" in run("old2", 16 * 60)
    assert "h5_tier_unapplied" not in alerts(run("fresh", 5 * 60))  # the executor reads it on its next tick
    assert "h5_tier_unapplied" not in alerts(run("same", 7200, ex="T1"))
    assert "h5_tier_unapplied" not in alerts(run("stopped", 7200, mut=lambda h: h.h5_props.update(ActiveState="inactive")))  # nothing is running to follow it
    assert "h5_tier_unapplied" not in alerts(run("invalid", 7200, tier_in_file=b"T3\n"))  # an invalid file is h5_tier_file, and the executor is on T0 by design


def test_tier_change_rows_and_the_two_tier_alerts_are_reported(tmp_path):
    rows = [{"kind": "tier_change", "from_tier": "T0", "to_tier": "T1", "problem": None, "ts_ms": int((NOW - 300) * 1000)},
            {"kind": "alert", "alert": "tier_file_problem", "problem": "tier_file_unsafe", "ts_ms": int((NOW - 200) * 1000)},
            {"kind": "alert", "alert": "tier_step_down_due", "tier": "T1", "why": "total_loss_stop", "ts_ms": int((NOW - 100) * 1000)}]
    h = FakeHost()
    h.files[f"{H5}/live/h5-ledger.jsonl"] = ledger({"kind": "start", "user": dc.WALLET, "ts_ms": 1}, *rows)
    rc, out = go(h, tmp=tmp_path)
    assert "INFO  tier_change T0 -> T1 at " in out
    assert "ALERT h5_executor_alert_tier_file_problem" in out and "failed the executor's checks" in out
    assert "ALERT h5_executor_alert_tier_step_down_due" in out and "never edits the file" in out and "Step up / step down a tier" in out
    rc, out = go(h, "--window-hours", "0.01", tmp=tmp_path / "short")  # outside a very short window nothing is reported
    assert "tier_change" not in out


# --- the watchdog ---------------------------------------------------------------------------------------------------------------------

def test_the_watchdog_posts_tier_change_once_and_the_two_alerts(tmp_path):
    rows = [{"kind": "tier_change", "from_tier": "T0", "to_tier": "T1", "problem": None, "ts_ms": int((NOW - 300) * 1000)},
            {"kind": "alert", "alert": "tier_file_problem", "problem": "tier_file_invalid", "ts_ms": int((NOW - 200) * 1000)},
            {"kind": "alert", "alert": "tier_step_down_due", "tier": "T1", "why": "daily_loss_stop", "ts_ms": int((NOW - 100) * 1000)}]
    h, p = world(), Poster()
    h.files[f"{H5}/live/h5-ledger.jsonl"] = ledger({"kind": "start", "user": dc.WALLET, "ts_ms": 1}, *rows)
    st = tmp_path / "state.json"
    assert watch_go(h, st, p) == 0 and len(p.posts) == 1
    text = p.posts[0][1]
    assert "EVENT tier_change T0 -> T1" in text and "ALERT h5_executor_alert_tier_file_problem" in text and "ALERT h5_executor_alert_tier_step_down_due" in text
    assert json.loads(st.read_text())["tier_change_ts"] == rows[0]["ts_ms"]
    assert watch_go(h, st, p, NOW + 300) == 0 and len(p.posts) == 1  # the same ledger rows are not announced again
    h.files[f"{H5}/live/h5-ledger.jsonl"] += ledger({"kind": "tier_change", "from_tier": "T1", "to_tier": "T0", "problem": "tier_file_invalid", "ts_ms": int((NOW + 350) * 1000)})
    assert watch_go(h, st, p, NOW + 400) == 0
    assert any("EVENT tier_change T1 -> T0 (problem: tier_file_invalid)" in c for _, c in p.posts[1:])  # a new row is
    # a bad TIER file reaches Discord from the file check itself, without waiting for the executor to notice
    h2, p2 = world(), Poster()
    with_tier(h2, b"T1\n", mode="root:root:664")
    watch_go(h2, tmp_path / "s2.json", p2)
    assert any("ALERT h5_tier_file" in c for _, c in p2.posts)


def test_the_watch_summary_names_the_tier(tmp_path, capsys):
    h = world()
    with_tier(h, b"T1\n")
    h.files[f"{H5}/live/h5-counters.json"] = tier_state("T1", since_age_s=60)
    p = Poster()
    assert watch_go(h, tmp_path / "s.json", p) == 0
    assert "tier=T1 alerts=" in capsys.readouterr().out


# --- the runbook ---------------------------------------------------------------------------------------------------------------------

def test_the_runbook_creates_tier_with_t0_at_go_live_and_has_the_step_procedure():
    t = runbook()
    s11 = t[t.index("**Step 11. Create `TIER` (T0) and `LIVE_OK`"):t.index("## Going live (manager, then Helm)")]
    a = s11.index("sudo install -m 0644 -o root -g root /dev/null /etc/mal-h5/TIER")
    b = s11.index("sudoedit /etc/mal-h5/TIER")
    c = s11.index("sudo install -m 0644 -o root -g root /dev/null /etc/mal-h5/LIVE_OK")
    assert a < b < c < s11.index("sudo systemctl start mal-h5-executor")  # the tier is T0 before the gate opens, and before the unit starts
    assert "the word T0" in s11 and "expect exactly: root:root 644 regular file /etc/mal-h5/TIER" in s11 and "expect: T0" in s11
    sec = t[t.index("## Step up / step down a tier"):t.index("## Stop, halt, status")]
    assert "sudoedit /etc/mal-h5/TIER" in sec and "expect exactly: root:root 644 regular file /etc/mal-h5/TIER" in sec
    assert "'\"kind\":\"tier_change\"'" in sec and '"from_tier":"T0","to_tier":"T1","problem":null' in sec and "iflag=nofollow" in sec
    assert "`tier_step_down_due`" in sec and "`tier_file_problem`" in sec and "the executor never lowers the tier by itself" in sec
    assert "manager asks in writing" in sec and "Helm edits the file" in sec and "Missing, unsafe or invalid means T0" in sec
    # T2 is allowed by the final executor head's code; Helm still writes it only on the manager's written ask, after T1's ~25 trades pass the checks
    assert "T2 is allowed by the code of the final executor head" in sec and "`T2_IMPACT_OK = True`" in sec
    assert "Helm writes `T2` only when the manager asks in writing, after T1's ~25 trades have passed the checks" in sec
    assert "T2 is blocked" not in sec and "before that" not in sec and "refuses T2 buys. The manager says" not in sec
    assert "per tier: 150" in sec and "reset at each change" in sec and "attempts_in_old_tier" in sec and "lifetime_attempts" in sec
    assert "`attempts=<lifetime> (lifetime; 0/150 in T1)`" in sec and "`--status` reads no tier" not in sec  # --status now names the tier and the attempts in it
    # the numbers on the page are the executor's table
    tiers = h5_executor.TIERS
    stake = ", ".join(f"{k} {v['stake_lamports'] / 1e9:.2f}" for k, v in tiers.items())
    assert f"**T0 {tiers['T0']['stake_lamports'] / 1e9:.2f} SOL per trade, T1 {tiers['T1']['stake_lamports'] / 1e9:.2f}, T2 {tiers['T2']['stake_lamports'] / 1e9:.2f}**" in sec, stake
    for k, v in tiers.items():
        row = f"{k} {v['max_open']} / {v['max_trades_per_day']} / {v['daily_loss_lamports'] / 1e9:.2f} / {v['total_loss_lamports'] / 1e9:.2f}"
        assert row in sec, row
    assert "35% of the wallet" in sec and h5_executor.TIER_WALLET_FRAC == 0.35
    # who/where/never/installer
    assert "`/etc/mal-h5/TIER`" in t.split("## What is where")[1].split("`intents_file` is a config value")[0]
    assert "Create `/etc/mal-h5/TIER` with `T0` at go-live" in t.replace("**Create `/etc/mal-h5/TIER` with `T0` at go-live; edit it to step the tier up or down**", "Create `/etc/mal-h5/TIER` with `T0` at go-live")
    assert "never creates, edits or removes `LIVE_OK` or `TIER`" in t and "an existing `/etc/mal-h5/TIER` that is not a regular `root:root` 0644 file" in t
    assert "Never write a higher tier into `/etc/mal-h5/TIER` except on the manager's written ask (for `T2`: after T1's ~25 trades have passed the checks)" in t
    assert "Never write `T2` into" not in t


def test_the_runbook_names_the_final_shadow_head_and_gate():
    t = runbook()
    assert "fe7eb43" not in t and t.count("3dbe1de") >= 3
    assert "#477 head, `3dbe1de` or later" in t and "`base_breaks_unresolved_settled` (the executor's gate)" in t
    assert "| `bad_intent:base_breaks_unresolved_settled` |" in t and "| `s0_before_history` |" in t
    going = t[t.index("## Going live (manager, then Helm)"):t.index("## Step up / step down a tier")]
    assert "3dbe1de or later" in going
    for name in re.findall(r'"(s0_[a-z_]+)"', EXECUTOR_SRC.split("S0_ANCHOR_REASONS = frozenset({")[1].split("})")[0]):
        assert name in dc.S0_REFUSALS, name  # every s0 anchor refusal the executor has is one the check counts
    assert "bad_intent:base_breaks_unresolved_settled" in dc.NEW_FIELD_REFUSALS
