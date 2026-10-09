"""Tests for scripts/mal-fast/h5-daily-check.py: a fake host (files, systemctl, getBalance) for the checks, and the real Host class
against a temp dir and a recording fake of sudo for the reads. No sudo, no network, no key."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FAST = ROOT / "scripts/mal-fast"
spec = importlib.util.spec_from_file_location("h5_daily_check", FAST / "h5-daily-check.py")
dc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dc)

BASE = (FAST / "mal-h5-executor.service").read_bytes()
DROPIN = (FAST / "mal-h5-executor-live-pinned.conf").read_bytes()
FEED = (FAST / "mal-h5-executor-shadow-feed.conf").read_text().replace("__SHADOW_DIR__", "/home/claude/data/h5-shadow").encode()
PROBE_STATE = json.dumps({"attempts": 62, "realized_lamports": -210_754_990, "open": {}, "pending": {}}).encode()
FUNDED = 250_000_000
NOW = 1_800_000_000.0
H5 = dc.H5_DIR
LIVE_EXEC = ("{ path=/usr/local/lib/mal-h5-exec/venv/bin/python ; argv[]=/usr/local/lib/mal-h5-exec/venv/bin/python -I -B -u "
             "/usr/local/lib/mal-h5-exec/current/launcher.py --config /usr/local/lib/mal-h5-exec/current/h5-executor-live.json --live }")
DRY_EXEC = LIVE_EXEC.replace("h5-executor-live.json --live", "h5-executor.json")


def ledger(*rows) -> bytes:
    return "".join(json.dumps(r) + "\n" for r in rows).encode()


class FakeHost(dc.Host):
    def __init__(self):
        self.files: dict[str, bytes] = {
            dc.PROBE_STATE: PROBE_STATE, dc.PROBE_STOP: b"",
            f"{dc.PINNED}/mal-h5-executor.service": BASE, f"{dc.PINNED}/mal-h5-executor-live-pinned.conf": DROPIN,
            dc.UNIT_FILE: BASE, dc.DROPIN_LIVE: DROPIN, dc.DROPIN_FEED: FEED, dc.LIVE_OK: b"",
            f"{H5}/live/state-live.json": json.dumps({"attempts": 3, "realized_lamports": -1_000_000, "open": {}, "pending": {}}).encode(),
            f"{H5}/live/h5-counters.json": json.dumps({"halts": {}, "sells_landed": 3, "sells_late": 0}).encode(),
            f"{H5}/live/h5-ledger.jsonl": ledger({"kind": "start", "user": dc.WALLET, "ts_ms": int((NOW - 7200) * 1000)},
                                                 {"kind": "decision", "ts_ms": int((NOW - 600) * 1000)}),
        }
        self.modes = {H5: "mal-live:mal-live:700", dc.H5_ETC: "root:root:755", dc.LIVE_OK: "root:root:644"}
        self.mtimes = {dc.LIVE_OK: NOW - 7200}
        self.links: set[str] = set()
        self.irregular: set[str] = set()
        self.unreadable: dict[str, Exception] = {}
        self.unit_files = "mal-probe-executor.service masked -\n"
        self.units = ""
        self.h5_props = {"LoadState": "loaded", "ActiveState": "active", "SubState": "running", "UnitFileState": "enabled", "NRestarts": "0",
                         "Result": "success", "FragmentPath": dc.UNIT_FILE}
        self.watch_props = {"ActiveState": "active", "UnitFileState": "enabled"}
        self.dropins = [dc.DROPIN_LIVE, dc.DROPIN_FEED]
        self.execstart = LIVE_EXEC
        self.balance_lamports = FUNDED - 1_000_000
        self.feed = ("h5-shadow-2026-10-09T14.jsonl", NOW - 60)
        self.sudo = True

    def sudo_ok(self):
        return self.sudo

    def read(self, path, tail=None):
        if path in self.unreadable:
            raise self.unreadable[path]
        return self.files.get(path)

    def exists(self, path):
        return path in self.files or path in self.modes

    def islink(self, path):
        return path in self.links

    def is_regular(self, path):
        return path in self.files and path not in self.links and path not in self.irregular

    def stat(self, path):
        return self.modes.get(path)

    def mtime(self, path):
        return self.mtimes.get(path)

    def newest_hourly(self, directory):
        return self.feed

    def systemctl(self, *argv):
        if argv[0] == "list-unit-files":
            return 0, self.unit_files
        if argv[0] == "list-units":
            return 0, self.units
        if argv[1] == dc.WATCH_TIMER:
            return 0, "".join(f"{k}={v}\n" for k, v in self.watch_props.items())
        if "DropInPaths" in argv:
            return 0, " ".join(self.dropins) + "\n"
        if "NRestarts" in argv and "--value" in argv:
            return 0, self.h5_props["NRestarts"] + "\n"
        if "--value" in argv:
            return 0, self.execstart
        return 0, "".join(f"{k}={v}\n" for k, v in self.h5_props.items())


def go(host, *extra, tmp, baseline=True, balance=None, now=NOW):
    tmp.mkdir(parents=True, exist_ok=True)
    base = tmp / "baseline.json"
    if baseline and not base.exists():
        base.write_text(json.dumps({"sha256": hashlib.sha256(PROBE_STATE).hexdigest()}))
    lines: list[str] = []
    rc = dc.main(["--funded-sol", "0.25", "--baseline", str(base), "--shadow-dir", "/nowhere", *extra], host=host, out=lines.append,
                 balance_fn=balance or (lambda w, e: host.balance_lamports), now=now)
    return rc, "\n".join(lines)


def alerts(out: str) -> list[str]:
    return re.findall(r"^ALERT (\w+):", out, re.M)


def test_clean_world_has_no_alerts_and_h5_is_allowed(tmp_path):
    h = FakeHost()
    rc, out = go(h, tmp=tmp_path)
    assert rc == 0 and "ALERTS=0" in out, out
    assert "mal-h5-executor active=active/running" in out and "files: STOP=no HALT=no LIVE_OK=yes" in out
    assert "no mal-probe-executor unit is active or enabled" in out and "probe state unchanged" in out
    assert "gap +0.000000" in out and "shadow feed fresh" in out


def test_probe_unit_enabled_or_active_alerts_masked_does_not(tmp_path):
    h = FakeHost()
    for files, units in (("mal-probe-executor.service enabled enabled\n", ""), ("mal-probe-executor.timer enabled-runtime -\n", ""),
                         ("", "mal-probe-executor.service loaded active running desc\n"),
                         ("", "mal-probe-executor.service loaded activating start desc\n")):
        h.unit_files, h.units = files, units
        rc, out = go(h, tmp=tmp_path)
        assert rc == 1 and "ALERT probe_unit" in out, (files, units)
    assert "docs/runbooks/h5-executor.md step 1" in out and "mask" not in out  # the runbook says not to mask it
    h.unit_files, h.units = "mal-probe-executor.service masked -\n", "mal-probe-executor.service loaded inactive dead desc\n"
    rc, out = go(h, tmp=tmp_path)
    assert rc == 0 and alerts(out) == []


def test_probe_state_changes_alert(tmp_path):
    cases = {
        "attempts": {"attempts": 63, "realized_lamports": -210_754_990, "open": {}, "pending": {}},
        "realized": {"attempts": 62, "realized_lamports": -210_000_000, "open": {}, "pending": {}},
        "open": {"attempts": 62, "realized_lamports": -210_754_990, "open": {"m": {}}, "pending": {}},
        "hash": {"attempts": 62, "realized_lamports": -210_754_990, "open": {}, "pending": {}, "extra": 1},  # same facts, different bytes
    }
    for name, state in cases.items():
        h = FakeHost()
        h.files[dc.PROBE_STATE] = json.dumps(state).encode()
        rc, out = go(h, tmp=tmp_path / name)
        assert rc == 1 and "ALERT probe_state" in out, name
    h = FakeHost()
    del h.files[dc.PROBE_STOP]
    rc, out = go(h, tmp=tmp_path / "stop")
    assert rc == 1 and "STOP is missing" in out
    del h.files[dc.PROBE_STATE]
    rc, out = go(h, tmp=tmp_path / "gone")
    assert rc == 1 and "state-live.json is missing" in out


def test_baseline_missing_alerts_then_write_baseline_fixes_it_and_expect_sha_guards_it(tmp_path):
    h = FakeHost()
    rc, out = go(h, tmp=tmp_path, baseline=False)
    assert rc == 1 and "no baseline" in out
    rc, out = go(h, "--write-baseline", "--expect-sha256", "0" * 64, tmp=tmp_path, baseline=False)
    assert rc == 1 and "NOT written" in out and not (tmp_path / "baseline.json").exists()  # changed since Helm's Step 1
    rc, out = go(h, "--write-baseline", "--expect-sha256", hashlib.sha256(PROBE_STATE).hexdigest().upper(), tmp=tmp_path, baseline=False)
    assert rc == 0 and "baseline written" in out
    rc, out = go(h, tmp=tmp_path, baseline=False)
    assert rc == 0


def test_h5_unit_files_must_equal_the_pinned_copies_and_drop_ins_come_from_systemd(tmp_path):
    muts = {
        "base": lambda h: h.files.__setitem__(dc.UNIT_FILE, BASE + b"User=root\n"),
        "live": lambda h: h.files.__setitem__(dc.DROPIN_LIVE, DROPIN + b"Environment=LD_PRELOAD=/x\n"),
        "bad feed": lambda h: h.files.__setitem__(dc.DROPIN_FEED, b"[Service]\nBindReadOnlyPaths=-/etc:/srv/mal-h5-shadow\n"),
        "unexpected dropin": lambda h: h.dropins.append(f"{dc.DROPIN_DIR}/zz.conf"),
        "prefix dropin": lambda h: h.dropins.append("/etc/systemd/system/mal-.service.d/override.conf"),
        "toplevel dropin": lambda h: h.dropins.append("/etc/systemd/system/service.d/x.conf"),
        "run dropin": lambda h: h.dropins.append("/run/systemd/system/mal-h5-executor.service.d/x.conf"),
        "fragment elsewhere": lambda h: h.h5_props.__setitem__("FragmentPath", "/usr/lib/systemd/system/mal-h5-executor.service"),
        "no pinned": lambda h: h.files.pop(f"{dc.PINNED}/mal-h5-executor.service"),
    }
    for name, mut in muts.items():
        h = FakeHost()
        mut(h)
        rc, out = go(h, tmp=tmp_path / name.replace(" ", "_"))
        assert rc == 1 and "ALERT h5_unit_files" in out, name
    h = FakeHost()
    h.execstart = "{ argv[]=/var/lib/mal/fast-forward/venv/bin/python -m tools.h5_executor }"
    rc, out = go(h, tmp=tmp_path / "exec")
    assert rc == 1 and "not the pinned launcher" in out


def test_alerts_never_echo_file_content(tmp_path):
    h = FakeHost()
    h.files[dc.DROPIN_FEED] = b"[Service]\nBindReadOnlyPaths=-/etc/SECRETLINE:/srv/mal-h5-shadow\nUser=SECRETUSER\n"
    h.files[dc.DROPIN_LIVE] = DROPIN + b"Environment=SECRETVALUE=1\n"
    h.files[dc.UNIT_FILE] = BASE + b"# SECRETCOMMENT\n"
    rc, out = go(h, tmp=tmp_path)
    assert rc == 1 and "ALERT h5_unit_files" in out and "SECRET" not in out


def test_h5_unit_failed_and_not_installed(tmp_path):
    h = FakeHost()
    h.h5_props["ActiveState"] = "failed"
    h.h5_props["Result"] = "exit-code"
    rc, out = go(h, tmp=tmp_path / "f")
    assert rc == 1 and "ALERT h5_unit_failed" in out
    h = FakeHost()
    h.h5_props = {"LoadState": "not-found"}
    rc, out = go(h, tmp=tmp_path / "n")
    assert "is not installed" in out and "ALERT h5_unit" not in out


def test_h5_state_alerts(tmp_path):
    def with_(mut):
        h = FakeHost()
        mut(h)
        return go(h, tmp=tmp_path / str(len(list(tmp_path.iterdir()))))

    rc, out = with_(lambda h: h.files.__setitem__(f"{H5}/HALT", b""))
    assert rc == 1 and "ALERT h5_halt_file" in out
    rc, out = with_(lambda h: h.files.__setitem__(dc.WALLET_HALT, b""))
    assert rc == 1 and "ALERT wallet_halt_file" in out
    rc, out = with_(lambda h: h.files.__setitem__(f"{H5}/live/h5-counters.json", json.dumps({"halts": {"stuck_position": {}}}).encode()))
    assert rc == 1 and "ALERT h5_live_halt: latched: stuck_position" in out
    st = {"attempts": 4, "realized_lamports": 0, "open": {"M": {"abandoned": True, "buy_cost_lamports": 20_000_000}}, "pending": {}}
    rc, out = with_(lambda h: h.files.__setitem__(f"{H5}/live/state-live.json", json.dumps(st).encode()))
    assert "ALERT h5_stuck_position" in out
    rc, out = with_(lambda h: h.modes.__setitem__(H5, "mal-live:mal-live:755"))
    assert rc == 1 and "ALERT h5_dir_mode" in out
    other = json.dumps({"kind": "start", "user": "Fakexxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"}) + "\n"
    rc, out = with_(lambda h: h.files.__setitem__(f"{H5}/live/h5-ledger.jsonl", other.encode()))
    assert rc == 1 and "ALERT h5_wallet_mismatch" in out
    rc, out = with_(lambda h: h.unreadable.__setitem__(f"{H5}/live/state-live.json", RuntimeError("x")))
    assert rc == 1 and "ALERT check_failed" in out  # a check that cannot run is an alert, never a silent pass


def test_positions_without_a_live_unit_are_an_alert(tmp_path):
    """M1: after a bare stop or a switch to dry run, an open live position has no seller and nothing else would say so."""
    open_ = {"M": {"buy_cost_lamports": 22_000_000, "spend": 20_000_000}}
    for name, mut, expect in (
        ("open, unit stopped", lambda h: h.h5_props.update(ActiveState="inactive"), True),
        ("open, dry-run ExecStart", lambda h: setattr(h, "execstart", DRY_EXEC), True),
        ("open, unit not installed", lambda h: setattr(h, "h5_props", {"LoadState": "not-found"}), True),
        ("open, unit failed", lambda h: h.h5_props.update(ActiveState="failed"), True),
        ("open, unit activating", lambda h: h.h5_props.update(ActiveState="activating"), True),
        ("open, live unit active", lambda h: None, False),
    ):
        h = FakeHost()
        h.files[f"{H5}/live/state-live.json"] = json.dumps({"attempts": 4, "realized_lamports": -1_000_000, "open": open_, "pending": {}}).encode()
        h.balance_lamports = FUNDED - 1_000_000 - 22_000_000
        mut(h)
        rc, out = go(h, tmp=tmp_path / name.replace(" ", "_").replace(",", ""))
        assert ("h5_positions_unmanaged" in alerts(out)) == expect, (name, out)
    h = FakeHost()  # pending only
    h.files[f"{H5}/live/state-live.json"] = json.dumps({"attempts": 4, "realized_lamports": 0, "open": {}, "pending": {"M": {"kind": "sell"}}}).encode()
    h.h5_props.update(ActiveState="inactive")
    assert "h5_positions_unmanaged" in alerts(go(h, tmp=tmp_path / "pending")[1])
    msg = next(m for n, m in dc.run_checks(dc.build_parser().parse_args(["--shadow-dir", "/x"]), h, lambda s: None, lambda w, e: 0, NOW).alert_list
               if n == "h5_positions_unmanaged")
    assert "sell-and-close" in msg and "1 pending" in msg


def test_idle_canary_alerts(tmp_path):
    def run(name, mut):
        h = FakeHost()
        mut(h)
        return go(h, tmp=tmp_path / name)

    # the shadow feed
    rc, out = run("stale", lambda h: setattr(h, "feed", ("h5-shadow-2026-10-09T14.jsonl", NOW - 11 * 60)))
    assert "h5_feed_stale" in alerts(out) and "11 min ago" in out
    rc, out = run("fresh_edge", lambda h: setattr(h, "feed", ("h5-shadow-2026-10-09T14.jsonl", NOW - 9 * 60)))
    assert rc == 0
    rc, out = run("nofeed", lambda h: setattr(h, "feed", None))
    assert "h5_feed_stale" in alerts(out)
    # the unit, once the gate is open
    rc, out = run("inactive", lambda h: h.h5_props.update(ActiveState="inactive"))
    assert "h5_idle" in alerts(out) and "unit is inactive" in out
    rc, out = run("disabled", lambda h: h.h5_props.update(UnitFileState="disabled"))
    assert "h5_idle" in alerts(out) and "not enabled" in out
    rc, out = run("dryrun", lambda h: setattr(h, "execstart", DRY_EXEC))
    assert "h5_idle" in alerts(out) and "no --live" in out
    rc, out = run("stop", lambda h: h.files.__setitem__(f"{H5}/STOP", b""))
    assert "h5_idle" in alerts(out) and "STOP exists" in out
    # the gate closed: idle by design, no idle alert (the unit may be stopped during the install steps)
    def closed(h):
        h.files.pop(dc.LIVE_OK), h.modes.pop(dc.LIVE_OK), h.h5_props.update(ActiveState="inactive")

    rc, out = run("gate_closed", closed)
    assert "h5_idle" not in alerts(out) and "canary idle by design" in out
    # LIVE_OK old, and no buy/skip/decision row in 6 h
    def silent(h):
        h.mtimes[dc.LIVE_OK] = NOW - 7 * 3600
        h.files[f"{H5}/live/h5-ledger.jsonl"] = ledger({"kind": "start", "user": dc.WALLET, "ts_ms": int((NOW - 7 * 3600) * 1000)},
                                                         {"kind": "sell", "ts_ms": int((NOW - 60) * 1000)}, {"kind": "decision", "ts_ms": int((NOW - 7 * 3600) * 1000)})

    rc, out = run("silent", silent)
    assert "h5_idle_ledger" in alerts(out)
    rc, out = run("talking", lambda h: (silent(h), h.files.__setitem__(f"{H5}/live/h5-ledger.jsonl", h.files[f"{H5}/live/h5-ledger.jsonl"] + ledger({"kind": "skip", "ts_ms": int((NOW - 3600) * 1000)}))))
    assert "h5_idle_ledger" not in alerts(out)
    rc, out = run("young_gate", lambda h: (silent(h), h.mtimes.__setitem__(dc.LIVE_OK, NOW - 3600))[0])
    assert "h5_idle_ledger" not in alerts(out)  # the gate opened an hour ago: nothing to say yet
    # the watchdog must be on while the gate is open
    for state in ({"ActiveState": "inactive", "UnitFileState": "enabled"}, {"ActiveState": "active", "UnitFileState": "disabled"}, {}):
        rc, out = run("watch" + str(len(state)) + state.get("ActiveState", ""), lambda h, s=state: setattr(h, "watch_props", s))
        assert "h5_watch_timer" in alerts(out)


def test_live_ok_is_the_root_owned_gate_in_etc_mal_h5(tmp_path):
    assert dc.LIVE_OK == "/etc/mal-h5/LIVE_OK" and dc.H5_ETC == "/etc/mal-h5"

    def case(name, mut):
        h = FakeHost()
        mut(h)
        return go(h, tmp=tmp_path / name)

    rc, out = go(FakeHost(), tmp=tmp_path / "ok")
    assert rc == 0 and "LIVE_OK=yes" in out
    for name, mut in {
        "group writable": lambda h: h.modes.__setitem__(dc.LIVE_OK, "root:root:664"),
        "other writable": lambda h: h.modes.__setitem__(dc.LIVE_OK, "root:root:646"),
        "owned by mal-live": lambda h: h.modes.__setitem__(dc.LIVE_OK, "mal-live:mal-live:644"),
        "symlink": lambda h: h.links.add(dc.LIVE_OK),
        "not a regular file": lambda h: h.irregular.add(dc.LIVE_OK),
    }.items():
        rc, out = case(name.replace(" ", "_"), mut)
        assert rc == 1 and "ALERT h5_live_ok_invalid" in out, name
    for name, mut in {
        "dir mode": lambda h: h.modes.__setitem__(dc.H5_ETC, "root:root:775"),
        "dir owner": lambda h: h.modes.__setitem__(dc.H5_ETC, "mal-live:mal-live:755"),
        "dir symlink": lambda h: h.links.add(dc.H5_ETC),
    }.items():
        rc, out = case("d" + name.replace(" ", "_"), mut)
        assert rc == 1 and "ALERT h5_etc_dir" in out, name
    rc, out = case("stale", lambda h: h.files.__setitem__(f"{H5}/LIVE_OK", b""))
    assert rc == 1 and "ALERT h5_live_ok_stale" in out


def test_stop_file_is_info_when_the_gate_is_closed_and_idle_when_open(tmp_path):
    h = FakeHost()
    h.files[f"{H5}/STOP"] = b""
    rc, out = go(h, tmp=tmp_path)
    assert "STOP=yes" in out and "h5_idle" in alerts(out)  # LIVE_OK is present in the clean world: STOP means the canary is idle


def test_wallet_balance_against_funded_plus_realized(tmp_path):
    h = FakeHost()
    for delta, alert in ((0, False), (3_000_000, False), (-3_000_000, False), (-30_000_000, True), (+30_000_000, True)):
        h.balance_lamports = FUNDED - 1_000_000 + delta
        rc, out = go(h, tmp=tmp_path / str(delta))
        assert ("wallet_gap" in alerts(out)) == alert, (delta, out)
    st = {"attempts": 4, "realized_lamports": -1_000_000, "open": {"M": {"buy_cost_lamports": 22_000_000}}, "pending": {}}
    h.files[f"{H5}/live/state-live.json"] = json.dumps(st).encode()
    h.balance_lamports = FUNDED - 1_000_000 - 22_000_000
    rc, out = go(h, tmp=tmp_path / "open")
    assert rc == 0 and "open cost 0.022000" in out
    # a buy in flight has left the wallet but is not an open position yet: subtracted too (no false gap)
    st = {"attempts": 4, "realized_lamports": -1_000_000, "open": {}, "pending": {"N": {"kind": "buy", "spend": 20_000_000}, "S": {"kind": "sell", "spend": 20_000_000}}}
    h.files[f"{H5}/live/state-live.json"] = json.dumps(st).encode()
    h.balance_lamports = FUNDED - 1_000_000 - 20_000_000
    rc, out = go(h, tmp=tmp_path / "pending")
    assert "wallet_gap" not in alerts(out) and "in-flight buys 0.020000" in out
    h.balance_lamports = FUNDED - 1_000_000 - 22_000_000 + 1_000_000_000
    rc, out = go(h, tmp=tmp_path / "dep")
    assert "wallet_gap" in alerts(out) and "stale --funded-sol" in out


def test_balance_failure_alerts_and_never_prints_the_key_or_url(tmp_path, monkeypatch):
    monkeypatch.setenv("HELIUS_API_KEY", "SECRETKEY123")

    def boom(wallet, env):
        raise RuntimeError("https://mainnet.helius-rpc.com/?api-key=SECRETKEY123 refused")

    rc, out = go(FakeHost(), tmp=tmp_path, balance=boom)
    assert rc == 1 and "ALERT balance_unavailable: getBalance failed (RuntimeError)" in out
    assert "SECRETKEY123" not in out and "helius-rpc" not in out


def test_no_funded_amount_skips_the_comparison_without_failing(tmp_path):
    h = FakeHost()
    lines: list[str] = []
    base = tmp_path / "b.json"
    base.write_text(json.dumps({"sha256": hashlib.sha256(PROBE_STATE).hexdigest()}))
    rc = dc.main(["--baseline", str(base), "--shadow-dir", "/x"], host=h, out=lines.append, now=NOW)
    assert rc == 0 and any("pass --funded-sol" in l for l in lines)


# --- sudo failures are alerts, never "absent" ---------------------------------------------------------------------------

def test_sudo_unavailable_is_an_alert_and_a_failing_privileged_read_is_not_absence(tmp_path):
    h = FakeHost()
    h.sudo = False
    rc, out = go(h, tmp=tmp_path / "a")
    assert rc == 1 and "ALERT sudo_unavailable" in out
    h = FakeHost()
    h.unreadable[f"{H5}/live/state-live.json"] = dc.SudoError("read /x: privileged read failed")
    rc, out = go(h, tmp=tmp_path / "b")
    assert rc == 1 and "ALERT sudo_failed: h5_state" in out and "does not exist yet" not in out
    h = FakeHost()
    h.unreadable[dc.PROBE_STATE] = dc.Unsafe("/var/lib/mal-live/state-live.json is not a regular file (link)")
    rc, out = go(h, tmp=tmp_path / "c")
    assert rc == 1 and "ALERT unsafe_path: probe_state" in out


class SudoHost(dc.Host):
    """The real Host with the privileged calls recorded and scripted."""

    def __init__(self, replies):
        self.calls: list[tuple[str, ...]] = []
        self.replies = replies

    def _sudo(self, *argv):
        self.calls.append(argv)
        return self.replies(argv)


def cp(rc=0, out=b"", err=b""):
    return subprocess.CompletedProcess([], rc, out, err)


@pytest.fixture
def locked(tmp_path):
    """A path that a non-root user cannot even lstat (parent mode 000): the code must go through sudo."""
    if os.geteuid() == 0:
        pytest.skip("needs a non-root user")
    d = tmp_path / "locked"
    d.mkdir()
    (d / "f").write_text("{}")
    d.chmod(0)
    yield str(d / "f")
    d.chmod(0o700)


def test_privileged_read_uses_dd_nofollow_on_the_exact_path_and_never_cat(locked):
    def replies(argv):
        if argv[0] == "/usr/bin/stat":
            return cp(0, b"regular file|1|mal-live|mal-live|600|1800000000|12\n")
        return cp(0, b'{"a": 1}')

    h = SudoHost(replies)
    assert h.read(locked) == b'{"a": 1}'
    assert h.calls[0][:3] == ("/usr/bin/stat", "-c", "%F|%h|%U|%G|%a|%Y|%s") and h.calls[0][-1] == locked
    assert h.calls[1] == ("/usr/bin/dd", "iflag=nofollow,skip_bytes", "skip=0", "status=none", f"if={locked}")
    assert all(c[0].startswith("/usr/bin/") and c[0] != "/usr/bin/cat" for c in h.calls)
    h.calls.clear()
    h.read(locked, tail=5)  # a tail read skips to size-5 (12 bytes: skip 7) instead of reading the whole ledger
    assert h.calls[1][2] == "skip=7"


def test_privileged_stat_refuses_symlinks_hardlinks_and_non_regular_before_any_read(locked):
    for label, nlink, want in (("symbolic link", 1, "not a regular file"), ("directory", 1, "not a regular file"), ("regular file", 2, "2 hard links")):
        h = SudoHost(lambda argv, label=label, nlink=nlink: cp(0, f"{label}|{nlink}|root|root|600|1800000000|5\n".encode()))
        with pytest.raises(dc.Unsafe, match=want):
            h.read(locked)
        assert [c[0] for c in h.calls] == ["/usr/bin/stat"]  # nothing was read


def test_sudo_failure_is_sudo_error_and_only_a_real_enoent_is_absence(locked):
    for err in (b"sudo: a password is required\n", b"sudo: sorry, you are not allowed to run sudo\n", b""):
        with pytest.raises(dc.SudoError):
            SudoHost(lambda argv, err=err: cp(1, b"", err)).lstat(locked)
    assert SudoHost(lambda argv: cp(1, b"", b"/usr/bin/stat: cannot statx '/x': No such file or directory\n")).lstat(locked) is None
    with pytest.raises(dc.SudoError):  # a sudo error that merely mentions the phrase is still a failure
        SudoHost(lambda argv: cp(1, b"", b"sudo: /usr/bin/stat: command not found: No such file or directory\n")).lstat(locked)
    # a stat that works but a read that fails
    def replies(argv):
        return cp(0, b"regular file|1|root|root|600|1800000000|5\n") if argv[0] == "/usr/bin/stat" else cp(1, b"", b"sudo: boom\n")

    with pytest.raises(dc.SudoError):
        SudoHost(replies).read(locked)


def test_direct_read_refuses_symlink_and_hardlink_and_follows_nothing(tmp_path):
    real = tmp_path / "real"
    real.write_text("secret")
    (tmp_path / "link").symlink_to(real)
    os.link(real, tmp_path / "hard")
    h = dc.Host()
    assert h.read(str(tmp_path / "missing")) is None
    with pytest.raises(dc.Unsafe, match="link"):
        h.read(str(tmp_path / "link"))
    with pytest.raises(dc.Unsafe, match="hard links"):
        h.read(str(tmp_path / "hard"))
    (tmp_path / "hard").unlink()
    assert h.read(str(real)) == b"secret" and h.read(str(real), tail=3) == b"ret"
    assert h.islink(str(tmp_path / "link")) and not h.is_regular(str(tmp_path / "link")) and h.is_regular(str(real))
    with pytest.raises(dc.Unsafe, match="not a regular file"):
        h.read(str(tmp_path))


def test_newest_hourly_ignores_the_status_file_and_the_error_log(tmp_path):
    for n in ("h5-shadow-status.json", "h5-shadow-errors.log", "h5-shadow-2026-10-09T13.jsonl", "h5-shadow-2026-10-09T14.jsonl", "notes.txt", "h5-shadow-2026-10-09T15.jsonl.tmp"):
        (tmp_path / n).write_text("x")
    os.utime(tmp_path / "h5-shadow-status.json", (NOW + 10, NOW + 10))  # the status file is the newest by mtime
    name, mtime = dc.Host().newest_hourly(str(tmp_path))
    assert name == "h5-shadow-2026-10-09T14.jsonl"
    assert dc.Host().newest_hourly(str(tmp_path / "nope")) is None


def test_script_is_read_only_and_keyless():
    src = (FAST / "h5-daily-check.py").read_text()
    assert "/etc/mal-probe/" not in src and "probe-wallet" not in src and "load_keypair" not in src and "solders" not in src
    assert "sendTransaction" not in src and "/usr/bin/cat" not in src and '"cat"' not in src
    verbs = {l.split("systemctl(")[1].split(",")[0].strip('")').strip('"') for l in src.splitlines() if "host.systemctl(" in l}
    assert verbs == {'"list-unit-files"', '"list-units"', '"show"'} or verbs == {"list-unit-files", "list-units", "show"}, verbs  # read verbs only
    sudo_args = re.findall(r'self\._sudo\("(/usr/bin/[a-z]+)"', src)
    assert sorted(set(sudo_args)) == ["/usr/bin/dd", "/usr/bin/stat", "/usr/bin/true"]  # sudo only runs fixed absolute read-only binaries
    assert "iflag=nofollow" in src and "O_NOFOLLOW" in src
