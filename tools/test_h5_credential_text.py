"""Tests for how h5-daily-check.py (and so the h5-watch.py watchdog) decides that a unit holds a credential: from the unit TEXT
(`systemctl cat`), never from the `show -p LoadCredential` property, which systemd 255 prints as `[unprintable]` for every unit.
A fake host, no sudo, no network, no key. Nothing here ran on the host."""
from __future__ import annotations

import pytest

from tools.test_h5_daily_check import BASE, DROPIN, FAST, PROBE_BASE, PROBE_FRAGMENT, FakeHost, H5, NOW, alerts, dc, go
from tools.test_h5_watch import Poster
from tools.test_h5_watch import go as watch_go

CRED = "LoadCredential=probe-wallet:/etc/mal-probe/probe-wallet.json\n"
PROBE_DROPIN = "/etc/systemd/system/mal-probe-executor.service.d/live.conf"
DROPIN_TEXT = "[Service]\n" + CRED + "ExecStart=\nExecStart=/bin/true\n"


# --- 1. the bug: [unprintable] on a clean unit ---------------------------------------------------------------------------------

def test_the_fake_models_systemd_255_which_prints_unprintable_for_a_clean_unit():
    h = FakeHost()
    rc, out = h.systemctl("show", "mal-probe-executor.service", "-p", "LoadCredential")
    assert "LoadCredential=[unprintable]" in out
    assert "LoadCredential" not in h.cat("mal-probe-executor.service")[1].replace("LoadCredential=[unprintable]", "")  # the text itself has none


def test_show_unprintable_with_no_credential_in_the_unit_text_gives_no_alert(tmp_path):
    h = FakeHost()
    assert h.probe_props["LoadCredential"] == "[unprintable]"
    rc, out = go(h, tmp=tmp_path)
    assert rc == 0 and alerts(out) == [], out
    assert "holds no drop-in or credential" in out


def test_the_watchdog_posts_nothing_for_it_not_even_every_six_hours(tmp_path):
    h, p, st = FakeHost(), Poster(), tmp_path / "state.json"
    for k in range(4):  # the bug posted probe_has_key on every 6 h repeat
        assert watch_go(h, st, p, NOW + k * (6 * 3600 + 1)) == 0
    assert p.posts == []


def test_the_show_call_no_longer_asks_for_the_credential_property(tmp_path):
    h = FakeHost()
    go(h, tmp=tmp_path)
    shows = [a for a in h.argv_log if a[0] == "show" and a[1] == "mal-probe-executor.service"]
    assert shows and all("LoadCredential" not in " ".join(a) for a in shows)
    assert ("cat", "mal-probe-executor.service", "--no-pager") in h.argv_log


# --- 2. a real credential is still found -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("line", [
    "LoadCredential=probe-wallet:/etc/mal-probe/probe-wallet.json",
    "LoadCredentialEncrypted=probe-wallet:/etc/mal-probe/probe-wallet.cred",
    "  LoadCredential=probe-wallet:/x",
    "\tLoadCredential = probe-wallet:/x",
    "LoadCredential\t=probe-wallet:/x",
])
def test_a_live_credential_line_in_a_drop_in_alerts_and_is_never_echoed(tmp_path, line):
    h = FakeHost()
    h.probe_props["DropInPaths"] = PROBE_DROPIN
    h.probe_dropin_text[PROBE_DROPIN] = f"[Service]\n{line}\nExecStart=\nExecStart=/bin/true\n"
    rc, out = go(h, tmp=tmp_path)
    assert rc == 1 and "probe_has_key" in alerts(out) and "probe_live_dropin" in alerts(out), out
    msg = next(l for l in out.splitlines() if l.startswith("ALERT probe_has_key"))
    assert "probe-wallet" not in out and "/etc/mal-probe" not in out  # the alert says a credential is set; it does not echo the line or its path
    assert "unit text sets LoadCredential= or LoadCredentialEncrypted=" in msg


def test_a_credential_in_the_base_unit_text_alerts_too(tmp_path):
    h = FakeHost()
    h.probe_unit_text = PROBE_BASE.replace("[Service]\n", "[Service]\n" + CRED, 1)
    assert h.probe_unit_text != PROBE_BASE
    rc, out = go(h, tmp=tmp_path)
    assert rc == 1 and alerts(out) == ["probe_has_key"], out


