"""Tests for scripts/mal-fast/h5-daily-check.py: a fake host (files, systemctl, getBalance) for the checks, and the real Host class
against a temp dir and a recording fake of sudo for the reads. No sudo, no network, no key."""
from __future__ import annotations

import calendar
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import time
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
UPTIME = 100_000.0
PROBE_BASE = (FAST / "mal-probe-executor.service").read_text()
PROBE_FRAGMENT = "/etc/systemd/system/mal-probe-executor.service"
WSVC = (FAST / "mal-h5-watch.service").read_bytes()
WTMR = (FAST / "mal-h5-watch.timer").read_bytes()
WATCH_SVC, WATCH_TMR = dc.WATCH_FILES[0][0], dc.WATCH_FILES[1][0]


def ledger(*rows) -> bytes:
    return "".join(json.dumps(r) + "\n" for r in rows).encode()


class FakeHost(dc.Host):
    def __init__(self):
        self.files: dict[str, bytes] = {
            dc.PROBE_STATE: PROBE_STATE,  # no /var/lib/mal-live/STOP: the executor treats it as a wallet-wide STOP, so go-live needs it gone
           
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
        self.h5_props["ActiveEnterTimestampMonotonic"] = str(int((UPTIME - 3600) * 1e6))  # the current run began an hour ago
        self.watch_props = {"LoadState": "loaded", "ActiveState": "active", "UnitFileState": "enabled", "FragmentPath": WATCH_TMR, "DropInPaths": ""}
        self.watch_svc = {"LoadState": "loaded", "ActiveState": "inactive", "Result": "success", "ExecMainStatus": "0", "FragmentPath": WATCH_SVC, "DropInPaths": ""}
        # systemd 255.4 (mal-fast-0) prints LoadCredential=[unprintable] for EVERY unit, credential or not: the fake models that, so a check that
        # reads the `show` property sees a non-empty value on a clean unit. The unit text (`systemctl cat`) is what tells the truth.
        self.probe_props = {"LoadState": "loaded", "ActiveState": "inactive", "UnitFileState": "disabled", "DropInPaths": "", "LoadCredential": "[unprintable]"}
        self.probe_unit_text = PROBE_BASE  # the probe's dry-run base unit as the repo ships it: no LoadCredential
        self.probe_dropin_text: dict[str, str] = {}  # drop-in path -> text, merged into `systemctl cat` after the fragment
        self.cat_fail: dict[str, tuple[int, str]] = {}  # unit -> (rc, stdout) that `systemctl cat` answers instead of the unit text
        self.argv_log: list[tuple[str, ...]] = []
        self.files.update({WATCH_SVC: WSVC, WATCH_TMR: WTMR, f"{dc.PINNED}/mal-h5-watch.service": WSVC, f"{dc.PINNED}/mal-h5-watch.timer": WTMR,
                           dc.WATCH_STATE: json.dumps({"ts": NOW - 120}).encode()})
        self.systemctl_rc = 0
        self.dropins = [dc.DROPIN_LIVE, dc.DROPIN_FEED]
        self.execstart = LIVE_EXEC
        self.balance_lamports = FUNDED - 1_000_000
        self.feed = ("h5-shadow-2026-10-09T14.jsonl", NOW - 60)
        self.shadow_start: dict | None = {"type": "start", "h5_look2": {"observed": True, "amendment_ref": "EXP-024-Am2"}}  # the newest shadow start record
        self.birth_t: float | None = NOW - 86400  # the shadow directory is a day old: older than the unit's run, so the unit's bind is the real one
        self.sudo = True

    def birth(self, path):
        return self.birth_t

    def uptime(self):
        return UPTIME

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

    def newest_shadow_start(self, directory, max_files=400):
        return self.shadow_start

    def cat(self, unit):
        """`systemctl cat`: the fragment, then every drop-in with its path as a comment header, as systemd prints them."""
        if unit in self.cat_fail:
            return self.cat_fail[unit]
        if unit == f"{dc.PROBE_UNIT}.service":
            if self.probe_props.get("LoadState") == "not-found":
                return 1, ""  # "No files found for ...": nothing on stdout, exit 1
            return 0, f"# {PROBE_FRAGMENT}\n{self.probe_unit_text}" + "".join(f"\n# {pth}\n{txt}" for pth, txt in self.probe_dropin_text.items())
        assert unit == f"{dc.H5_UNIT}.service", unit
        return 0, f"# {dc.UNIT_FILE}\n{self.files[dc.UNIT_FILE].decode()}" + "".join(f"\n# {pth}\n{self.files.get(pth, b'').decode()}" for pth in self.dropins)

    def systemctl(self, *argv):
        self.argv_log.append(argv)
        if self.systemctl_rc:
            return self.systemctl_rc, ""
        if argv[0] == "cat":
            return self.cat(argv[1])
        if argv[0] == "list-unit-files":
            return 0, self.unit_files
        if argv[0] == "list-units":
            return 0, self.units
        unit = argv[1]

        def props(d):
            return "".join(f"{k}={v}\n" for k, v in d.items())

        if unit == dc.WATCH_TIMER:
            return 0, props(self.watch_props)
        if unit == dc.WATCH_SERVICE:
            return 0, props(self.watch_svc)
        if unit == f"{dc.PROBE_UNIT}.service":
            return 0, props(self.probe_props)
        if "DropInPaths" in argv and "--value" in argv:
            return 0, " ".join(self.dropins) + "\n"
        if "NRestarts" in argv and "--value" in argv:
            return 0, self.h5_props["NRestarts"] + "\n"
        if "--value" in argv:
            return 0, self.execstart
        return 0, props(self.h5_props)


def go(host, *extra, tmp, baseline=True, balance=None, now=NOW):
    tmp.mkdir(parents=True, exist_ok=True)
    base = tmp / "baseline.json"
    if baseline and not base.exists():
        base.write_text(json.dumps({"sha256": hashlib.sha256(PROBE_STATE).hexdigest(), "dec020_sha256": "absent"}))
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
    assert "the shadow window could not be judged" in out  # no shadow file for the window: the old alert (fail closed)
    rc, out = run("talking", lambda h: (silent(h), h.files.__setitem__(f"{H5}/live/h5-ledger.jsonl", h.files[f"{H5}/live/h5-ledger.jsonl"] + ledger({"kind": "skip", "ts_ms": int((NOW - 3600) * 1000)}))))
    assert "h5_idle_ledger" not in alerts(out)
    rc, out = run("young_gate", lambda h: (silent(h), h.mtimes.__setitem__(dc.LIVE_OK, NOW - 3600))[0])
    assert "h5_idle_ledger" not in alerts(out)  # the gate opened an hour ago: nothing to say yet
    # the watchdog must be on while the gate is open
    for state in ({"ActiveState": "inactive", "UnitFileState": "enabled"}, {"ActiveState": "active", "UnitFileState": "disabled"}):
        rc, out = run("watch" + str(len(state)) + state.get("ActiveState", ""), lambda h, s=state: setattr(h, "watch_props", {**h.watch_props, **s}))
        assert "h5_watch_timer" in alerts(out)
    rc, out = run("watch_missing", lambda h: setattr(h, "watch_props", {"LoadState": "not-found", "ActiveState": "inactive", "UnitFileState": ""}))
    assert "h5_watch_timer" in alerts(out)


# ---- h5_idle_ledger judged against the shadow (2026-10-11: a quiet market raised it at ~00:25Z; jobs #672-#676) -----------------
SHADOW_DIR = "/nowhere"  # what go() passes as --shadow-dir
# The EXP-022 seal window of the idle check (dc.IDLE_SEAL_START_S, pinned to tools/h5_executor.py SEAL_START_MS): from this instant, by the
# watchdog's clock, no trigger is judged against the ledger (QP-B1/B3 on #589). The worlds that need h5_idle_unledgered run before it.
SEAL_START = calendar.timegm((2026, 10, 16, 1, 0, 0))
T = calendar.timegm((2026, 10, 15, 0, 25, 0))  # before the seal; 25 minutes into an hour, like the 00:25Z alert: the 6 h window spans 7 hourly files
T_SEAL = SEAL_START + 25 * 60  # 2026-10-16T01:25Z: in the seal window
T_SEAL_LAST = calendar.timegm((2026, 11, 6, 1, 25, 0))  # the last hour before the executor's default seal_end_ms (2026-11-06T02Z)
T_LATE = NOW + 25 * 60  # 2027-01-15T08:25Z: after any default seal end; the switch has no automatic end
OLD_IDLE = "the live ledger has no buy, skip or decision row in the last 6 h: the canary sees no triggers (feed, detector or executor)"
OLD_IDLE_SEAL = "the live ledger has no buy, skip or decision row in the last 6 h: the canary has acted on nothing (feed, detector or executor)"
QUIET = "INFO  quiet market: no plain trigger in 6 h; "
QUIET_SEAL = "INFO  quiet market: ledger idle 6 h; "
NONE_DRAINED = "none drained to <= 40 SOL without the pv rule firing"
DRAINED = "pool(s) drained to <= 40 SOL without the pv rule firing or without usable sps, below the alert: "
CAND = "missed candidate(s) (no gap and a min_q window ending by 330 s, or no usable sps; h5_detector_missed needs 2)"
AMBIGUOUS = "ambiguous (a gap or a 1500-slot min_q window past 330 s: possibly a drain after the rule's 300 s; never counted as missed)"


def ms(t: float) -> int:
    return int(t * 1000)


def shadow_files(*recs, directory: str = SHADOW_DIR) -> dict[str, bytes]:
    """Records grouped into the shadow's hourly files by their t_ms (compact JSON, "type" first, as JsonlSink writes them)."""
    out: dict[str, bytes] = {}
    for r in recs:
        path = f"{directory}/h5-shadow-{time.strftime('%Y-%m-%dT%H', time.gmtime(r['t_ms'] / 1000))}.jsonl"
        out[path] = out.get(path, b"") + (json.dumps(r, separators=(",", ":")) + "\n").encode()
    return out


def s_trigger(t: float, pool: str = "POOLSECRET1", variant: str = "pv", synthetic=False) -> dict:
    return {"type": "trigger", "variant": variant, "pool": pool, "mint": "MINTSECRET" + pool, "s0": 1, "t_recv_ms": ms(t) - 40,
            "t_detect_ms": ms(t), "q_trigger_sol": 37.25, "synthetic": synthetic, "synthetic_src": "rpc", "v": 1, "t_ms": ms(t)}


def s_pool(t: float, n: int, min_q=61.5, triggered=(), synthetic=False, src="rpc", sealed=False, look2=False, sps=0.217, gap=False,
           sps_last=None) -> dict:
    """A shadow `pool` close record. sps_at_s0 0.217 s/slot (the usual since go-live): min_q's 1500 slots end at 325.5 s, within 330 s."""
    if sealed:  # the EXP-022 s9 close record: no min_q, no triggered, no class
        return {"type": "pool", "reason": "done", "sealed": True, "pool": f"POOLSECRET{n}", "mint": f"MINTSECRET{n}", "sps_at_s0": sps, "v": 1,
                "t_ms": ms(t)}
    rec = {"type": "pool", "reason": "done", "pool": f"POOLSECRET{n}", "mint": f"MINTSECRET{n}", "sps_at_s0": sps, "sps_last": sps_last,
           "min_q_pv_sol": min_q, "min_q_fv_sol": min_q, "sealed": False, "triggered": list(triggered), "synthetic": synthetic,
           "synthetic_src": src, "gap": gap, "v": 1, "t_ms": ms(t)}
    if look2:  # EXP-024 Look 2's seal: the shadow writes min_q None with this flag (a number here tests the flag alone)
        rec["h5_look2_sealed"] = True
    return rec


def s_excluded(t: float, n: int, reason: str = "unclassified") -> dict:
    """The shadow's `excluded` record (_exclude): a trigger withheld because the pool was synthetic or unclassified at decision time."""
    return {"type": "excluded", "reason": reason, "pool": f"POOLSECRET{n}", "mint": f"MINTSECRET{n}", "s0": 1, "slot": 2, "v": 1, "t_ms": ms(t)}


def s_skipped(t: float, n: int) -> dict:
    """The shadow's `skipped_no_sps` record (_eval): a would-be trigger candidate the rule could not judge for want of sps."""
    return {"type": "skipped_no_sps", "pool": f"POOLSECRET{n}", "mint": f"MINTSECRET{n}", "s0": 1, "slot": 400, "slot_offset": 399, "sps": None,
            "t_recv_ms": ms(t) - 40, "signature": "SIGSECRET", "q_pv_post_sol": 38.2, "q_fv_post_sol": 38.2, "boost_spent_sol": 1.0,
            "boost_src": "pda", "v": 1, "t_ms": ms(t)}


def spread(n: int, older_s: float, newer_s: float, first: int = 0, now: float = T, **kw) -> list[dict]:
    """n plain pool closes evenly in [now - older_s, now - newer_s], not drained (min_q 41-90 SOL)."""
    step = (older_s - newer_s) / max(1, n - 1)
    return [s_pool(now - older_s + i * step, first + i, **{"min_q": 41.0 + i % 50, **kw}) for i in range(n)]


def quiet_pools(n: int = 108, now: float = T, **kw) -> list[dict]:
    """n plain pool closes spread over the 6 h before `now`, none drained to <= 40 SOL (108: 19Z-00Z on 2026-10-10, job #676)."""
    return spread(n, 6 * 3600 - 60, 60, now=now, **kw)


def idle_world(*recs, ledger_rows=None, now: float = T, cls=None) -> FakeHost:
    """LIVE_OK 28 h old; the ledger's newest buy/skip/decision row 19 h old with feed rows since (the 2026-10-11 ledger: boost_last_slice,
    feed_gap ... and no buy, skip or decision row); the shadow from `recs`; the feed, the watchdog and the shadow directory's birth at `now`."""
    h = (cls or FakeHost)()
    h.mtimes[dc.LIVE_OK] = now - 28 * 3600
    h.feed = ("h5-shadow-hour.jsonl", now - 60)
    h.birth_t = now - 86400  # older than the unit's current run (an hour): the unit's bind is the real directory
    h.files[dc.WATCH_STATE] = json.dumps({"ts": now - 120}).encode()
    rows = ledger_rows if ledger_rows is not None else (
        {"kind": "start", "user": dc.WALLET, "ts_ms": ms(now - 28 * 3600)}, {"kind": "decision", "ts_ms": ms(now - 20 * 3600)},
        {"kind": "skip", "reason": "stop_file", "ts_ms": ms(now - 19 * 3600)}, {"kind": "boost_last_slice", "ts_ms": ms(now - 7200)},
        {"kind": "boost_row_ignored", "ts_ms": ms(now - 3600)}, {"kind": "feed_reconnect", "ts_ms": ms(now - 600)}, {"kind": "feed_gap", "ts_ms": ms(now - 60)})
    h.files[f"{H5}/live/h5-ledger.jsonl"] = ledger(*rows)
    h.files.update(shadow_files(*recs))
    return h


def no_per_pool_values(out: str) -> bool:
    return "SECRET" not in out and "37.25" not in out and "38.2" not in out and "61.5" not in out


def missed_alert(n: int, nosps: int = 0) -> str:
    return (f"ALERT h5_detector_missed: {n} pool(s) in the last 6 h are missed candidates" + (f" ({nosps} with no usable sps)" if nosps else "")
            + ": plain closes drained to <= 40 SOL (min_q_pv_sol) without the pv rule firing, with no gap and the 1500-slot min_q window ending by "
              "330 s (at the larger of sps_at_s0 and sps_last) or with sps_at_s0 not in (0.15, 0.6) s/slot, and pools with a skipped_no_sps record "
              "(the rule cannot fire without sps); at least 2 needed (the rule fires only within 300 s with BOOST still buying, so one pool can "
              "drain legitimately just after: check the shadow before calling it a detector fault)")


def test_idle_ledger_in_a_quiet_market_is_info_not_an_alert(tmp_path):
    excluded = [{"type": "excluded", "reason": "synthetic", "pool": f"POOLSECRETX{i}", "mint": "MINTSECRETX", "s0": 1, "slot": 2, "v": 1,
                 "t_ms": ms(T - 3600 * i)} for i in range(1, 6)]  # synthetic exclusions: silent by design, never activity
    hb = [{"type": "hb", "counters": {"triggers_pv": 9, "pools_tracked": 400, "excluded_synthetic": 41}, "v": 1, "t_ms": ms(T - 60 * i)} for i in range(1, 60)]
    h = idle_world(*quiet_pools(), *excluded, *hb, s_pool(T - 3000, 900, min_q=30.0, synthetic=True, src="ws"),  # synthetic, drained: not plain
                   s_pool(T - 2000, 901, sealed=True))  # sealed: no min_q, skipped
    rc, out = go(h, tmp=tmp_path, now=T)
    assert rc == 0 and alerts(out) == [], out
    assert (QUIET + "108 plain pools closed with a min_q, " + NONE_DRAINED + "; 36 plain closes in the last 2 h; 7 shadow file(s) read") in out
    assert no_per_pool_values(out)


def test_idle_ledger_plain_triggers_without_a_ledger_row_mean_the_executor_is_not_reading(tmp_path):
    base = quiet_pools()
    h = idle_world(*base, s_trigger(T - 3 * 3600), s_trigger(T - 3 * 3600 + 0.4, variant="fv"),  # one pool, both variants: counted once
                   s_trigger(T - 2 * 3600, pool="POOLSECRET2"), s_trigger(T - 2 * 3600, pool="POOLSECRET2"))
    rc, out = go(h, tmp=tmp_path / "two", now=T)
    assert rc == 1 and alerts(out) == ["h5_idle_unledgered"], out
    assert ("the detector wrote 2 plain pv trigger(s) in the last 19 h, more than 5 min ago and after the live ledger's newest buy, skip or decision "
            "row, and the ledger has no row for them: the executor is not reading the feed (bind, tail or crash)") in out
    assert no_per_pool_values(out)
    no_detect = {k: v for k, v in s_trigger(T - 3600).items() if k != "t_detect_ms"}  # t_recv_ms is the fallback
    rc, out = go(idle_world(*base, no_detect), tmp=tmp_path / "recv", now=T)
    assert alerts(out) == ["h5_idle_unledgered"] and "wrote 1 plain pv trigger(s)" in out
    # not counted: the fv variant alone (the executor never trades it), a trigger not marked plain, one still in flight (5 min grace),
    # one before the ledger's newest skip row (19 h ago), and a drained-and-triggered pool's close record with no trigger record
    for name, rec in {"fv only": s_trigger(T - 3 * 3600, variant="fv"), "synthetic null": s_trigger(T - 3 * 3600, synthetic=None),
                      "synthetic true": s_trigger(T - 3 * 3600, synthetic=True), "in flight": s_trigger(T - 200),
                      "before the newest row": s_trigger(T - 19 * 3600 - 300), "close only": s_pool(T - 3600, 999, min_q=12.0, triggered=["fv", "pv"])}.items():
        rc, out = go(idle_world(*base, rec), tmp=tmp_path / name.replace(" ", "_"), now=T)
        assert alerts(out) == [] and "quiet market" in out, (name, out)
    rc, out = go(idle_world(*base, s_trigger(T - 301)), tmp=tmp_path / "grace_edge", now=T)
    assert alerts(out) == ["h5_idle_unledgered"]  # just past the grace


def test_idle_ledger_an_unledgered_trigger_older_than_6_h_still_alerts(tmp_path):
    """#589 review B1: a stuck executor whose last unledgered trigger is more than 6 h old must not read as a quiet market. The probe: last
    decision 20 h ago, one plain pv trigger 7 h ago with no ledger row, 108 quiet plain pools, LIVE_OK 28 h old."""
    rows = ({"kind": "start", "user": dc.WALLET, "ts_ms": ms(T - 28 * 3600)}, {"kind": "decision", "ts_ms": ms(T - 20 * 3600)},
            {"kind": "feed_gap", "ts_ms": ms(T - 60)})
    rc, out = go(idle_world(*quiet_pools(), s_trigger(T - 7 * 3600), ledger_rows=rows), tmp=tmp_path / "7h", now=T)
    assert rc == 1 and alerts(out) == ["h5_idle_unledgered"] and "quiet market" not in out, out
    assert "wrote 1 plain pv trigger(s) in the last 20 h" in out and no_per_pool_values(out)
    rc, out = go(idle_world(*quiet_pools(), s_trigger(T - 20 * 3600 + 60), ledger_rows=rows), tmp=tmp_path / "20h", now=T)
    assert alerts(out) == ["h5_idle_unledgered"]  # a minute after the newest decision row
    rc, out = go(idle_world(*quiet_pools(), s_trigger(T - 20 * 3600 - 60), ledger_rows=rows), tmp=tmp_path / "before", now=T)
    assert alerts(out) == [] and "quiet market" in out  # before it: that row may be its own
    h = idle_world(*quiet_pools(), s_trigger(T - 7 * 3600), ledger_rows=rows)
    h.mtimes[dc.LIVE_OK] = T - 6.5 * 3600  # the gate opened after the trigger: the live executor was never meant to see it
    rc, out = go(h, tmp=tmp_path / "pre_gate", now=T)
    assert alerts(out) == [] and "quiet market" in out
    # no buy, skip or decision row at all (the whole file read), LIVE_OK 20 h old: looked for back to the gate
    rows = ({"kind": "start", "user": dc.WALLET, "ts_ms": ms(T - 20 * 3600)}, {"kind": "feed_gap", "ts_ms": ms(T - 60)})
    h = idle_world(*quiet_pools(), s_trigger(T - 15 * 3600), ledger_rows=rows)
    h.mtimes[dc.LIVE_OK] = T - 20 * 3600
    rc, out = go(h, tmp=tmp_path / "none", now=T)
    assert alerts(out) == ["h5_idle_unledgered"] and "in the last 20 h" in out
    # never further back than 24 h, whatever the caller asks (the 24 h backstop has an older case)
    for t_trig in (T - 25 * 3600, T - 24 * 3600 - 60):  # in an older hour's file, and in the oldest file read but before the 24 h
        assert dc.shadow_idle_counts(idle_world(s_trigger(t_trig), *quiet_pools()), SHADOW_DIR, T, T - 26 * 3600)[0]["unledgered"] == 0
    assert dc.shadow_idle_counts(idle_world(s_trigger(T - 24 * 3600 + 60), *quiet_pools()), SHADOW_DIR, T, T - 26 * 3600)[0]["unledgered"] == 1


def test_idle_ledger_a_seal_count_row_accounts_for_the_triggers_before_it(tmp_path):
    """#589 review F5: the executor's EXP-022 seal refuses a trigger with a count (a `seal_count` row), not a skip row. QP-B4 (4): a world
    that needs the unledgered judgement runs before 2026-10-16T01Z (T); from then on the judgement is off (QP-B1/B3), checked below."""
    def sealed_rows(now, t_seal):
        return ({"kind": "start", "user": dc.WALLET, "ts_ms": ms(now - 28 * 3600)}, {"kind": "decision", "ts_ms": ms(now - 20 * 3600)},
                {"kind": "seal_count", "seal_skips": 4, "ts_ms": ms(t_seal)}, {"kind": "feed_gap", "ts_ms": ms(now - 60)})

    trig = (s_trigger(T - 3 * 3600), s_trigger(T - 2 * 3600, pool="POOLSECRET2"))
    rc, out = go(idle_world(*quiet_pools(), *trig, ledger_rows=sealed_rows(T, T - 2 * 3600 + 30)), tmp=tmp_path / "after", now=T)
    assert rc == 0 and alerts(out) == [], out
    assert ("INFO  no unledgered trigger: the 2 plain pv trigger(s) in 6 h all came before the executor's newest seal_count row (EXP-022 seal "
            "refusals are counted, not ledgered); 108 plain pools closed with a min_q, " + NONE_DRAINED) in out and no_per_pool_values(out)
    rc, out = go(idle_world(*quiet_pools(), *trig, ledger_rows=sealed_rows(T, T - 2.5 * 3600)), tmp=tmp_path / "between", now=T)
    assert alerts(out) == ["h5_idle_unledgered"] and "wrote 1 plain pv trigger(s)" in out  # the newer one came after the newest seal_count row
    # the same two worlds in the seal window: no trigger is judged or counted, whatever the seal_count row says
    trig = (s_trigger(T_SEAL - 3 * 3600), s_trigger(T_SEAL - 2 * 3600, pool="POOLSECRET2"))
    for name, t_seal in {"after": T_SEAL - 2 * 3600 + 30, "between": T_SEAL - 2.5 * 3600}.items():
        h = idle_world(*quiet_pools(now=T_SEAL), *trig, ledger_rows=sealed_rows(T_SEAL, t_seal), now=T_SEAL)
        rc, out = go(h, tmp=tmp_path / f"seal_{name}", now=T_SEAL)
        assert rc == 0 and alerts(out) == [], (name, out)
        assert QUIET_SEAL + "108 plain pools closed with a min_q, " + NONE_DRAINED + "; 36 plain closes in the last 2 h; 7 shadow file(s) read" in out
        assert "trigger" not in out.lower() and "seal_count" not in out and no_per_pool_values(out), (name, out)


def test_idle_ledger_in_the_exp022_seal_window_judges_no_trigger_against_the_ledger(tmp_path, monkeypatch):
    """QP-B1/B3/B4 on #589: from 2026-10-16T01Z a healthy executor leaves a shadow plain pv trigger without a ledger row only on seal_pick
    (_refuse writes nothing for it), so counting triggers against the ledger would count executor-picked mints that drained (DEC-024 Am.6 items
    4(a) and 5). From then on, by the watchdog's clock and with no automatic end, the judgement is skipped whole: no h5_idle_unledgered, no
    trigger count, no trigger wording, and no trigger record read."""
    def world(now, t_trig):  # a plain pv trigger older than 5 min, no ledger row after it, 6 h (here 19 h) of ledger silence, 108 quiet pools
        return idle_world(*quiet_pools(now=now), s_trigger(t_trig), now=now)

    # (2) before the seal window the world alerts, up to the last second before it
    for name, now in {"T": T, "last second": SEAL_START - 1}.items():
        rc, out = go(world(now, now - 3600), tmp=tmp_path / f"pre_{name.replace(' ', '_')}", now=now)
        assert rc == 1 and alerts(out) == ["h5_idle_unledgered"] and "wrote 1 plain pv trigger(s)" in out, (name, out)
    # (1) inside [2026-10-16T01Z, 2026-11-06T02Z), and after it (no automatic end): suppressed, a pool-close INFO, the word "trigger" nowhere
    for name, now in {"seal": T_SEAL, "mid": T_SEAL + 10 * 86400, "last hour": T_SEAL_LAST, "2027": T_LATE}.items():
        rc, out = go(world(now, now - 3600), tmp=tmp_path / f"in_{name.replace(' ', '_')}", now=now)
        assert rc == 0 and alerts(out) == [], (name, out)
        assert (QUIET_SEAL + "108 plain pools closed with a min_q, " + NONE_DRAINED + "; 36 plain closes in the last 2 h; 7 shadow file(s) read"
                in out), (name, out)
        assert "trigger" not in out.lower() and no_per_pool_values(out), (name, out)
    rc, out = go(world(SEAL_START, SEAL_START - 3600), tmp=tmp_path / "first_second", now=SEAL_START)
    assert alerts(out) == [] and "INFO  quiet market: ledger idle 6 h; " in out and "trigger" not in out.lower(), out
    # (3) a trigger detected before 01:00Z and judged after it: suppressed (keyed on the watchdog's clock, not on the trigger's or a pool's time)
    rc, out = go(world(T_SEAL, SEAL_START - 600), tmp=tmp_path / "straddle", now=T_SEAL)
    assert rc == 0 and alerts(out) == [] and "trigger" not in out.lower(), out
    # nothing returned carries a trigger count, and no trigger record is parsed: neither the type-first form JsonlSink writes nor another order
    other_order = {"variant": "pv", **s_trigger(T_SEAL - 1800, pool="POOLSECRET3")}  # "type" not first: parsed, then dropped unused
    h = idle_world(*quiet_pools(now=T_SEAL), s_trigger(T_SEAL - 3600), other_order, now=T_SEAL)
    parsed: list[str] = []
    real_loads = dc.json.loads
    monkeypatch.setattr(dc.json, "loads", lambda s, *a, **k: (parsed.append(s if isinstance(s, str) else s.decode()), real_loads(s, *a, **k))[1])
    counts, why = dc.shadow_idle_counts(h, SHADOW_DIR, T_SEAL, T_SEAL - 20 * 3600)  # a trig_lo passed anyway is ignored
    monkeypatch.undo()
    assert counts is not None and "unledgered" not in counts and "triggers_window" not in counts, (counts, why)
    assert not [p for p in parsed if p.startswith('{"type":"trigger"')] and len([p for p in parsed if '"trigger"' in p]) == 1
    assert counts == dc.shadow_idle_counts(idle_world(*quiet_pools(now=T_SEAL), now=T_SEAL), SHADOW_DIR, T_SEAL)[0]  # as if there were none
    pre = dc.shadow_idle_counts(world(T, T - 3600), SHADOW_DIR, T)[0]
    assert (pre["unledgered"], pre["triggers_window"]) == (1, 1)
    # the seal window reads no file older than the window (the pre-seal look-back is for triggers): an unreadable one 10 h back is not opened
    h = world(T_SEAL, T_SEAL - 3600)
    h.files[dc.shadow_hour_path(SHADOW_DIR, int((T_SEAL - 10 * 3600) // 3600))] = b""
    h.unreadable[dc.shadow_hour_path(SHADOW_DIR, int((T_SEAL - 10 * 3600) // 3600))] = PermissionError(13, "Permission denied")
    rc, out = go(h, tmp=tmp_path / "old_unreadable", now=T_SEAL)
    assert alerts(out) == [] and QUIET_SEAL in out, out
    h = world(T, T - 3600)  # ... while before the seal it is read (for triggers), so the same file fails the check closed
    h.unreadable[dc.shadow_hour_path(SHADOW_DIR, int((T - 10 * 3600) // 3600))] = PermissionError(13, "Permission denied")
    rc, out = go(h, tmp=tmp_path / "old_unreadable_pre", now=T)
    assert alerts(out) == ["h5_idle_ledger"] and "PermissionError" in out


def test_idle_ledger_in_the_seal_window_keeps_the_shadow_only_alerts_without_trigger_words(tmp_path):
    """In the seal window liveness rests on the plain-pool quorum, h5_detector_missed (shadow-only facts about non-sealed pools), the fail-closed
    old alert and the 24 h backstop: each still fires, and none prints the word "trigger" or a count of triggered pools."""
    base = quiet_pools(now=T_SEAL)
    # `triggered` still classifies a close (a drained pool with pv in it is not missed), but no count of triggered pools is printed: the line
    # for 108 quiet + 2 drained-and-triggered closes is the line for 110 quiet ones
    trig2 = [s_pool(T_SEAL - 3600 - i, 500 + i, min_q=12.0, triggered=["pv"]) for i in range(2)]
    rc, out = go(idle_world(*base, *trig2, now=T_SEAL), tmp=tmp_path / "triggered", now=T_SEAL)
    quiet2 = [s_pool(T_SEAL - 3600 - i, 500 + i) for i in range(2)]  # the same closes, not drained
    rc2, out2 = go(idle_world(*base, *quiet2, now=T_SEAL), tmp=tmp_path / "quiet2", now=T_SEAL)
    line = [x for x in out.splitlines() if x.startswith(QUIET_SEAL)]
    assert rc == 0 and alerts(out) == [] and line == [x for x in out2.splitlines() if x.startswith(QUIET_SEAL)], (out, out2)
    assert line[0].startswith(QUIET_SEAL + "110 plain pools closed with a min_q, " + NONE_DRAINED) and "trigger" not in out.lower()
    # two drained closes without pv: h5_detector_missed, worded on pool closes
    drained = [s_pool(T_SEAL - 3600 - i, 500 + i, min_q=30.0) for i in range(2)]
    rc, out = go(idle_world(*base, *drained, now=T_SEAL), tmp=tmp_path / "missed", now=T_SEAL)
    assert rc == 1 and alerts(out) == ["h5_detector_missed"] and missed_alert(2) in out and "trigger" not in out.lower(), out
    # one: an INFO that is not an all-clear
    rc, out = go(idle_world(*base, drained[0], now=T_SEAL), tmp=tmp_path / "one", now=T_SEAL)
    assert alerts(out) == [] and ("INFO  ledger idle 6 h; 1 " + DRAINED + "1 " + CAND + "; 109 plain pools closed with a min_q; ") in out, out
    assert "quiet market" not in out and "trigger" not in out.lower()
    # skipped_no_sps records (non-sealed pools only) count in the seal window too
    rc, out = go(idle_world(*base, s_skipped(T_SEAL - 3600, 700), s_skipped(T_SEAL - 1800, 701), now=T_SEAL), tmp=tmp_path / "nosps", now=T_SEAL)
    assert alerts(out) == ["h5_detector_missed"] and missed_alert(2, 2) in out and "trigger" not in out.lower(), out
    # too few plain closes: the not-tracking alert without "no unledgered trigger"
    rc, out = go(idle_world(*quiet_pools(29, now=T_SEAL), now=T_SEAL), tmp=tmp_path / "few", now=T_SEAL)
    assert alerts(out) == ["h5_idle_ledger"] and ("ALERT h5_idle_ledger: no missed drain, but 29 plain pool(s) closed in 6 h and 10 in the last 2 h "
                                                  "(at least 30 and 10 expected): the detector is not tracking pools") in out, out
    assert "trigger" not in out.lower()
    # fail closed (no shadow file, a missing hour, withheld min_q, no ledger row): the old alert, worded without "triggers"
    h = idle_world(*quiet_pools(200, now=T_SEAL), now=T_SEAL)
    h.files.pop(sorted(dc.shadow_window_paths(SHADOW_DIR, T_SEAL))[3])
    for name, world in {"no shadow": idle_world(now=T_SEAL), "missing hour": h, "withheld": idle_world(*quiet_pools(now=T_SEAL, min_q=None), now=T_SEAL),
                        "no ledger row": idle_world(*base, ledger_rows=(), now=T_SEAL)}.items():
        rc, out = go(world, tmp=tmp_path / name.replace(" ", "_"), now=T_SEAL)
        assert rc == 1 and alerts(out) == ["h5_idle_ledger"] and OLD_IDLE_SEAL in out and "trigger" not in out.lower(), (name, out)
    # the 24 h backstop
    old = ({"kind": "start", "user": dc.WALLET, "ts_ms": ms(T_SEAL - 28 * 3600)}, {"kind": "decision", "ts_ms": ms(T_SEAL - 25 * 3600)})
    rc, out = go(idle_world(*base, s_trigger(T_SEAL - 3600), ledger_rows=old, now=T_SEAL), tmp=tmp_path / "backstop", now=T_SEAL)
    assert alerts(out) == ["h5_idle_ledger"] and "24 h backstop" in out and "trigger" not in out.lower(), out


def test_idle_ledger_drained_plain_pool_without_a_trigger_is_a_detector_miss(tmp_path):
    base = quiet_pools()
    h = idle_world(*base, s_pool(T - 3600, 500, min_q=38.2), s_pool(T - 1800, 501, min_q=40.0))
    rc, out = go(h, tmp=tmp_path / "two", now=T)
    assert rc == 1 and alerts(out) == ["h5_detector_missed"], out
    assert missed_alert(2) in out and no_per_pool_values(out)
    other = s_pool(T - 1800, 501, min_q=40.0)  # a second missed candidate: one alone is an INFO (IDLE_MISSED_MIN)
    no_key = {k: v for k, v in s_pool(T - 3600, 500, min_q=39.0).items() if k != "triggered"}
    rc, out = go(idle_world(*base, no_key, other), tmp=tmp_path / "missing_key", now=T)
    assert alerts(out) == ["h5_detector_missed"] and "2 pool(s) in the last 6 h are missed candidates" in out
    # #589 review F4: the executor trades pv only, so a pool drained on pv with only an fv trigger is a pv miss
    rc, out = go(idle_world(*base, s_pool(T - 3600, 500, min_q=20.0, triggered=["fv"]), other), tmp=tmp_path / "fv_only", now=T)
    assert alerts(out) == ["h5_detector_missed"] and "2 pool(s) in the last 6 h are missed candidates" in out
    for name, rec in {"triggered": s_pool(T - 3600, 500, min_q=12.0, triggered=["fv", "pv"]), "pv only": s_pool(T - 3600, 500, min_q=12.0, triggered=["pv"]),
                      "above Q*": s_pool(T - 3600, 500, min_q=40.01), "synthetic": s_pool(T - 3600, 500, min_q=20.0, synthetic=True),
                      "not rpc": s_pool(T - 3600, 500, min_q=20.0, src="ws"), "sealed": s_pool(T - 3600, 500, sealed=True),
                      "before the window": s_pool(T - 6 * 3600 - 60, 500, min_q=20.0)}.items():
        rc, out = go(idle_world(*base, rec), tmp=tmp_path / name.replace(" ", "_"), now=T)
        assert alerts(out) == [] and "quiet market" in out and "withheld" not in out, (name, out)
    # min_q withheld (None, not a number, a bool) or Look-2-sealed: not judged either way, and said so
    for name, rec in {"min_q withheld": s_pool(T - 3600, 500, min_q=None), "not a number": s_pool(T - 3600, 500, min_q="12"),
                      "a bool": s_pool(T - 3600, 500, min_q=True), "look2": s_pool(T - 3600, 500, min_q=12.0, look2=True)}.items():
        rc, out = go(idle_world(*base, rec), tmp=tmp_path / name.replace(" ", "_"), now=T)
        assert alerts(out) == [] and "108 plain pools closed with a min_q" in out and "(1 more with min_q withheld, not judged)" in out, (name, out)
    # an unledgered trigger outranks a miss: one alert, the executor's
    rc, out = go(idle_world(*base, s_pool(T - 3600, 500, min_q=38.2), s_trigger(T - 3600)), tmp=tmp_path / "both", now=T)
    assert alerts(out) == ["h5_idle_unledgered"] and "executor is not reading" in out


def test_idle_ledger_one_missed_candidate_is_info_and_slow_slots_or_gaps_are_ambiguous(tmp_path):
    """MiScusi jobs #681/#682: min_q_pv covers the first 1500 slots after s0, the rule only 300 s. On 2026-10-09 10Z-14Z (0.265-0.270 s/slot,
    a window of about 400 s) 20 plain pools drained to <= 40 SOL with an empty `triggered`, all after 300 s; a 21st at 18Z had sps 0.2212
    (331.8 s) and a gap. Since go-live, 23/23 drained plain pools triggered pv. Such pools are ambiguous: judged, never missed."""
    base = quiet_pools()
    # one missed candidate alone: an INFO naming it, not an alert (a single pool can drain legitimately up to 30 s past the rule window), and
    # not worded as an all-clear (#589 review R-F)
    rc, out = go(idle_world(*base, s_pool(T - 3600, 500, min_q=38.2)), tmp=tmp_path / "one", now=T)
    assert rc == 0 and alerts(out) == [], out
    assert ("INFO  no plain trigger in 6 h; 1 " + DRAINED + "1 " + CAND + "; 109 plain pools closed with a min_q; 37 plain closes in the last 2 h; "
            "7 shadow file(s) read") in out and "quiet market" not in out and "ambiguous" not in out and no_per_pool_values(out)
    # the #681 shape: 20 slow-slot pools (0.267 s/slot), min_q 30, untriggered: ambiguous, never missed
    slow = [s_pool(T - 3600 - 60 * i, 500 + i, min_q=30.0, sps=0.267) for i in range(20)]
    rc, out = go(idle_world(*base, *slow), tmp=tmp_path / "slow", now=T)
    assert rc == 0 and alerts(out) == [], out
    assert "INFO  no plain trigger in 6 h; 20 " + DRAINED + "20 " + AMBIGUOUS + "; 128 plain pools closed with a min_q; " in out, out
    assert "quiet market" not in out and no_per_pool_values(out)
    # ... and alongside one missed candidate, both are counted; it still takes 2 candidates to alert
    rc, out = go(idle_world(*base, *slow, s_pool(T - 1800, 600, min_q=10.0)), tmp=tmp_path / "slow_and_one", now=T)
    assert alerts(out) == [] and ("21 " + DRAINED + "1 " + CAND + "; 20 " + AMBIGUOUS + "; 129 plain pools closed with a min_q; ") in out, out
    rc, out = go(idle_world(*base, *slow, s_pool(T - 1800, 600, min_q=10.0), s_pool(T - 1700, 601, min_q=10.0)), tmp=tmp_path / "slow_and_two", now=T)
    assert alerts(out) == ["h5_detector_missed"] and missed_alert(2) in out, out
    # each of these, twice (so two would alert if they were missed): ambiguous
    for name, kw in {"sps 0.267": {"sps": 0.267}, "sps 0.2212 (331.8 s)": {"sps": 0.2212}, "gap True": {"gap": True},
                     "gap True, fast slots": {"gap": True, "sps": 0.2}, "gap None": {"gap": None}}.items():
        recs = [s_pool(T - 3600 - i, 500 + i, min_q=30.0, **kw) for i in range(2)]
        rc, out = go(idle_world(*base, *recs), tmp=tmp_path / re.sub(r"\W", "_", name), now=T)
        assert rc == 0 and alerts(out) == [] and "2 " + DRAINED + "2 " + AMBIGUOUS + "; 110 plain pools closed with a min_q" in out, (name, out)
    rc, out = go(idle_world(*base, *({k: v for k, v in s_pool(T - 3600 - i, 500 + i, min_q=30.0).items() if k != "gap"} for i in range(2))),
                 tmp=tmp_path / "no_gap_key", now=T)
    assert alerts(out) == [] and "2 " + AMBIGUOUS in out, out
    # 330 s exactly (0.22 s/slot) is inside the bound: missed
    rc, out = go(idle_world(*base, *(s_pool(T - 3600 - i, 500 + i, min_q=30.0, sps=0.22) for i in range(2))), tmp=tmp_path / "edge", now=T)
    assert alerts(out) == ["h5_detector_missed"] and missed_alert(2) in out
    # a numeric min_q above 40 is quiet whatever the window (the min over 400 s bounds the min over 300 s from below), and so is a gapped one;
    # a drained pool with a pv trigger is triggered whatever its window or gap
    for name, recs in {"slow quiet": quiet_pools(sps=0.267), "gapped quiet": quiet_pools(gap=True),
                       "slow triggered": (*base, *(s_pool(T - 3600 - i, 500 + i, min_q=30.0, sps=0.267, gap=True, triggered=["pv"]) for i in range(2)))}.items():
        rc, out = go(idle_world(*recs), tmp=tmp_path / name.replace(" ", "_"), now=T)
        assert rc == 0 and alerts(out) == [] and NONE_DRAINED in out and "ambiguous" not in out, (name, out)
    counts = dc.shadow_idle_counts(idle_world(*quiet_pools(sps=0.267)), SHADOW_DIR, T)[0]
    assert (counts["judged"], counts["missed"], counts["ambiguous"]) == (108, 0, 0)
    # ambiguous pools count toward the judged quorum (30), withheld ones do not
    rc, out = go(idle_world(*quiet_pools(29), s_pool(T - 600, 500, min_q=30.0, sps=0.267)), tmp=tmp_path / "quorum", now=T)
    assert alerts(out) == [] and "1 " + DRAINED + "1 " + AMBIGUOUS + "; 30 plain pools closed with a min_q" in out, out
    rc, out = go(idle_world(*quiet_pools(29), s_pool(T - 600, 500, min_q=None)), tmp=tmp_path / "quorum_withheld", now=T)
    assert alerts(out) == ["h5_idle_ledger"] and "only 29 of 30 plain pool closes carry a min_q_pv_sol" in out


def test_idle_ledger_a_drained_pool_with_no_usable_sps_is_a_missed_candidate(tmp_path):
    """#589 review R-B1: the rule cannot fire without sps (the shadow's sps_ok: a finite number in the open interval (0.15, 0.6)). A drained,
    untriggered, plain close whose sps_at_s0 fails it is a missed candidate, gap or no gap; so is every pool with a skipped_no_sps record."""
    base = quiet_pools()
    for name, kw in {"sps None": {"sps": None}, "sps 0.7": {"sps": 0.7}, "sps a string": {"sps": "0.217"}, "sps a bool": {"sps": True},
                     "sps NaN": {"sps": float("nan")}, "sps 0.15 (open)": {"sps": 0.15}, "sps 0.6 (open)": {"sps": 0.6}, "sps 0.1": {"sps": 0.1},
                     "sps None with a gap": {"sps": None, "gap": True}, "sps 0.7 with sps_last 0.2": {"sps": 0.7, "sps_last": 0.2}}.items():
        recs = [s_pool(T - 3600 - i, 500 + i, min_q=30.0, **kw) for i in range(2)]
        rc, out = go(idle_world(*base, *recs), tmp=tmp_path / re.sub(r"\W", "_", name), now=T)
        assert rc == 1 and alerts(out) == ["h5_detector_missed"] and missed_alert(2, 2) in out and no_per_pool_values(out), (name, out)
    rc, out = go(idle_world(*base, *({k: v for k, v in s_pool(T - 3600 - i, 500 + i, min_q=30.0).items() if k != "sps_at_s0"} for i in range(2))),
                 tmp=tmp_path / "no_sps_key", now=T)
    assert alerts(out) == ["h5_detector_missed"] and missed_alert(2, 2) in out, out
    # one alone: an INFO
    rc, out = go(idle_world(*base, s_pool(T - 3600, 500, min_q=30.0, sps=None)), tmp=tmp_path / "one", now=T)
    assert rc == 0 and alerts(out) == [] and "INFO  no plain trigger in 6 h; 1 " + DRAINED + "1 " + CAND in out, out
    # two pools with a skipped_no_sps record: an alert, whatever their closes say (here none in the window)
    rc, out = go(idle_world(*base, s_skipped(T - 3600, 700), s_skipped(T - 1800, 701)), tmp=tmp_path / "skipped", now=T)
    assert rc == 1 and alerts(out) == ["h5_detector_missed"] and missed_alert(2, 2) in out and no_per_pool_values(out), out
    # union by pool: a skipped_no_sps pool whose close is also a no-sps (or in-window) candidate counts once; two records of one pool count once
    for name, recs in {"close too": (s_skipped(T - 3600, 700), s_pool(T - 3300, 700, min_q=30.0, sps=None)),
                       "in-window close too": (s_skipped(T - 3600, 700), s_pool(T - 3300, 700, min_q=30.0)),
                       "ambiguous close too": (s_skipped(T - 3600, 700), s_pool(T - 3300, 700, min_q=30.0, gap=True)),
                       "twice": (s_skipped(T - 3600, 700), s_skipped(T - 3500, 700))}.items():
        rc, out = go(idle_world(*base, *recs), tmp=tmp_path / name.replace(" ", "_").replace("-", "_"), now=T)
        assert alerts(out) == [] and "1 " + DRAINED + "1 " + CAND in out and "ambiguous" not in out, (name, out)
    # a skipped_no_sps record before the window is not counted
    rc, out = go(idle_world(*base, s_skipped(T - 6 * 3600 - 60, 700), s_skipped(T - 1800, 701)), tmp=tmp_path / "skipped_old", now=T)
    assert alerts(out) == [] and "1 " + DRAINED + "1 " + CAND in out, out
    counts = dc.shadow_idle_counts(idle_world(*base, s_skipped(T - 3600, 700), s_pool(T - 3300, 701, min_q=30.0)), SHADOW_DIR, T)[0]
    assert (counts["missed"], counts["missed_nosps"], counts["ambiguous"]) == (2, 1, 0)


def test_idle_ledger_excluded_pools_and_sps_last_in_the_miss_test(tmp_path):
    """#589 review R-F: a pool excluded as synthetic or unclassified at decision time can close plain with an empty `triggered`: never missed or
    ambiguous. The 1500-slot window is judged at the larger of sps_at_s0 and sps_last when both are finite numbers."""
    base = quiet_pools()
    drained = [s_pool(T - 3600 - i, 500 + i, min_q=30.0) for i in range(2)]  # would be h5_detector_missed
    for name, exc in {"unclassified": [s_excluded(T - 3600 - 200 - i, 500 + i) for i in range(2)],
                      "synthetic": [s_excluded(T - 3600 - 200 - i, 500 + i, reason="synthetic") for i in range(2)]}.items():
        rc, out = go(idle_world(*base, *exc, *drained), tmp=tmp_path / name, now=T)
        assert rc == 0 and alerts(out) == [], (name, out)
        assert QUIET + "110 plain pools closed with a min_q, " + NONE_DRAINED in out and no_per_pool_values(out), (name, out)
    rc, out = go(idle_world(*base, s_excluded(T - 3800, 500), *drained), tmp=tmp_path / "one_excluded", now=T)
    assert alerts(out) == [] and "1 " + DRAINED + "1 " + CAND in out, out
    # an exclusion just before the window, for a pool that closes in it (the window's start 3 min into its hour): still seen
    t3 = T - 22 * 60  # 00:03Z: the window starts at 18:03Z
    early = [s_pool(t3 - 6 * 3600 + 100 + i, 500 + i, min_q=30.0) for i in range(2)]
    exc = [s_excluded(t3 - 6 * 3600 - 300 + i, 500 + i) for i in range(2)]  # 17:58Z: the hour before the window's first
    for now, tag in ((t3, "pre"), (SEAL_START + 63 * 60, "seal")):  # 00:03Z before the seal, 02:03Z in it
        shift = now - t3
        mv = [{**r, "t_ms": r["t_ms"] + ms(shift)} for r in (*early, *exc)]
        rc, out = go(idle_world(*quiet_pools(now=now - 240), *mv, now=now), tmp=tmp_path / f"lead_{tag}", now=now)
        assert alerts(out) == [] and "none drained to <= 40 SOL" in out, (tag, out)
        rc, out = go(idle_world(*quiet_pools(now=now - 240), *mv[:2], now=now), tmp=tmp_path / f"lead_{tag}_none", now=now)
        assert alerts(out) == ["h5_detector_missed"], (tag, out)  # without the exclusions: two candidates
    # sps_last: the window at max(sps_at_s0, sps_last)
    for name, (sps_last, want) in {"slower last (400 s)": (0.267, "ambiguous"), "faster last": (0.2, "missed"), "last None": (None, "missed"),
                                   "last a string": ("0.3", "missed"), "last NaN": (float("nan"), "missed"), "last Infinity": (float("inf"), "missed"),
                                   "last a bool": (True, "missed"),
                                   "last 0.2212 (331.8 s)": (0.2212, "ambiguous")}.items():
        recs = [s_pool(T - 3600 - i, 500 + i, min_q=30.0, sps_last=sps_last) for i in range(2)]
        rc, out = go(idle_world(*base, *recs), tmp=tmp_path / re.sub(r"\W", "_", name), now=T)
        if want == "missed":
            assert alerts(out) == ["h5_detector_missed"] and missed_alert(2) in out, (name, out)
        else:
            assert alerts(out) == [] and "2 " + DRAINED + "2 " + AMBIGUOUS in out, (name, out)


def test_idle_ledger_a_window_of_withheld_min_q_is_not_a_quiet_market(tmp_path):
    """#589 review B2: a pool whose min_q the shadow withholds (h5_look2_sealed; every pool from 2026-11-06T00Z) cannot show a missed drain, so
    it does not count toward the quiet verdict: too few judged pools keep the old alert."""
    for name, recs in {"all None": quiet_pools(min_q=None), "all look2": quiet_pools(look2=True),
                       "29 judged": (*quiet_pools(79, min_q=None), *spread(29, 5 * 3600, 600, first=500))}.items():
        rc, out = go(idle_world(*recs), tmp=tmp_path / name.replace(" ", "_"), now=T)
        assert rc == 1 and alerts(out) == ["h5_idle_ledger"] and OLD_IDLE in out and "quiet market" not in out, (name, out)
        judged = 29 if name == "29 judged" else 0
        assert f"(the shadow window could not be judged: only {judged} of 108 plain pool closes carry a min_q_pv_sol, {108 - judged} withheld)" in out
    rc, out = go(idle_world(*quiet_pools(78, min_q=None), *spread(30, 5 * 3600, 600, first=500)), tmp=tmp_path / "30", now=T)
    assert alerts(out) == [] and "30 plain pools closed with a min_q" in out and "(78 more with min_q withheld, not judged)" in out


def test_idle_ledger_too_few_plain_pools_means_the_detector_is_not_tracking(tmp_path):
    rc, out = go(idle_world(*spread(19, 6 * 3600 - 60, 2 * 3600 + 60), *spread(10, 2 * 3600 - 60, 60, first=100)), tmp=tmp_path / "29", now=T)
    assert rc == 1 and alerts(out) == ["h5_idle_ledger"], out
    assert ("no unledgered trigger and no missed drain, but 29 plain pool(s) closed in 6 h and 10 in the last 2 h (at least 30 and 10 expected): "
            "the detector is not tracking pools") in out
    rc, out = go(idle_world(*spread(20, 6 * 3600 - 60, 2 * 3600 + 60), *spread(10, 2 * 3600 - 60, 60, first=100)), tmp=tmp_path / "30", now=T)
    assert alerts(out) == [] and "30 plain pools closed with a min_q" in out and "10 plain closes in the last 2 h" in out
    # #589 review F3: a detector that writes hb but stopped tracking 2 h ago (plenty of closes before that) is seen within ~2 h, not 6
    rc, out = go(idle_world(*spread(100, 6 * 3600 - 60, 2 * 3600 + 60), *spread(9, 2 * 3600 - 60, 60, first=200)), tmp=tmp_path / "starved", now=T)
    assert alerts(out) == ["h5_idle_ledger"] and "but 109 plain pool(s) closed in 6 h and 9 in the last 2 h" in out
    others = (s_pool(T - 600, 900, synthetic=True), s_pool(T - 600, 901, synthetic=None), s_pool(T - 600, 902, src=None),
              {"type": "excluded", "reason": "synthetic", "pool": "POOLSECRETX", "v": 1, "t_ms": ms(T - 600)})
    rc, out = go(idle_world(*quiet_pools(29), *others), tmp=tmp_path / "others", now=T)
    assert "but 29 plain pool(s)" in out and "sealed" not in out  # synthetic, unclassified and ws-sourced closes and exclusions are not plain pools
    # EXP-022 sealed closes (from 2026-10-16T01Z) are not plain pools either; their count says why the plain count is low
    rc, out = go(idle_world(*quiet_pools(29), *(s_pool(T - 600 - i, 950 + i, sealed=True) for i in range(70))), tmp=tmp_path / "sealed", now=T)
    assert alerts(out) == ["h5_idle_ledger"] and "but 29 plain pool(s)" in out
    assert "; 70 EXP-022 sealed close(s) not counted (a pick, or no pick-oracle answer at close): the detector is not tracking pools" in out


def test_idle_ledger_unreadable_or_missing_shadow_keeps_the_old_alert(tmp_path):
    rc, out = go(idle_world(), tmp=tmp_path / "none", now=T)
    assert rc == 1 and alerts(out) == ["h5_idle_ledger"] and OLD_IDLE in out, out
    assert "(the shadow window could not be judged: no h5-shadow-<hour>.jsonl in /nowhere for the last 6 h)" in out
    for name, exc in {"link": dc.Unsafe("/nowhere/h5-shadow-x.jsonl is not a regular file (link)"),
                      "sudo": dc.SudoError("read /nowhere/h5-shadow-x.jsonl: not a path the privileged calls are allowed for"),
                      "oserror": PermissionError(13, "Permission denied")}.items():
        h = idle_world(*quiet_pools())
        h.unreadable[sorted(p for p in h.files if p.startswith(SHADOW_DIR + "/"))[2]] = exc  # one file of the window
        rc, out = go(h, tmp=tmp_path / name, now=T)
        assert rc == 1 and alerts(out) == ["h5_idle_ledger"] and OLD_IDLE in out and "quiet market" not in out, (name, out)
    assert "PermissionError" in out


def test_idle_ledger_a_missing_hour_in_the_window_keeps_the_old_alert(tmp_path):
    """#589 review F2: the detector down for a whole hour of the window (back now): the window was not seen, so it is not called quiet."""
    paths = sorted(dc.shadow_window_paths(SHADOW_DIR, T))
    for k in (0, 3):  # the window's first (partial) hour, and one in the middle
        h = idle_world(*quiet_pools(200))
        h.files.pop(paths[k])
        rc, out = go(h, tmp=tmp_path / f"gap{k}", now=T)
        assert rc == 1 and alerts(out) == ["h5_idle_ledger"] and OLD_IDLE in out and "quiet market" not in out, (k, out)
        assert "(the shadow window could not be judged: 1 of its 7 hourly files are missing, the detector wrote nothing for a whole hour)" in out
    h = idle_world(*quiet_pools(200), s_trigger(T - 3600))
    h.files.pop(paths[3])
    rc, out = go(h, tmp=tmp_path / "gap_and_trigger", now=T)
    assert alerts(out) == ["h5_idle_unledgered"]  # a trigger it did see without a ledger row still says so
    # the current hour's file may not exist yet in its first FEED_STALE_S (the next hb writes it): not missing
    t2 = T - 22 * 60
    rc, out = go(idle_world(*quiet_pools(now=t2 - 240), now=t2), tmp=tmp_path / "young_hour", now=t2)
    assert alerts(out) == [] and "quiet market" in out and "6 shadow file(s) read" in out, out


def test_idle_ledger_missing_or_empty_ledger_keeps_the_old_alert(tmp_path):
    """#589 review F1: with no ledger row at all there is nothing to judge the shadow against (the executor writes `start` when it starts)."""
    for name, body in {"deleted": None, "empty": b"", "torn": b'{"kind":"sta', "no ts": ledger({"kind": "start"})}.items():
        h = idle_world(*quiet_pools())
        h.mtimes[dc.LIVE_OK] = T - 10 * 3600
        if body is None:
            h.files.pop(f"{H5}/live/h5-ledger.jsonl")
        else:
            h.files[f"{H5}/live/h5-ledger.jsonl"] = body
        rc, out = go(h, tmp=tmp_path / name.replace(" ", "_"), now=T)
        assert rc == 1 and "h5_idle_ledger" in alerts(out) and "quiet market" not in out, (name, out)
        assert "LIVE_OK is 10 h old and " + OLD_IDLE + " (the live ledger is missing or holds no row: the shadow was not consulted)" in out


def test_idle_ledger_24_h_backstop(tmp_path, monkeypatch):
    quiet = quiet_pools()
    old = ({"kind": "start", "user": dc.WALLET, "ts_ms": ms(T - 28 * 3600)}, {"kind": "decision", "ts_ms": ms(T - 25 * 3600)}, {"kind": "feed_gap", "ts_ms": ms(T - 60)})
    rc, out = go(idle_world(*quiet, ledger_rows=old), tmp=tmp_path / "old", now=T)
    assert rc == 1 and alerts(out) == ["h5_idle_ledger"] and "quiet market" not in out, out
    assert "no buy, skip or decision row in the last 24 h (the 24 h backstop, whatever the shadow shows)" in out
    h = idle_world(*quiet, ledger_rows=old)
    h.mtimes[dc.LIVE_OK] = T - 20 * 3600  # the gate is younger than 24 h: the 6 h logic alone
    rc, out = go(h, tmp=tmp_path / "young", now=T)
    assert alerts(out) == [] and "quiet market" in out
    feed_only = ({"kind": "start", "user": dc.WALLET, "ts_ms": ms(T - 28 * 3600)},
                 *({"kind": "feed_gap", "ts_ms": ms(T - 3600 * k)} for k in range(27, 0, -1)))
    rc, out = go(idle_world(*quiet, ledger_rows=feed_only), tmp=tmp_path / "never", now=T)
    assert alerts(out) == ["h5_idle_ledger"] and "24 h backstop" in out  # the whole ledger was read: no such row at all

    class Tail(FakeHost):  # the real Host's tail: the last `tail` bytes, the first row torn
        def read(self, path, tail=None):
            raw = super().read(path, tail)
            return raw[-tail:] if raw is not None and tail else raw

    row = len(ledger({"kind": "feed_gap", "ts_ms": ms(T - 3600)}))
    monkeypatch.setattr(dc, "LEDGER_TAIL", row * 10 + 7)  # about the last 10 hours of this ledger
    rc, out = go(idle_world(*quiet, ledger_rows=feed_only, cls=Tail), tmp=tmp_path / "short_tail", now=T)
    assert alerts(out) == [] and "quiet market" in out, out  # the newest row is unknown: the backstop is not fired from that alone
    assert "reaches back less than 24 h and holds no buy, skip or decision row: the 24 h backstop was not judged" in out
    # ... but a trigger newer than the tail's oldest row is unledgered for sure (a row for it would be in the tail)
    rc, out = go(idle_world(*quiet, s_trigger(T - 8 * 3600), ledger_rows=feed_only, cls=Tail), tmp=tmp_path / "short_tail_trigger", now=T)
    assert alerts(out) == ["h5_idle_unledgered"] and "in the last 10 h" in out, out
    rc, out = go(idle_world(*quiet, s_trigger(T - 12 * 3600), ledger_rows=feed_only, cls=Tail), tmp=tmp_path / "short_tail_older", now=T)
    assert alerts(out) == [] and "quiet market" in out  # older than the tail: its row may be before it
    monkeypatch.setattr(dc, "LEDGER_TAIL", row * 26 + 7)  # back to T - 26 h
    rc, out = go(idle_world(*quiet, ledger_rows=feed_only, cls=Tail), tmp=tmp_path / "long_tail", now=T)
    assert alerts(out) == ["h5_idle_ledger"] and "24 h backstop" in out
    monkeypatch.setattr(dc, "LEDGER_TAIL", row * 10 + 7)  # a short tail that holds an old decision row: the newest is known
    stale = ({"kind": "decision", "ts_ms": ms(T - 25 * 3600)}, *({"kind": "feed_gap", "ts_ms": ms(T - 3600 * k)} for k in range(9, 0, -1)))
    rc, out = go(idle_world(*quiet, ledger_rows=stale, cls=Tail), tmp=tmp_path / "known", now=T)
    assert alerts(out) == ["h5_idle_ledger"] and "24 h backstop" in out


def test_idle_constants_follow_the_shadow_and_the_executor():
    from tools import h5_executor, h5_shadow

    assert dc.Q_STAR_SOL == h5_shadow.Q_STAR_SOL
    # the EXP-022 seal switch of the idle check is the executor's seal start (QP-B1/B3 on #589), 2026-10-16T01:00:00Z
    assert dc.IDLE_SEAL_START_S * 1000 == h5_executor.SEAL_START_MS and dc.IDLE_SEAL_START_S == SEAL_START
    assert not dc.idle_sealed(SEAL_START - 1) and dc.idle_sealed(SEAL_START) and dc.idle_sealed(h5_executor.SEAL_END_DEFAULT_MS / 1000 + 365 * 86400)
    # the shadow's sps_ok interval (R-B1), and the lead before the window for exclusions (a pool closes at most WALL_CLOSE_S after s0)
    assert (dc.SPS_LO, dc.SPS_HI) == (h5_shadow.SPS_LO, h5_shadow.SPS_HI) == (0.15, 0.6)
    assert all(dc._sps_usable(v) == h5_shadow.sps_ok(v) for v in (0.1501, 0.217, 0.5999, 0.15, 0.6, 0.1, 0.7, None))
    assert h5_shadow.WALL_CLOSE_S < dc.IDLE_EXCLUDED_LEAD_S == 600
    # min_q_pv's window is an inline literal in the shadow's _on_print: pinned by its line, which must still set min_q_pv
    lines = (ROOT / "tools/h5_shadow.py").read_text().splitlines()
    at = [i for i, line in enumerate(lines) if f"if pr.slot - p.s0 <= {dc.IDLE_MINQ_SLOTS}:" in line]
    assert len(at) == 1 and lines[at[0] + 1].strip().startswith("p.min_q_pv = qpv if p.min_q_pv is None else min(p.min_q_pv, qpv)")
    shadow = "\n".join(lines)
    assert shadow.count("p.min_q_pv = ") == 1  # nothing else moves min_q_pv
    # the trigger window (300 s) the 330 s bound leaves 30 s past, and the pool-close fields the miss test reads (from _close's plain record)
    assert (h5_shadow.T_MIN_S, h5_shadow.T_MAX_S) == (0.0, 300.0)
    assert (dc.IDLE_MINQ_SLOTS, dc.IDLE_MINQ_WINDOW_MAX_S, dc.IDLE_MISSED_MIN) == (1500, 330.0, 2)
    close = shadow.split("    def _close(self, p: Pool, reason: str) -> None:\n", 1)[1].split("\n    def ", 1)[0]
    plain = close.split("        rec = {\n", 1)[1]
    assert all(f in plain for f in ('"sps_at_s0": p.sps0, "sps_last": p.sps_last,', '"gap": bool(p.gaps),',
                                    '"min_q_pv_sol": None if (sealed or h5s or p.min_q_pv is None) else p.min_q_pv / 1e9,',
                                    '"triggered": None if sealed else sorted(v for v, r in p.trig.items() if "excluded" not in r),'))
    # the record types the miss test reads besides the closes: an exclusion (pool named) and a skipped_no_sps (pool named, non-sealed only)
    assert 'self.emit({"type": "excluded", "reason": reason, "pool": p.pool,' in shadow
    assert 'rec = {"type": "skipped_no_sps", "pool": p.pool,' in shadow
    ex = (ROOT / "tools/h5_executor.py").read_text()
    assert f'\nTRIGGER_VARIANT = "{dc.SHADOW_TRIGGER_VARIANT}"' in ex and f'\nSYNTHETIC_SRC_OK = "{dc.SHADOW_PLAIN_SRC}"' in ex
    assert f'self._log("{dc.IDLE_SEAL_KIND}", "", seal_skips=' in ex  # the executor's count row for seal refusals
    assert (dc.IDLE_LEDGER_S, dc.IDLE_LEDGER_MAX_S, dc.IDLE_TRIGGER_GRACE_S, dc.IDLE_MIN_PLAIN_POOLS, dc.IDLE_RECENT_S, dc.IDLE_RECENT_MIN_PLAIN_POOLS) == (
        6 * 3600, 24 * 3600, 300, 30, 2 * 3600, 10)
    assert [os.path.basename(p) for p in dc.shadow_window_paths("/d", T)] == [
        f"h5-shadow-{time.strftime('%Y-%m-%dT%H', time.gmtime(T - 6 * 3600 + 3600 * k))}.jsonl" for k in range(7)]
    assert dc.TRIGGER_MARK == b'{"type":"trigger"'


def test_shadow_idle_counts_reads_real_shadow_records_only_in_the_window_and_never_follows_a_link(tmp_path):
    from tools import h5_shadow

    d = tmp_path / "h5-shadow"
    sink = h5_shadow.JsonlSink(d)
    for r in (s_trigger(T - 3600), s_trigger(T - 3600 + 1, variant="fv"), *(s_pool(T - 3600 * (i % 5) - 100, i) for i in range(40)),
              s_pool(T - 600, 99, min_q=39.0), s_trigger(T - 10 * 3600, pool="POOLSECRET7"), s_pool(T - 10 * 3600, 98, min_q=1.0)):  # the last two: before the window
        sink.write(r)
    sink.close()
    with open(d / f"h5-shadow-{time.strftime('%Y-%m-%dT%H', time.gmtime(T))}.jsonl", "a") as fh:
        fh.write('{"type":"pool","reason":"done","synthetic":false,"synthetic_src":"rpc","min_q_pv_sol":3')  # a line still being written
    counts, why = dc.shadow_idle_counts(dc.Host(), str(d), T)
    assert counts == {"files": 5, "hours": 7, "missing": 2, "unledgered": 1, "triggers_window": 1, "plain_pools": 41, "plain_recent": 17,
                      "judged": 41, "missed": 1, "missed_nosps": 0, "ambiguous": 0, "sealed": 0}, why
    # looking further back for triggers (B1) reads the older files for triggers only: the window's pool counts do not change
    back = dc.shadow_idle_counts(dc.Host(), str(d), T, T - 11 * 3600)[0]
    assert back == {**counts, "files": 6, "unledgered": 2}
    early = d / f"h5-shadow-{time.strftime('%Y-%m-%dT%H', time.gmtime(T - 10 * 3600))}.jsonl"
    early.unlink()
    early.symlink_to(tmp_path / "elsewhere.jsonl")  # outside the window: never opened
    assert dc.shadow_idle_counts(dc.Host(), str(d), T)[0] == counts
    counts_b, why = dc.shadow_idle_counts(dc.Host(), str(d), T, T - 11 * 3600)  # ... unless the trigger look-back reaches it: refused, not followed
    assert counts_b is None and "is not a regular file (link)" in why
    inside = d / f"h5-shadow-{time.strftime('%Y-%m-%dT%H', time.gmtime(T - 3600))}.jsonl"
    (tmp_path / "real.jsonl").write_bytes(inside.read_bytes())
    inside.unlink()
    inside.symlink_to(tmp_path / "real.jsonl")
    counts2, why = dc.shadow_idle_counts(dc.Host(), str(d), T)
    assert counts2 is None and "is not a regular file (link)" in why and "SECRET" not in why
    inside.unlink()
    os.link(tmp_path / "real.jsonl", inside)
    counts3, why = dc.shadow_idle_counts(dc.Host(), str(d), T)
    assert counts3 is None and "hard links" in why
    assert dc.shadow_idle_counts(dc.Host(), str(tmp_path / "nope"), T) == (None, f"no h5-shadow-<hour>.jsonl in {tmp_path / 'nope'} for the last 6 h")


def test_shadow_idle_counts_in_the_seal_window_on_real_shadow_records(tmp_path):
    """QP-B1/B3: the same reader on real JsonlSink output in the seal window: no trigger key, the trigger records change nothing, and a file
    before the window (where the pre-seal look-back would go for triggers) is never opened, even as a symlink."""
    from tools import h5_shadow

    d = tmp_path / "h5-shadow"
    sink = h5_shadow.JsonlSink(d)
    recs = [*(s_pool(T_SEAL - 3600 * (i % 5) - 100, i) for i in range(40)), s_pool(T_SEAL - 600, 99, min_q=39.0),
            s_excluded(T_SEAL - 900, 98), s_pool(T_SEAL - 700, 98, min_q=1.0), s_skipped(T_SEAL - 1200, 97)]
    for r in (*recs, s_trigger(T_SEAL - 3600), s_trigger(T_SEAL - 1800, pool="POOLSECRET5")):
        sink.write(r)
    sink.close()
    counts, why = dc.shadow_idle_counts(dc.Host(), str(d), T_SEAL, T_SEAL - 11 * 3600)
    assert counts == {"files": 5, "hours": 7, "missing": 2, "plain_pools": 42, "plain_recent": 18, "judged": 42, "missed": 2, "missed_nosps": 1,
                      "ambiguous": 0, "sealed": 0}, why
    early = d / f"h5-shadow-{time.strftime('%Y-%m-%dT%H', time.gmtime(T_SEAL - 10 * 3600))}.jsonl"
    early.symlink_to(tmp_path / "elsewhere.jsonl")
    assert dc.shadow_idle_counts(dc.Host(), str(d), T_SEAL, T_SEAL - 11 * 3600)[0] == counts


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
        "mode exactly 0644, not tighter (mal-live must be able to read it)": lambda h: h.modes.__setitem__(dc.LIVE_OK, "root:root:600"),
        "mode 0640": lambda h: h.modes.__setitem__(dc.LIVE_OK, "root:root:640"),
        "mode 0444": lambda h: h.modes.__setitem__(dc.LIVE_OK, "root:root:444"),
        "mode 0755": lambda h: h.modes.__setitem__(dc.LIVE_OK, "root:root:755"),
        "group is not root": lambda h: h.modes.__setitem__(dc.LIVE_OK, "root:mal-live:644"),
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


def test_wallet_wide_stop_and_halt_follow_the_executor(tmp_path):
    """At executor 4f05e30 /var/lib/mal-live/STOP and HALT (the probe's file names) are wallet-wide switches next to the H5 ones."""
    assert dc.WALLET_STOP == "/var/lib/mal-live/STOP" and dc.WALLET_HALT == "/var/lib/mal-live/HALT"
    rc, out = go(FakeHost(), tmp=tmp_path / "clean")
    assert rc == 0 and "wallet_STOP=no wallet_HALT=no" in out
    h = FakeHost()
    h.files[dc.WALLET_STOP] = b""  # the probe's leftover STOP: H5 would never buy
    rc, out = go(h, tmp=tmp_path / "gate_open")
    assert rc == 1 and "h5_idle" in alerts(out) and "wallet-wide /var/lib/mal-live/STOP exists" in out and "wallet_STOP=yes" in out
    h = FakeHost()
    h.files[dc.WALLET_STOP] = b""
    h.files.pop(dc.LIVE_OK), h.modes.pop(dc.LIVE_OK), h.h5_props.update(ActiveState="inactive")
    rc, out = go(h, tmp=tmp_path / "gate_closed")
    assert "h5_idle" not in alerts(out) and "wallet_STOP=yes" in out  # before go-live it is only reported
    # its absence is no longer an alert (the old probe check required it)
    assert "STOP is missing" not in out
    h = FakeHost()
    h.files[dc.WALLET_HALT] = b""
    assert "wallet_halt_file" in alerts(go(h, tmp=tmp_path / "halt")[1])


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
    base.write_text(json.dumps({"sha256": hashlib.sha256(PROBE_STATE).hexdigest(), "dec020_sha256": "absent"}))
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
def locked(tmp_path, monkeypatch):
    """A path that a non-root user cannot even lstat (parent mode 000): the code must go through sudo. It is added to the fixed lists."""
    if os.geteuid() == 0:
        pytest.skip("needs a non-root user")
    d = tmp_path / "locked"
    d.mkdir()
    (d / "f").write_text("{}")
    d.chmod(0)
    monkeypatch.setattr(dc.Host, "priv_stat", (*dc.PRIV_STAT, str(d / "f")))
    monkeypatch.setattr(dc.Host, "priv_read", (*dc.PRIV_READ, str(d / "f")))
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
    assert h.calls[1] == ("/usr/bin/dd", "iflag=nofollow", "status=none", f"if={locked}")  # no varying operand (skip=): a sudoers rule can pin it exactly
    assert all(c[0].startswith("/usr/bin/") and c[0] != "/usr/bin/cat" for c in h.calls)
    h2 = SudoHost(lambda argv: cp(0, b"regular file|1|mal-live|mal-live|600|1800000000|12\n") if argv[0] == "/usr/bin/stat" else cp(0, b"0123456789AB"))
    assert h2.read(locked, tail=5) == b"789AB"  # the tail is cut in Python from the whole (size-capped) file
    assert h2.calls[1] == ("/usr/bin/dd", "iflag=nofollow", "status=none", f"if={locked}")


def test_privileged_calls_only_for_the_fixed_paths_and_never_for_a_file_over_8_mb(locked, tmp_path):
    other = str(tmp_path / "locked" / "other")
    h = SudoHost(lambda argv: cp(0, b"regular file|1|root|root|600|1800000000|5\n"))
    with pytest.raises(dc.SudoError, match="not a path the privileged calls are allowed for"):
        h.lstat(other)
    assert h.calls == []  # nothing was run for a path that is not on the list
    big = SudoHost(lambda argv: cp(0, f"regular file|1|mal-live|mal-live|600|1800000000|{dc.MAX_READ + 1}\n".encode()))
    with pytest.raises(dc.Unsafe, match="larger than"):
        big.read(locked)
    assert [c[0] for c in big.calls] == ["/usr/bin/stat"]  # refused on the size from stat, before any read


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


# ---- EXP-024 Amendment 2: the shadow must run with the declared observation inside [2026-10-16T00Z, 2026-11-06T00Z) -----------------
WIN = 1_792_108_800 + 3600  # 2026-10-16T01:00:00Z, inside the window


def look2_alerts(host, now, live_ok=True):
    lines: list[str] = []
    rep = dc.Report(lines.append)
    dc.check_look2_flag(host, rep, live_ok, "/shadow", now)
    return [n for n, _ in rep.alert_list], lines


def test_look2_window_constants_equal_the_shadows():
    from tools import h5_shadow

    assert (dc.LOOK2_START_S * 1000, dc.LOOK2_END_S * 1000) == (h5_shadow.H5_LOOK2_START_MS, h5_shadow.H5_LOOK2_END_MS)
    assert dc.START_MARK == b'{"type":"start"'  # the shadow's JsonlSink writes compact JSON with "type" first


def test_look2_flag_unset_after_1016_with_live_ok_alerts():
    h = FakeHost()
    for start, why in ((None, "no start record was found"), ({"type": "start"}, "has no h5_look2 field"),
                       ({"type": "start", "h5_look2": {"observed": False, "amendment_ref": None}}, "shows h5_look2.observed false")):
        h.shadow_start = start
        names, lines = look2_alerts(h, WIN)
        assert names == ["h5_look2_not_observed"], why
        assert "H5_LOOK2_OBSERVED=EXP-024-Am2" in lines[0] and why in lines[0], lines[0]
    h.shadow_start = {"type": "start", "h5_look2": {"observed": "yes"}}  # only the boolean true counts
    assert look2_alerts(h, WIN)[0] == ["h5_look2_not_observed"]


def test_look2_flag_set_is_ok_and_silent():
    names, lines = look2_alerts(FakeHost(), WIN)
    assert names == [] and lines[0].startswith("OK") and "Look 2 observation" in lines[0]


def test_look2_flag_only_matters_inside_the_window_with_live_ok_and_a_feed():
    h = FakeHost()
    h.shadow_start = {"type": "start", "h5_look2": {"observed": False}}
    assert look2_alerts(h, dc.LOOK2_START_S - 1)[0] == []  # before 10-16T00Z
    assert look2_alerts(h, dc.LOOK2_START_S)[0] == ["h5_look2_not_observed"]  # exactly 10-16T00:00:00Z
    assert look2_alerts(h, dc.LOOK2_END_S - 1)[0] == ["h5_look2_not_observed"]
    assert look2_alerts(h, dc.LOOK2_END_S)[0] == []  # the declared window has ended: the flag changes nothing from 11-06T00Z
    assert look2_alerts(h, WIN, live_ok=False)[0] == []  # gate closed: no canary
    h.feed = None
    assert look2_alerts(h, WIN)[0] == []  # no feed at all is h5_feed_stale's alert


def test_look2_flag_is_a_step_of_the_run_and_never_prints_the_record(tmp_path):
    h = FakeHost()
    h.shadow_start = {"type": "start", "h5_look2": {"observed": False}, "argv": ["--ws-url", "wss://SECRETHOST/key"], "out_dir": "/home/x/SECRETDIR"}
    lines: list[str] = []
    rep = dc.run_checks(dc.build_parser().parse_args(["--shadow-dir", "/x", "--baseline", str(tmp_path / "b.json")]), h, lines.append, lambda w, e: 0, WIN)
    assert "h5_look2_not_observed" in [n for n, _ in rep.alert_list]
    assert "SECRET" not in "\n".join(lines)


def shadow_line(**kw) -> str:
    return json.dumps({"type": "start", "v": 1, "t_ms": 1, **kw}, separators=(",", ":")) + "\n"


def test_the_cap_pick_subdirectory_is_ignored_and_raises_no_feed_alert(tmp_path):
    # CAP_PICK_OUT=$HOME/data/h5-shadow/cap-pick: the exporter's subdirectory sits in the shadow directory. It is never an hourly file, never
    # makes a dead detector look fresh, and never by itself raises an h5_feed_* alert.
    d = tmp_path / "h5-shadow"
    (d / "cap-pick").mkdir(parents=True)
    hour = d / "h5-shadow-2026-10-16T03.jsonl"
    hour.write_text(shadow_line(h5_look2={"observed": False}) + '{"type":"hb"}\n')
    (d / "cap-pick" / "picks.jsonl").write_text(shadow_line(h5_look2={"observed": True}) + '{"hb":true,"t_ms":1}\n')
    os.utime(d / "cap-pick" / "picks.jsonl", (NOW, NOW))
    os.utime(d / "cap-pick", (NOW, NOW))
    assert dc.Host().newest_hourly(str(d))[0] == hour.name
    assert dc.Host().newest_shadow_start(str(d))["h5_look2"] == {"observed": False}

    class H(FakeHost):
        newest_hourly = dc.Host.newest_hourly

    os.utime(hour, (NOW - 60, NOW - 60))
    rc, out = go(H(), "--shadow-dir", str(d), tmp=tmp_path / "fresh")
    assert not [a for a in alerts(out) if a.startswith("h5_feed")], out
    assert f"shadow feed fresh ({hour.name}, 60 s)" in out
    os.utime(hour, (NOW - 11 * 60, NOW - 11 * 60))  # the detector stopped; the exporter still writes
    rc, out = go(H(), "--shadow-dir", str(d), tmp=tmp_path / "stale")
    assert "h5_feed_stale" in alerts(out) and hour.name in out and "cap-pick" not in out
    hour.unlink()
    rc, out = go(H(), "--shadow-dir", str(d), tmp=tmp_path / "none")
    assert "h5_feed_stale" in alerts(out) and "no h5-shadow-<hour>.jsonl" in out


def test_newest_shadow_start_reads_the_last_start_record_of_the_newest_file_that_has_one(tmp_path):
    d = tmp_path
    (d / "h5-shadow-2026-10-16T01.jsonl").write_text(shadow_line(h5_look2={"observed": False}) + '{"type":"hb"}\n')
    (d / "h5-shadow-2026-10-16T02.jsonl").write_text('{"type":"hb"}\n' + shadow_line(h5_look2={"observed": False}) + '{"type":"pool"}\n'
                                                      + shadow_line(h5_look2={"observed": True}) + '{"type":"hb"}\n')  # a restart in the same hour: the last one wins
    (d / "h5-shadow-2026-10-16T03.jsonl").write_text('{"type":"hb"}\n{"type":"pool"}\n')  # newest file, no start record: look back
    (d / "h5-shadow-status.json").write_text(shadow_line(h5_look2={"observed": False}))  # not an hourly file
    rec = dc.Host().newest_shadow_start(str(d))
    assert rec["type"] == "start" and rec["h5_look2"] == {"observed": True}
    assert dc.Host().newest_shadow_start(str(d / "nope")) is None
    (d / "h5-shadow-2026-10-16T04.jsonl").write_text(shadow_line(h5_look2={"observed": False}))
    assert dc.Host().newest_shadow_start(str(d))["h5_look2"] == {"observed": False}  # a newer file with a start record wins


def test_newest_shadow_start_ignores_the_marker_inside_another_record_a_torn_line_and_unsafe_files(tmp_path):
    d = tmp_path
    inner = json.dumps({"type": "error", "msg": '{"type":"start","h5_look2":{"observed":true}}'}, separators=(",", ":"))  # marker text inside a record
    (d / "h5-shadow-2026-10-16T01.jsonl").write_text(shadow_line(h5_look2={"observed": False}) + inner + "\n"
                                                      + '{"type":"start","h5_look2":{"observed":tr')  # the last line is still being written
    assert dc.Host().newest_shadow_start(str(d))["h5_look2"] == {"observed": False}
    (d / "h5-shadow-2026-10-16T02.jsonl").write_text("")  # an empty file is skipped, not an error
    assert dc.Host().newest_shadow_start(str(d))["h5_look2"] == {"observed": False}
    real = d / "real.txt"
    real.write_text(shadow_line(h5_look2={"observed": True}))
    (d / "h5-shadow-2026-10-16T03.jsonl").symlink_to(real)  # a symlink is never followed
    assert dc.Host().newest_shadow_start(str(d))["h5_look2"] == {"observed": False}
    os.link(real, d / "h5-shadow-2026-10-16T04.jsonl")  # a hard-linked file is refused
    assert dc.Host().newest_shadow_start(str(d))["h5_look2"] == {"observed": False}


def test_newest_shadow_start_of_a_real_shadow_start_record_round_trips(tmp_path):
    from tools import h5_shadow

    rec = {"type": "start", "rule": h5_shadow.RULE_ID, "argv": ["--h5-look2-observed", "EXP-024-Am2"], "h5_look2": h5_shadow.look2_start_info("EXP-024-Am2")}
    sink = h5_shadow.JsonlSink(tmp_path)
    sink.write({**rec, "v": 1, "t_ms": 1_792_112_400_000})
    sink.close()
    got = dc.Host().newest_shadow_start(str(tmp_path))
    assert got["h5_look2"]["observed"] is True and got["h5_look2"]["amendment_ref"] == "EXP-024-Am2"


def test_script_is_read_only_and_keyless():
    src = (FAST / "h5-daily-check.py").read_text()
    assert "/etc/mal-probe/" not in src and "probe-wallet" not in src and "load_keypair" not in src and "solders" not in src
    assert "sendTransaction" not in src and "/usr/bin/cat" not in src
    assert src.count('"cat"') == 1 and 'host.systemctl("cat", unit, "--no-pager")' in src  # no file is ever cat'ed; `systemctl cat` is the one place, a read verb
    verbs = {l.split("systemctl(")[1].split(",")[0].strip('")').strip('"') for l in src.splitlines() if "host.systemctl(" in l}
    assert verbs == {"cat", "list-unit-files", "list-units", "show"}, verbs  # read verbs only
    assert set(re.findall(r'"(/usr/bin/[a-z]+)"', src)) == {"/usr/bin/true", "/usr/bin/stat", "/usr/bin/dd", "/usr/bin/systemctl"}  # nothing else is ever run
    assert src.count("self._sudo(") == 3  # sudo runs only these three calls, built from fixed paths
    assert 'self._sudo("/usr/bin/true")' in src and "self._sudo(*stat_argv(path))" in src and "self._sudo(*dd_argv(path))" in src
    assert "iflag=nofollow" in src and "O_NOFOLLOW" in src
