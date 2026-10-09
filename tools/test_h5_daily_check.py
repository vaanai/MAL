"""Tests for scripts/mal-fast/h5-daily-check.py: a fake host (files, systemctl, getBalance). No sudo, no network, no key."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

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
ETC = "/etc/systemd/system"
H5 = dc.H5_DIR


class FakeHost(dc.Host):
    def __init__(self):
        self.files: dict[str, bytes] = {
            dc.PROBE_STATE: PROBE_STATE, dc.PROBE_STOP: b"",
            f"{dc.PINNED}/mal-h5-executor.service": BASE, f"{dc.PINNED}/mal-h5-executor-live-pinned.conf": DROPIN,
            f"{ETC}/mal-h5-executor.service": BASE, f"{ETC}/mal-h5-executor.service.d/live.conf": DROPIN,
            f"{ETC}/mal-h5-executor.service.d/10-shadow-feed.conf": FEED,
            f"{H5}/LIVE_OK": b"",
            f"{H5}/live/state-live.json": json.dumps({"attempts": 3, "realized_lamports": -1_000_000, "open": {}, "pending": {}}).encode(),
            f"{H5}/live/h5-counters.json": json.dumps({"halts": {}, "sells_landed": 3, "sells_late": 0}).encode(),
            f"{H5}/live/h5-ledger.jsonl": (json.dumps({"kind": "start", "user": dc.WALLET}) + "\n").encode(),
        }
        self.modes = {H5: "mal-live:mal-live:700"}
        self.unit_files = "mal-probe-executor.service masked -\n"
        self.units = ""
        self.h5_props = {"LoadState": "loaded", "ActiveState": "active", "SubState": "running", "UnitFileState": "enabled", "NRestarts": "0", "Result": "success"}
        self.execstart = "{ path=/usr/local/lib/mal-h5-exec/venv/bin/python ; argv[]=/usr/local/lib/mal-h5-exec/venv/bin/python -I -B -u /usr/local/lib/mal-h5-exec/current/launcher.py --live }"
        self.balance_lamports = FUNDED - 1_000_000

    def read(self, path):
        v = self.files.get(path)
        if isinstance(v, Exception):
            raise v
        return v

    def exists(self, path):
        return path in self.files

    def stat(self, path):
        return self.modes.get(path)

    def listdir(self, path):
        pre = path.rstrip("/") + "/"
        names = sorted({p[len(pre):].split("/")[0] for p in self.files if p.startswith(pre)})
        return names or None

    def systemctl(self, *argv):
        if argv[0] == "list-unit-files":
            return 0, self.unit_files
        if argv[0] == "list-units":
            return 0, self.units
        if "--value" in argv:
            return 0, self.execstart
        return 0, "".join(f"{k}={v}\n" for k, v in self.h5_props.items())


def go(host, *extra, tmp, baseline=True, balance=None):
    tmp.mkdir(parents=True, exist_ok=True)
    base = tmp / "baseline.json"
    if baseline and not base.exists():
        base.write_text(json.dumps({"sha256": hashlib.sha256(PROBE_STATE).hexdigest()}))
    lines: list[str] = []
    rc = dc.main(["--funded-sol", "0.25", "--baseline", str(base), *extra], host=host, out=lines.append,
                 balance_fn=balance or (lambda w, e: host.balance_lamports))
    return rc, "\n".join(lines)


def test_clean_world_has_no_alerts_and_h5_is_allowed(tmp_path):
    h = FakeHost()
    rc, out = go(h, tmp=tmp_path)
    assert rc == 0 and "ALERTS=0" in out, out
    assert "mal-h5-executor active=active/running" in out and "files: STOP=no HALT=no LIVE_OK=yes" in out
    assert "no mal-probe-executor unit is active or enabled" in out and "probe state unchanged" in out
    assert "gap +0.000000" in out


def test_probe_unit_enabled_or_active_alerts_masked_does_not(tmp_path):
    h = FakeHost()
    for files, units in (("mal-probe-executor.service enabled enabled\n", ""), ("mal-probe-executor.timer enabled-runtime -\n", ""),
                         ("", "mal-probe-executor.service loaded active running desc\n"),
                         ("", "mal-probe-executor.service loaded activating start desc\n")):
        h.unit_files, h.units = files, units
        rc, out = go(h, tmp=tmp_path)
        assert rc == 1 and "ALERT probe_unit" in out, (files, units)
    h.unit_files, h.units = "mal-probe-executor.service masked -\n", "mal-probe-executor.service loaded inactive dead desc\n"
    rc, out = go(h, tmp=tmp_path)
    assert rc == 0 and "ALERT" not in out.replace("ALERTS=0", "")


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


def test_baseline_missing_alerts_then_write_baseline_fixes_it(tmp_path):
    h = FakeHost()
    rc, out = go(h, tmp=tmp_path, baseline=False)
    assert rc == 1 and "no baseline" in out
    rc, out = go(h, "--write-baseline", tmp=tmp_path, baseline=False)
    assert rc == 0 and "baseline written" in out
    rc, out = go(h, tmp=tmp_path, baseline=False)
    assert rc == 0


def test_h5_unit_files_must_equal_the_pinned_copies(tmp_path):
    muts = {
        "base": lambda h: h.files.__setitem__(f"{ETC}/mal-h5-executor.service", BASE + b"User=root\n"),
        "live": lambda h: h.files.__setitem__(f"{ETC}/mal-h5-executor.service.d/live.conf", DROPIN + b"Environment=LD_PRELOAD=/x\n"),
        "extra dropin": lambda h: h.files.__setitem__(f"{ETC}/mal-h5-executor.service.d/zz.conf", b"[Service]\nUser=root\n"),
        "bad feed": lambda h: h.files.__setitem__(f"{ETC}/mal-h5-executor.service.d/10-shadow-feed.conf", b"[Service]\nBindReadOnlyPaths=-/etc:/srv/mal-h5-shadow\n"),
        "run dropin": lambda h: h.files.__setitem__("/run/systemd/system/mal-h5-executor.service.d/x.conf", b"[Service]\n"),
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
    rc, out = with_(lambda h: h.files.__setitem__(f"{H5}/live/state-live.json", PermissionError("x")))
    assert rc == 1 and "ALERT check_failed" in out  # a check that cannot run is an alert, never a silent pass


def test_stop_file_is_info_not_alert(tmp_path):
    h = FakeHost()
    h.files[f"{H5}/STOP"] = b""
    rc, out = go(h, tmp=tmp_path)
    assert rc == 0 and "STOP=yes" in out


def test_wallet_balance_against_funded_plus_realized(tmp_path):
    h = FakeHost()
    for delta, alert in ((0, False), (3_000_000, False), (-3_000_000, False), (-30_000_000, True), (+30_000_000, True)):
        h.balance_lamports = FUNDED - 1_000_000 + delta
        rc, out = go(h, tmp=tmp_path / str(delta))
        assert (rc == 1 and "ALERT wallet_gap" in out) == alert, (delta, out)
    # an open position's cost has left the wallet: it is subtracted, and the tolerance grows by its rent
    st = {"attempts": 4, "realized_lamports": -1_000_000, "open": {"M": {"buy_cost_lamports": 22_000_000}}, "pending": {}}
    h.files[f"{H5}/live/state-live.json"] = json.dumps(st).encode()
    h.balance_lamports = FUNDED - 1_000_000 - 22_000_000
    rc, out = go(h, tmp=tmp_path / "open")
    assert rc == 0 and "open cost 0.022000" in out
    # wrong --funded-sol (a deposit nobody told the check about)
    h.balance_lamports = FUNDED - 1_000_000 - 22_000_000 + 1_000_000_000
    rc, out = go(h, tmp=tmp_path / "dep")
    assert rc == 1 and "ALERT wallet_gap" in out and "stale --funded-sol" in out


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
    rc = dc.main(["--baseline", str(base)], host=h, out=lines.append)
    assert rc == 0 and any("pass --funded-sol" in l for l in lines)


def test_script_is_read_only_and_keyless():
    src = (FAST / "h5-daily-check.py").read_text()
    assert "/etc/mal-probe/" not in src and "probe-wallet" not in src and "load_keypair" not in src and "solders" not in src
    assert "sendTransaction" not in src
    verbs = {l.split("systemctl(")[1].split(",")[0].strip('")') .strip('"') for l in src.splitlines() if "host.systemctl(" in l}
    assert verbs == {'"list-unit-files"', '"list-units"', '"show"'} or verbs == {"list-unit-files", "list-units", "show"}, verbs  # read verbs only
    sudo_calls = [l for l in src.splitlines() if "_sudo(" in l and "def " not in l]
    assert sudo_calls and all('"/usr/bin/' in l for l in sudo_calls)  # sudo only runs fixed absolute binaries