def test_the_watchdog_posts_a_live_credential_once(tmp_path):
    h, p, st = FakeHost(), Poster(), tmp_path / "state.json"
    h.probe_props["DropInPaths"] = PROBE_DROPIN
    h.probe_dropin_text[PROBE_DROPIN] = DROPIN_TEXT
    assert watch_go(h, st, p) == 0
    # the watchdog skips the probe-STATE check but not the probe UNIT check
    assert len(p.posts) == 1 and "ALERT probe_has_key" in p.posts[0][1] and "ALERT probe_live_dropin" in p.posts[0][1]
    assert "probe-wallet" not in p.posts[0][1] and "/etc/mal-probe/" not in p.posts[0][1]


# --- 3. comments are not credentials ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "[Service]\n# LoadCredential=probe-wallet:/etc/mal-probe/probe-wallet.json\n",
    "[Service]\n; LoadCredential=probe-wallet:/x\n",
    "[Service]\n  \t# LoadCredentialEncrypted=probe-wallet:/x\n",
    "[Service]\n#LoadCredential=probe-wallet:/x\n;LoadCredentialEncrypted=y:/z\n",
    "[Service]\nExecStart=/bin/true \\\n# LoadCredential=probe-wallet:/x\n  --flag\n",  # a comment inside a continuation is skipped, as systemd does
    "[Service]\n# the live drop-in adds LoadCredential= and ExecStart= (prose)\nUser=x\n",
    "[Service]\nEnvironment=NOTE=LoadCredential=x\nExecStart=/bin/echo LoadCredential=x\n",  # the key must START the line
    "[Service]\nLoadCredentials=probe-wallet:/x\nXLoadCredential=a:b\nLoadCredentialFoo=a:b\nSetCredential=a:b\n",  # other names are other keys
    "[Service]\nloadcredential=probe-wallet:/x\n",  # keys are case-sensitive in systemd: this is an unknown key, which does nothing
    "",
])
def test_commented_or_lookalike_lines_are_not_credentials(text):
    assert dc.text_sets_credential(text) is False


def test_a_commented_credential_line_in_the_probe_unit_gives_no_alert(tmp_path):
    h = FakeHost()
    h.probe_unit_text += "# LoadCredential=probe-wallet:/etc/mal-probe/probe-wallet.json\n"
    h.probe_props["DropInPaths"] = PROBE_DROPIN
    h.probe_dropin_text[PROBE_DROPIN] = "[Service]\n; LoadCredentialEncrypted=probe-wallet:/x\n"
    rc, out = go(h, tmp=tmp_path)
    assert "probe_has_key" not in alerts(out), out
    assert alerts(out) == ["probe_live_dropin"]  # the drop-in itself is still an alert; only the credential alert is about the text


@pytest.mark.parametrize("text", [
    "[Service]\nLoadCredential=a:b",  # no trailing newline
    "[Service]\r\nLoadCredential=a:b\r\n",  # CRLF
    "[Service]\nUser=x\n\n\n   LoadCredential=a:b   \n",
    "[Service]\nLoadCredential \\\n=a:b\n",  # the key split over a continuation: systemd joins it into `LoadCredential  =a:b`
    "[Service]\n\\\nLoadCredential=a:b\n",
    "[Service]\nExecStart=/bin/true\nLoadCredentialEncrypted=a:b\n[Install]\nWantedBy=x\n",
])
def test_the_credential_is_found_whatever_the_line_ending_and_spacing(text):
    assert dc.text_sets_credential(text) is True


# --- 4. the live H5 unit is credentialed ---------------------------------------------------------------------------------------------

def test_the_h5_unit_with_its_live_drop_in_is_credentialed_and_the_base_alone_is_not():
    assert dc.text_sets_credential(DROPIN.decode()) is True  # the pinned live drop-in, its comment header included
    assert dc.text_sets_credential(BASE.decode()) is False  # the keyless dry-run base unit
    assert dc.text_sets_credential(BASE.decode() + "\n# /etc/systemd/system/mal-h5-executor.service.d/live.conf\n" + DROPIN.decode()) is True
    h = FakeHost()
    rep = dc.Report(lambda s: None)
    assert dc.unit_sets_credential(h, rep, "mal-h5-executor.service", "loaded") is True  # FakeHost: base + live.conf + the shadow-feed drop-in
    h.dropins = [dc.DROPIN_FEED]
    assert dc.unit_sets_credential(h, rep, "mal-h5-executor.service", "loaded") is False
    assert rep.alerts == 0


def test_the_repo_probe_units_the_dry_run_has_no_credential_and_the_live_drop_ins_do():
    assert dc.text_sets_credential((FAST / "mal-probe-executor.service").read_text()) is False
    for name in ("mal-probe-executor-live-pinned.conf", "mal-probe-executor-live.conf"):
        assert dc.text_sets_credential((FAST / name).read_text()) is True, name


