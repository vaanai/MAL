"""Review round 3 of #499 (the two-lens delta review at 26026e1): probe drop-ins and DEC-020 baseline, the watchdog's own failures, stops and
ALERTs reaching Discord, the watch unit's hardening, the installer's pip environment, the feed seen through the unit, `--force`, the daily-check
job command and its exact sudoers lines, watch.env creation, and the runbook NITs. No host, no network, no key."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tools import h5_sell_and_close as sc
from tools.test_h5_daily_check import (DRY_EXEC, H5, NOW, UPTIME, WATCH_SVC, WATCH_TMR, WSVC, WTMR, FakeHost, alerts, cp, dc, go, ledger)
from tools.test_h5_executor_pinned import INSTALL, installer_var
from tools.test_h5_watch import ENV, Poster, hw
from tools.test_h5_watch import go as watch_go
from tools.test_h5_watch import world

ROOT = Path(__file__).resolve().parent.parent
FAST = ROOT / "scripts/mal-fast"
RUNBOOK = (ROOT / "docs/runbooks/h5-executor.md")
EXECUTOR_SRC = (ROOT / "tools/h5_executor.py").read_text()
JOB = ("/usr/bin/python3 -I /home/claude/MAL/scripts/mal-fast/h5-daily-check.py --funded-sol 0.25 --public-rpc --window-hours 24 "
       "--shadow-dir /home/claude/data/h5-shadow --baseline /home/claude/data/h5-daily/probe-state.baseline.json")


def runbook() -> str:
    return RUNBOOK.read_text()


def row(kind, n=1, age_s=600, **kw):
    return [{"kind": kind, "ts_ms": int((NOW - age_s) * 1000), **kw} for _ in range(n)]


def ledger_world(*rows) -> FakeHost:
    h = FakeHost()
    h.files[f"{H5}/live/h5-ledger.jsonl"] = ledger({"kind": "start", "user": dc.WALLET, "ts_ms": int((NOW - 7200) * 1000)}, *rows)
    return h


# --- 1. the probe cannot trade again after Step 11 ------------------------------------------------------------------------------

def test_a_probe_drop_in_or_credential_is_an_alert(tmp_path):
    rc, out = go(FakeHost(), tmp=tmp_path / "clean")
    assert rc == 0 and "holds no drop-in or credential" in out
    h = FakeHost()
    h.probe_props["DropInPaths"] = "/etc/systemd/system/mal-probe-executor.service.d/live.conf"
    rc, out = go(h, tmp=tmp_path / "dropin")
    assert rc == 1 and "ALERT probe_live_dropin" in out and "/root/disabled" in out and "live.conf" in out
    h = FakeHost()
    h.probe_props["LoadCredential"] = "probe-wallet:/etc/mal-probe/probe-wallet.json"
    rc, out = go(h, tmp=tmp_path / "cred")
    assert rc == 1 and "ALERT probe_has_key" in out and "probe-wallet.json" not in out  # the alert says a credential is set, it does not echo it


def test_the_dec020_state_file_is_baselined_and_its_appearance_alerts(tmp_path):
    dec = b'{"attempts": 1, "realized_lamports": -5}'
    h = FakeHost()
    h.files[dc.PROBE_STATE_DEC020] = dec
    rc, out = go(h, tmp=tmp_path / "appeared")  # baseline says absent
    assert rc == 1 and "state-live-dec020.json differs from the baseline" in out
    assert go(FakeHost(), tmp=tmp_path / "absent")[0] == 0
    base = tmp_path / "old-format" / "b.json"
    base.parent.mkdir()
    from tools.test_h5_daily_check import PROBE_STATE

    base.write_text(json.dumps({"sha256": hashlib.sha256(PROBE_STATE).hexdigest()}))  # a baseline written before this change has no DEC-020 hash
    lines: list[str] = []
    rc = dc.main(["--baseline", str(base), "--shadow-dir", "/x"], host=FakeHost(), out=lines.append, now=NOW)
    assert rc == 1 and any("no DEC-020 hash" in l for l in lines)
    # writing: both hashes recorded; each --expect value guards its own file
    h = FakeHost()
    h.files[dc.PROBE_STATE_DEC020] = dec
    b2 = tmp_path / "w"
    rc, out = go(h, "--write-baseline", "--expect-sha256", hashlib.sha256(PROBE_STATE).hexdigest(), "--expect-dec020-sha256", "absent", tmp=b2, baseline=False)
    assert rc == 1 and "--expect-dec020-sha256 does not match" in out and not (b2 / "baseline.json").exists()
    rc, out = go(h, "--write-baseline", "--expect-dec020-sha256", hashlib.sha256(dec).hexdigest().upper(), tmp=b2, baseline=False)
    assert rc == 0 and json.loads((b2 / "baseline.json").read_text())["dec020_sha256"] == hashlib.sha256(dec).hexdigest()
    rc, out = go(FakeHost(), "--write-baseline", "--expect-dec020-sha256", "absent", tmp=tmp_path / "w2", baseline=False)
    assert rc == 0 and json.loads((tmp_path / "w2" / "baseline.json").read_text())["dec020_sha256"] == "absent"


def test_runbook_step_1_moves_the_probe_drop_ins_before_step_11_removes_the_stop():
    t = runbook()
    s1 = t[t.index("**Step 1. Stop and disable"):t.index("**Step 1b.")]
    assert "/root/disabled" in s1 and 'sudo mv "$f" /root/disabled/' in s1 and "mal-probe-executor.service.d/*.conf" in s1
    assert "sudo systemctl daemon-reload" in s1 and "grep -c LoadCredential" in s1 and "DropInPaths --value mal-probe-executor" in s1
    assert "state-live-dec020.json" in s1 and "|| echo absent" in s1
    assert t.index("/root/disabled") < t.index("sudo rm /var/lib/mal-live/STOP")
    assert "moved, not deleted" in t and "probe_live_dropin" in t
    assert "--expect-dec020-sha256" in t


# --- 2. the watchdog's own failures are visible -------------------------------------------------------------------------------

def test_a_failing_watch_service_a_stale_state_and_changed_unit_files_alert(tmp_path):
    def run(name, mut):
        h = FakeHost()
        mut(h)
        return go(h, tmp=tmp_path / name)

    rc, out = go(FakeHost(), tmp=tmp_path / "ok")
    assert rc == 0 and "the watchdog ran 2 min ago" in out
    rc, out = run("failing", lambda h: h.watch_svc.update(Result="exit-code", ExecMainStatus="1"))
    assert "h5_watch_failing" in alerts(out) and "Result=exit-code" in out and "nothing is being posted" in out
    for mins, alert in ((14, False), (16, True), (600, True)):
        rc, out = run(f"ts{mins}", lambda h, m=mins: h.files.__setitem__(dc.WATCH_STATE, json.dumps({"ts": NOW - m * 60}).encode()))
        assert ("h5_watch_stale" in alerts(out)) == alert, (mins, out)
    rc, out = run("nostate", lambda h: h.files.pop(dc.WATCH_STATE))
    assert "h5_watch_stale" in alerts(out) and "never completed a run" in out  # the gate is open in the clean world
    rc, out = run("nostate_closed", lambda h: (h.files.pop(dc.WATCH_STATE), h.files.pop(dc.LIVE_OK), h.modes.pop(dc.LIVE_OK), h.h5_props.update(ActiveState="inactive")))
    assert "h5_watch_stale" not in alerts(out) and "has not completed a run yet" in out
    rc, out = run("badts", lambda h: h.files.__setitem__(dc.WATCH_STATE, b'{"ts": "yesterday"}'))
    assert "h5_watch_stale" in alerts(out)
    # installed unit files and drop-ins
    for name, mut in {
        "service differs": lambda h: h.files.__setitem__(WATCH_SVC, WSVC + b"User=mal-live\n"),
        "timer differs": lambda h: h.files.__setitem__(WATCH_TMR, WTMR.replace(b"5min", b"1s")),
        "service dropin": lambda h: h.watch_svc.update(DropInPaths="/etc/systemd/system/mal-.service.d/override.conf"),
        "timer dropin": lambda h: h.watch_props.update(DropInPaths="/run/systemd/system/mal-h5-watch.timer.d/x.conf"),
        "fragment elsewhere": lambda h: h.watch_svc.update(FragmentPath="/usr/lib/systemd/system/mal-h5-watch.service"),
        "no pinned copy": lambda h: h.files.pop(f"{dc.PINNED}/mal-h5-watch.service"),
    }.items():
        rc, out = run(name.replace(" ", "_"), mut)
        assert "h5_watch_files" in alerts(out), name


def test_systemctl_failing_is_an_alert_and_never_not_installed(tmp_path):
    h = FakeHost()
    h.systemctl_rc = 1
    rc, out = go(h, tmp=tmp_path / "rc1")
    assert rc == 1 and "ALERT systemctl_failed" in out and "is not installed" not in out and "no mal-probe-executor unit is active" not in out
    assert "not 'not installed'" in out or "this is not 'not installed'" in out

    class Empty(FakeHost):
        def systemctl(self, *argv):
            return 0, ""  # exit 0 and nothing on stdout: systemd did not answer

    rc, out = go(Empty(), tmp=tmp_path / "empty")
    assert rc == 1 and "ALERT systemctl_failed" in out and "is not installed" not in out
    h = FakeHost()
    h.h5_props = {"LoadState": "not-found"}  # a truly missing unit still answers
    rc, out = go(h, tmp=tmp_path / "missing")
    assert "is not installed" in out and "systemctl_failed" not in out


def test_host_read_is_capped_at_8_mb_and_reports_unsafe_path(tmp_path, monkeypatch):
    assert dc.MAX_READ == 8 * 1024 * 1024
    f = tmp_path / "big"
    f.write_bytes(b"x" * 20)
    monkeypatch.setattr(dc, "MAX_READ", 10)
    with pytest.raises(dc.Unsafe, match="larger than 10 bytes"):
        dc.Host().read(str(f))
    f.write_bytes(b"x" * 10)
    assert dc.Host().read(str(f)) == b"x" * 10
    h = FakeHost()
    h.unreadable[f"{H5}/live/state-live.json"] = dc.Unsafe(f"{H5}/live/state-live.json is larger than {8 * 1024 * 1024} bytes (9437184)")
    rc, out = go(h, tmp=tmp_path / "alert")
    assert rc == 1 and "ALERT unsafe_path: h5_state" in out and "larger than" in out


def test_the_watch_survives_a_failing_restarts_call_and_prints_a_summary(tmp_path, capsys):
    class NoRestarts(FakeHost):
        def systemctl(self, *argv):
            if "NRestarts" in argv and "--value" in argv:
                raise OSError("systemctl timed out")
            return super().systemctl(*argv)

    p = Poster()
    assert watch_go(NoRestarts(), tmp_path / "s.json", p) == 0 and p.posts == []
    out = capsys.readouterr().out
    assert "h5_watch: unit=mal-h5-executor active=active enabled=enabled feed_age=60s alerts=0 posted=0" in out
    h = world()
    h.systemctl_rc = 1  # systemd does not answer: an alert is posted, and the summary says unknown rather than looking healthy
    p = Poster()
    assert watch_go(h, tmp_path / "s2.json", p) == 0
    assert "ALERT systemctl_failed" in p.posts[0][1] and "unit=unknown active=unknown" in capsys.readouterr().out


def test_the_webhook_is_removed_from_the_environment_after_it_is_read(monkeypatch, tmp_path):
    seen = {}

    def fake_run(host, env, post, state_path, now=None, balance_fn=None):
        seen["env_has"] = env.get(hw.WEBHOOK_VAR)
        seen["os_has"] = os.environ.get(hw.WEBHOOK_VAR)
        return 0

    monkeypatch.setenv(hw.WEBHOOK_VAR, ENV[hw.WEBHOOK_VAR])
    monkeypatch.setenv("STATE_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(hw, "run", fake_run)
    assert hw.main([]) == 0
    assert seen == {"env_has": ENV[hw.WEBHOOK_VAR], "os_has": None}  # run() has it; no child process (systemctl, stat, dd, true) can inherit it
    assert "os.environ.pop(WEBHOOK_VAR" in (FAST / "h5-watch.py").read_text()


def test_the_watch_runs_under_python_dash_I_dash_S_without_site():
    watch, daily = FAST / "h5-watch.py", FAST / "h5-daily-check.py"
    r = subprocess.run([sys.executable, "-I", "-S", "-B", str(watch), "--test-message"], capture_output=True, text=True, env={"PATH": os.environ["PATH"]})
    assert r.returncode == 1 and "not a Discord webhook URL" in r.stderr and "Traceback" not in r.stderr  # imports (urllib, json, importlib) work without site
    code = ("import importlib.util as u, sys; s = u.spec_from_file_location('d', sys.argv[1]); m = u.module_from_spec(s); s.loader.exec_module(m); "
            "c = u.spec_from_file_location('c', sys.argv[2]); k = u.module_from_spec(c); c.loader.exec_module(k); print(m.WALLET, k.SHADOW_DEST)")
    r = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(daily), str(FAST / "check-h5-unit.py")], capture_output=True, text=True)
    assert r.returncode == 0 and dc.WALLET in r.stdout, r.stderr  # the daily-check engine and the checker load without site too
    src = daily.read_text()
    assert '["/usr/bin/systemctl", *argv]' in src and '["systemctl"' not in src  # not resolved through systemd's PATH (/usr/local first)


# --- 3. stops and ALERT rows reach Discord (DEC-024 section 8) ----------------------------------------------------------------

def test_budget_stops_and_executor_alert_rows_are_alerts_and_reach_discord(tmp_path):
    for name in dc.BUDGET_STOPS:
        assert f'"{name}"' in EXECUTOR_SRC, name  # every budget-stop reason is one the executor really writes
    for name in dc.BUDGET_STOPS:
        h, p = ledger_world(*row("skip", 3, reason=name)), Poster()
        watch_go(h, tmp_path / f"{name}.json", p)
        assert p.posts and f"ALERT h5_budget_stop_{name}" in p.posts[0][1] and "3 trigger(s)" in p.posts[0][1], name
    for what in ("bad_intent_rate", "out_of_rule_entry", "zero_token_balance", "unsafe_tx_refused"):
        h, p = ledger_world(*row("alert", 2, alert=what)), Poster()
        watch_go(h, tmp_path / f"{what}.json", p)
        assert p.posts and f"ALERT h5_executor_alert_{what}: the executor wrote 2 ALERT {what} row(s)" in p.posts[0][1], what
    # old rows are not re-announced, and hostile names are not printed
    rc, out = go(ledger_world(*row("skip", 5, reason="total_loss_stop", age_s=7 * 3600)), tmp=tmp_path / "old")
    assert "h5_budget_stop_total_loss_stop" not in out
    rc, out = go(ledger_world(*row("alert", 1, alert="SECRET value\nwith spaces")), tmp=tmp_path / "hostile")
    assert "SECRET" not in out
    rc, out = go(ledger_world(*row("skip", 1, reason="max_trades_day", age_s=20 * 3600)), "--window-hours", "24", tmp=tmp_path / "daily-window")
    assert "h5_budget_stop_max_trades_day" in alerts(out)  # the daily job looks back 24 h


# --- 6. the feed as the unit sees it ----------------------------------------------------------------------------------------

def test_a_shadow_directory_born_after_the_units_run_started_is_a_stale_bind(tmp_path):
    start = NOW - UPTIME + (UPTIME - 3600)  # the fake unit's run began one hour ago
    for name, birth, expect in (("older", NOW - 86400, False), ("at start", start + 2, False), ("recreated", start + 600, True), ("just now", NOW - 30, True)):
        h = FakeHost()
        h.birth_t = birth
        rc, out = go(h, tmp=tmp_path / name.replace(" ", "_"))
        assert ("h5_feed_bind_stale" in alerts(out)) == expect, (name, out)
    assert "nsenter" in out
    h = FakeHost()
    h.birth_t = None
    rc, out = go(h, tmp=tmp_path / "unknown")
    assert "h5_feed_bind_stale" not in alerts(out) and "bind was not compared" in out
    h = FakeHost()
    h.birth_t = NOW - 30
    h.h5_props.update(ActiveState="inactive")  # no current run: nothing to compare against
    assert "h5_feed_bind_stale" not in alerts(go(h, tmp=tmp_path / "stopped")[1])
    t = runbook()
    assert "h5_feed_bind_stale" in t and "birth time" in t and "CAP_SYS_ADMIN" in t and "repeat the Step 8 `nsenter` lines" in t


# --- 5. the installer's pip environment ---------------------------------------------------------------------------------------

def _pip_ok(tmp_path: Path, requirements: str, freeze: str) -> bool:
    t = INSTALL.read_text()
    func = t[t.index("pip_set_ok() {"):t.index("# The venv is rebuilt in place")]
    (tmp_path / "req").write_text(requirements)
    (tmp_path / "freeze").write_text(freeze)
    r = subprocess.run(["bash", "-c", f"set -euo pipefail\n{func}\npip_set_ok {tmp_path}/req {tmp_path}/freeze"], capture_output=True, text=True)
    return r.returncode == 0


REQ = (FAST / "requirements-probe-exec.txt").read_text()
GOOD = "pip==24.0\nsolders==0.21.0\njsonalias==0.1.1\ntyping-extensions==4.12.2\n"


def test_pip_runs_in_a_clean_environment_and_the_installed_set_must_match(tmp_path):
    t = INSTALL.read_text()
    assert "CLEAN_ENV=(env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin PIP_CONFIG_FILE=/dev/null)" in t
    assert '"${CLEAN_ENV[@]}" /usr/bin/python3 -I -m venv "$VENV_NEW"' in t
    assert '"${CLEAN_ENV[@]}" "$VENV_NEW/bin/python" -I -m pip install --quiet --require-hashes --only-binary=:all: --no-deps' in t
    assert '"${CLEAN_ENV[@]}" "$VENV_NEW/bin/python" -I -m pip list --format=freeze' in t
    assert not re.search(r'^(?!.*CLEAN_ENV).*"\$VENV_NEW/bin/python" -I -m pip', t, re.M)  # no pip call outside the clean environment
    assert t.index("pip list --format=freeze") < t.index('"$CHECK" "$STAGE" "$VENV_NEW"') < t.index('mv -T "$STAGE" "$DEST/$COMMIT"')  # checked before anything moves
    reqs = re.findall(r"^(\S+==\S+) \\\n\s+--hash=sha256:", REQ, re.M)
    assert [r.split("==")[0] for r in reqs] == ["solders", "jsonalias", "typing_extensions"]
    pins = "\n".join(f"{r.split('==')[0].replace('_', '-')}=={r.split('==')[1]}" for r in reqs)
    # names are compared ignoring case and _ . -; pip and setuptools are allowed; any extra or missing distribution fails
    assert _pip_ok(tmp_path, REQ, pins + "\npip==24.0\n")
    assert _pip_ok(tmp_path, REQ, "\n".join(r.upper() for r in reqs) + "\npip==24.0\nsetuptools==69.0.0\n")
    assert not _pip_ok(tmp_path, REQ, pins + "\npip==24.0\nevil==1.0\n")
    assert not _pip_ok(tmp_path, REQ, "\n".join(reqs[:-1]) + "\npip==24.0\n")
    assert not _pip_ok(tmp_path, REQ, "\n".join([*reqs[:-1], reqs[-1].split("==")[0] + "==0.0.1"]) + "\n")  # a different version
    assert not _pip_ok(tmp_path, "", "pip==24.0\n")  # an empty requirements file never passes
    assert not _pip_ok(tmp_path, REQ, "")


def test_a_polluted_pip_environment_cannot_reach_the_venv_build():
    """The behaviour that matters, run for real: env -i drops PIP_* from the caller and PIP_CONFIG_FILE=/dev/null hides every config file."""
    t = INSTALL.read_text()
    arr = re.search(r"^CLEAN_ENV=\((.*)\)$", t, re.M).group(1)
    r = subprocess.run(["bash", "-c", f'CLEAN_ENV=({arr}); "${{CLEAN_ENV[@]}}" /usr/bin/env'], capture_output=True, text=True,
                       env={"PATH": os.environ["PATH"], "PIP_REQUIREMENT": "/tmp/evil.txt", "PIP_FIND_LINKS": "/tmp/evil", "PIP_INDEX_URL": "http://evil", "HOME": "/root"})
    assert sorted(r.stdout.split()) == ["PATH=/usr/sbin:/usr/bin:/sbin:/bin", "PIP_CONFIG_FILE=/dev/null"]


# --- 7. --force ---------------------------------------------------------------------------------------------------------------

def test_the_refusal_message_does_not_suggest_force_and_the_flag_is_documented_as_helm_only():
    src = Path(sc.__file__).read_text()
    refusal = next(l for l in src.splitlines() if "is not stopped" in l and "Refuse" in l)
    assert "--force" not in refusal and "stop it first" in refusal
    assert "HELM ONLY" in src
    t = runbook()
    flags = t[t.index("| Flag | Meaning |"):t.index("The tool refuses while either executor unit's ActiveState")]
    force = next(l for l in flags.splitlines() if l.startswith("| `--force`"))
    assert "Helm only" in force and "ActiveState" in force and "inactive" in force and "activating" in force
    assert "Never pass `--force`" in t
    with pytest.raises(sc.Refuse) as e:
        import argparse

        sc.run(argparse.Namespace(mint="A" * 43, keyfile="/none", rpc_env="/none", slippage_bps=None, emergency=False, priority_lamports=1, send=False, force=False),
               lambda *a: None, is_active=lambda u: True, out=lambda s: None, check_location=False)
    assert "--force" not in str(e.value.code)


# --- 8. the daily-check job and its sudoers lines -------------------------------------------------------------------------------

def test_the_runbook_has_the_exact_daily_check_job_command():
    t = runbook()
    s = t[t.index("**Step 10b."):t.index("**Step 11. Create")]
    assert JOB in s and JOB in t.split("**Step 11. Create")[1]  # the same command, with the same paths, in Step 10b and in Going live
    argv = shlex.split(JOB)
    assert argv[:3] == ["/usr/bin/python3", "-I", "/home/claude/MAL/scripts/mal-fast/h5-daily-check.py"]
    args = dc.build_parser().parse_args(argv[3:])  # the command parses with the script's own parser
    assert args.funded_sol == 0.25 and args.public_rpc and args.window_hours == 24 and args.shadow_dir.startswith("/home/claude/")
    assert args.baseline == "/home/claude/data/h5-daily/probe-state.baseline.json"
    assert subprocess.run(["sh", "-n", "-c", JOB]).returncode == 0  # plain sh: it is a MiScusi job
    assert "user `claude`" in s and "under `sh`" in s and "absolute paths only" in s
    assert "--baseline" in t.split("**Step 11. Create")[1].split("## Stop, halt, status")[0]


def allowed_sudo_calls() -> set[tuple[str, ...]]:
    return {("/usr/bin/true",), *(dc.stat_argv(p) for p in dc.PRIV_STAT), *(dc.dd_argv(p) for p in dc.PRIV_READ)}


def parse_sudoers(text: str) -> set[tuple[str, ...]]:
    body = text.split("=", 1)[1].split("\nclaude ALL=")[0]
    cmds = [c.strip() for c in re.split(r",\s*\\\n", body)]
    return {tuple(re.sub(r"\\([,:=\\])", r"\1", tok) for tok in c.split(" ")) for c in cmds}


def test_the_sudoers_lines_are_exactly_the_privileged_calls_with_no_wildcard():
    text = dc.sudoers_text("claude")
    assert parse_sudoers(text) == allowed_sudo_calls()
    assert "*" not in text and "ALL=(root) NOPASSWD: MAL_H5_CHECK" in text and "claude ALL=(root)" in text
    assert "/usr/bin/cat" not in text and "skip=" not in text and "of=" not in text
    assert all(re.fullmatch(r"if\\=/[A-Za-z0-9_./-]+", a) for c in parse_sudoers(text) for a in [x.replace("=", "\\=", 1) for x in c if x.startswith("if=")])
    # the lines on the runbook page are the ones the code prints
    t = runbook()
    block = t[t.index("Cmnd_Alias MAL_H5_CHECK"):]
    block = block[:block.index("```")].rstrip()
    assert block == text.rstrip()
    lines: list[str] = []
    assert dc.main(["--print-sudoers"], out=lines.append) == 0 and "\n".join(lines) == text.rstrip()
    assert "visudo -f /etc/sudoers.d/mal-h5-daily-check" in t and "visudo -c" in t and "never a `dd` rule with `*`" in t.replace("Never a `dd` rule with `*`", "never a `dd` rule with `*`")


def test_every_sudo_call_a_full_run_makes_is_one_the_sudoers_lines_allow(tmp_path, monkeypatch):
    calls: list[tuple[str, ...]] = []
    real_lstat, real_open = os.lstat, os.open
    locked = ("/var/lib/mal-live", "/var/lib/mal-h5-watch")

    def lstat(path, *a, **k):
        if str(path).startswith(locked):
            raise PermissionError(path)
        return real_lstat(path, *a, **k)

    def open_(path, *a, **k):
        if str(path).startswith(locked):
            raise PermissionError(path)
        return real_open(path, *a, **k)

    monkeypatch.setattr(dc.os, "lstat", lstat)
    monkeypatch.setattr(dc.os, "open", open_)
    fake = FakeHost()

    class Recording(dc.Host):
        def _sudo(self, *argv):
            calls.append(argv)
            if argv[0] == "/usr/bin/true":
                return cp(0)
            if argv[0] == "/usr/bin/stat":
                path = argv[-1]
                if path in (dc.WALLET_STOP, dc.WALLET_HALT, f"{H5}/STOP", f"{H5}/HALT", f"{H5}/LIVE_OK", dc.PROBE_STATE_DEC020):
                    return cp(1, b"", b"/usr/bin/stat: cannot statx: No such file or directory\n")
                kind, mode = ("directory", "700") if path == H5 else ("regular file", "600")
                return cp(0, f"{kind}|1|mal-live|mal-live|{mode}|{int(NOW)}|40\n".encode())
            if argv[0] == "/usr/bin/dd":
                return cp(0, json.dumps({"ts": NOW - 60, "attempts": 0, "open": {}, "pending": {}}).encode() + b"\n")
            raise AssertionError(argv)

        def systemctl(self, *argv):
            return fake.systemctl(*argv)

        def birth(self, path):
            return fake.birth(path)

        def uptime(self):
            return UPTIME

        def newest_hourly(self, directory):
            return fake.feed

    args = dc.build_parser().parse_args(["--funded-sol", "0.25", "--public-rpc", "--shadow-dir", "/nowhere", "--baseline", str(tmp_path / "b.json"), "--window-hours", "24"])
    dc.run_checks(args, Recording(), lambda s: None, lambda w, e: 249_000_000, NOW)
    assert len(calls) > 8
    assert set(calls) <= allowed_sudo_calls(), set(calls) - allowed_sudo_calls()
    assert {c[-1] for c in calls if c[0] == "/usr/bin/stat"} >= {dc.PROBE_STATE, dc.WATCH_STATE, f"{H5}/live/h5-ledger.jsonl"}
    assert {c[-1].removeprefix("if=") for c in calls if c[0] == "/usr/bin/dd"} >= {dc.PROBE_STATE, f"{H5}/live/state-live.json"}


# --- 9. watch.env ---------------------------------------------------------------------------------------------------------------

def test_watch_env_is_created_with_install_and_sudoedit_and_never_echoed():
    t = runbook()
    s = t[t.index("**Step 10. Auditd and the watchdog"):t.index("**Step 10b.")]
    a = s.index("sudo install -m 0600 -o root -g root /dev/null /etc/mal-h5-watch/watch.env")
    b = s.index("sudoedit /etc/mal-h5-watch/watch.env")
    assert s.index("sudo install -d -m 0700 -o root -g root /etc/mal-h5-watch") < a < b < s.index("sudo stat -c '%U:%G %a %n' /etc/mal-h5-watch")
    in_code = False
    for line in t.splitlines():  # in the commands (the fenced blocks), the webhook never meets echo, tee, printf or cat
        if line.startswith("```"):
            in_code = not in_code
        elif in_code and "H5_WATCH_DISCORD_WEBHOOK" in line:
            assert not re.search(r"\b(echo|tee|printf|cat)\b", line), line
    assert not re.search(r"tee\s+\S*watch\.env", t) and "<<" not in s
    assert "no comments, no quotes and no spaces" in s and "never with `echo`, `tee`, a here-document or on any command line" in s
    assert "sudo wc -l < /etc/mal-h5-watch/watch.env" in s and "expect: 3" in s
    # none of the three example lines carries an inline comment
    assert not re.search(r"H5_WATCH_[A-Z_]+=\S*\s+#", t)


# --- NITs -----------------------------------------------------------------------------------------------------------------------

def test_runbook_nits_sudo_on_home_paths_stale_lines_and_the_journal_warning():
    t = runbook()
    s7 = t[t.index("**Step 7."):t.index("**Step 8.")]
    assert "sudo stat -c '%U:%G %a %n' \"$SHADOW\"" in s7 and "sudo ls -l \"$SHADOW\"" in s7
    assert not re.search(r"^(stat|ls) .*\"\$SHADOW\"", t, re.M) and 'sudo ls -ld "$SHADOW"' in t
    assert "signals_absent" not in t and "grep signals_file_missing" in t and "there is no ledger row for it" in t
    assert "chmod 755 ~/data/h5-shadow" in t and "-rw-r--r--" in t
    assert not re.search(r"missing `/var/lib/mal-live/STOP` is", t) and "STOP must exist" not in t.replace("The old \"STOP must exist\" alert is gone", "")
    # the Wind-down lister reads the state file with dd nofollow, not with a python open() that follows symlinks
    wd = t[t.index("## Wind-down"):t.index("## Helm's steps")]
    assert "dd iflag=nofollow status=none if=/var/lib/mal-live/h5/live/state-live.json | python3 -I -c" in wd and "json.load(open(" not in wd
    # the daily-check alert no longer says mask
    assert "mask it" not in (FAST / "h5-daily-check.py").read_text()
    # the watch-unit install lines are in Step 1b, Step 10 and the rollback row
    line = "sudo install -m 0644 -o root -g root /usr/local/lib/mal-h5-exec/current/mal-h5-watch.service /etc/systemd/system/mal-h5-watch.service"
    assert t.count(line) == 3
    assert line in t[t.index("**Step 1b."):t.index("**Step 2.")] and line in t[t.index("## Rollback"):t.index("## Auditd")]