def test_the_live_h5_unit_with_its_drop_in_has_no_alert_and_without_it_the_check_says_so(tmp_path):
    rc, out = go(FakeHost(), tmp=tmp_path / "live")
    assert rc == 0 and alerts(out) == [] and "H5 unit files equal the pinned copies" in out
    h = FakeHost()
    h.dropins = [dc.DROPIN_FEED]  # still running with --live per ExecStart, but systemd's unit text no longer hands the credential over
    rc, out = go(h, tmp=tmp_path / "nodropin")
    assert rc == 1 and alerts(out) == ["h5_unit_files"] and "sets no LoadCredential=" in out and "probe-wallet" not in out


def test_a_dry_run_h5_unit_needs_no_credential(tmp_path):
    h = FakeHost()
    h.dropins = [dc.DROPIN_FEED]
    h.execstart = h.execstart.replace(" --live", "")
    h.h5_props["ActiveState"] = "inactive"
    h.files.pop(dc.LIVE_OK)
    h.modes.pop(dc.LIVE_OK)
    rc, out = go(h, tmp=tmp_path)
    assert "h5_unit_files" not in alerts(out), out
    assert ("cat", "mal-h5-executor.service", "--no-pager") not in h.argv_log  # not asked: only a unit running live must show a credential


# --- 5. a missing unit, masked unit, failing systemctl -------------------------------------------------------------------------------

def test_a_missing_or_masked_probe_unit_counts_as_no_key(tmp_path):
    h = FakeHost()
    h.probe_props.update(LoadState="not-found", UnitFileState="", FragmentPath="", DropInPaths="")
    rc, out = go(h, tmp=tmp_path / "missing")  # systemctl cat exits 1 with nothing on stdout, as for a real missing unit
    assert rc == 0 and alerts(out) == [], out
    assert ("cat", "mal-probe-executor.service", "--no-pager") not in h.argv_log  # not even asked
    h = FakeHost()
    h.probe_props.update(LoadState="masked")
    rc, out = go(h, tmp=tmp_path / "masked")
    assert rc == 0 and alerts(out) == [], out


def test_a_failing_systemctl_cat_stays_an_alert_and_is_never_no_key(tmp_path):
    for name, answer in {"exit 1": (1, ""), "nothing on stdout": (0, ""), "whitespace only": (0, "\n  \n"), "timeout": (127, "")}.items():
        h = FakeHost()
        h.cat_fail["mal-probe-executor.service"] = answer
        rc, out = go(h, tmp=tmp_path / name.replace(" ", "_"))
        assert rc == 1 and alerts(out) == ["systemctl_failed"], (name, out)
        assert "`systemctl cat mal-probe-executor.service` failed" in out and "holds no drop-in or credential" not in out, name
    h = FakeHost()
    h.systemctl_rc = 1  # everything fails, `show` first: still systemctl_failed, no OK line for the probe, and no cat call after a failed show
    rc, out = go(h, tmp=tmp_path / "all")
    assert rc == 1 and "ALERT systemctl_failed" in out and "no mal-probe-executor unit is active" not in out
    assert not [a for a in h.argv_log if a[0] == "cat"]


def test_a_failing_systemctl_cat_for_the_live_h5_unit_is_systemctl_failed_not_a_missing_credential(tmp_path):
    h = FakeHost()
    h.cat_fail["mal-h5-executor.service"] = (1, "")
    rc, out = go(h, tmp=tmp_path)
    assert rc == 1 and alerts(out) == ["systemctl_failed"], out
    assert "sets no LoadCredential" not in out and "H5 unit files equal the pinned copies" not in out


def test_an_active_probe_holding_a_credential_reports_both_alerts(tmp_path):
    h = FakeHost()
    h.probe_props["ActiveState"] = "active"
    h.probe_unit_text += CRED
    rc, out = go(h, tmp=tmp_path)
    assert sorted(alerts(out)) == ["probe_has_key", "probe_unit"]


def test_the_unit_text_is_only_scanned_never_echoed(tmp_path):
    secret = "SECRET-MARKER-9f3a"
    h = FakeHost()
    h.probe_unit_text += f"Environment=X={secret}\nLoadCredential=probe-wallet:/etc/mal-probe/{secret}.json\n"
    rc, out = go(h, tmp=tmp_path)
    assert "probe_has_key" in alerts(out) and secret not in out
    p, st = Poster(), tmp_path / "w.json"
    watch_go(h, st, p)
    assert p.posts and secret not in p.posts[0][1]
    assert PROBE_FRAGMENT not in out  # not even the path of the file it read
