"""Tests for the H5 watchdog (scripts/mal-fast/h5-watch.py), its units, and the hardening added to the base unit. A fake host and a fake
Discord post: no network, no sudo, no key. Nothing here ran on the host."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from tools.test_h5_daily_check import DROPIN, FakeHost, H5, NOW, dc

ROOT = Path(__file__).resolve().parent.parent
FAST = ROOT / "scripts/mal-fast"
spec = importlib.util.spec_from_file_location("h5_watch", FAST / "h5-watch.py")
hw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hw)

WEBHOOK = "https://discord.com/api/webhooks/123456789/SECRET-TOKEN_abc"
ENV = {"H5_WATCH_DISCORD_WEBHOOK": WEBHOOK, "H5_WATCH_FUNDED_SOL": "0.25", "H5_WATCH_SHADOW_DIR": "/home/claude/data/h5-shadow"}
SERVICE = FAST / "mal-h5-watch.service"
TIMER = FAST / "mal-h5-watch.timer"
UNIT = FAST / "mal-h5-executor.service"


def _checker():
    spec = importlib.util.spec_from_file_location("check_h5_unit", FAST / "check-h5-unit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Poster:
    def __init__(self, fail=False):
        self.posts: list[tuple[str, str]] = []
        self.fail = fail

    def __call__(self, webhook, content):
        if self.fail:
            raise RuntimeError("post failed (URLError)")
        self.posts.append((webhook, content))


def world(**kw) -> FakeHost:
    h = FakeHost()
    for k, v in kw.items():
        setattr(h, k, v)
    return h


def go(h, state, poster, now=NOW, env=ENV):
    if now > NOW:  # the fake clock advanced: the detector keeps writing, and the gate was opened an hour ago (the 6 h idle-ledger rule is quiet)
        h.feed = (h.feed[0], max(h.feed[1], now - 60)) if h.feed else h.feed
        h.mtimes[dc.LIVE_OK] = max(h.mtimes[dc.LIVE_OK], now - 3600)
        h.files[dc.WATCH_STATE] = json.dumps({"ts": now - 120}).encode()  # the watchdog's own previous run, two minutes ago
    return hw.run(h, env, poster, state, now, balance_fn=lambda w, e: h.balance_lamports)


# --- the posting rules ------------------------------------------------------------------------------------------------------

def test_a_healthy_canary_posts_nothing_and_writes_a_private_state(tmp_path):
    p = Poster()
    st = tmp_path / "state.json"
    assert go(world(), st, p) == 0 and p.posts == []
    assert json.loads(st.read_text())["active"] == {} and oct(st.stat().st_mode & 0o777) == "0o600"


def test_an_alert_is_posted_once_reposted_after_six_hours_and_resolved_when_it_clears(tmp_path):
    p, st = Poster(), tmp_path / "state.json"
    stuck = {"attempts": 4, "realized_lamports": 0, "open": {"M": {"abandoned": True, "buy_cost_lamports": 20_000_000}}, "pending": {}}
    h = world()
    h.files[f"{H5}/live/state-live.json"] = json.dumps(stuck).encode()
    h.balance_lamports = 250_000_000 - 20_000_000
    assert go(h, st, p) == 0
    assert len(p.posts) == 1 and "ALERT h5_stuck_position" in p.posts[0][1] and p.posts[0][1].startswith("[H5 watch] ")
    go(h, st, p, NOW + 300)
    go(h, st, p, NOW + 3 * 3600)
    assert len(p.posts) == 1  # the same alert is not repeated within 6 h
    go(h, st, p, NOW + 6 * 3600 + 1)
    assert len(p.posts) == 2 and "ALERT h5_stuck_position" in p.posts[1][1]
    h.files[f"{H5}/live/state-live.json"] = json.dumps({"attempts": 4, "realized_lamports": 0, "open": {}, "pending": {}}).encode()
    h.balance_lamports = 250_000_000
    go(h, st, p, NOW + 6 * 3600 + 400)
    assert len(p.posts) == 3 and "RESOLVED h5_stuck_position" in p.posts[2][1]
    go(h, st, p, NOW + 6 * 3600 + 800)
    assert len(p.posts) == 3


def test_every_class_of_alert_the_brief_names_reaches_discord(tmp_path):
    """stuck position, any latched halt, stop fired, unit restarts, wallet balance (DEC-024 section 8), plus the idle and unmanaged cases."""
    cases = {
        "latched halt": (lambda h: h.files.__setitem__(f"{H5}/live/h5-counters.json", json.dumps({"halts": {"boost_last_slice_lt_335": {}}}).encode()), "h5_live_halt"),
        "wallet balance": (lambda h: setattr(h, "balance_lamports", 100_000_000), "wallet_gap"),
        "HALT file": (lambda h: h.files.__setitem__(f"{H5}/HALT", b""), "h5_halt_file"),
        "unit failed": (lambda h: h.h5_props.update(ActiveState="failed"), "h5_unit_failed"),
        "unmanaged": (lambda h: (h.files.__setitem__(f"{H5}/live/state-live.json", json.dumps({"open": {"M": {}}, "pending": {}}).encode()), h.h5_props.update(ActiveState="inactive"))[0], "h5_positions_unmanaged"),
        "stale feed": (lambda h: setattr(h, "feed", ("h5-shadow-2026-10-09T14.jsonl", NOW - 3600)), "h5_feed_stale"),
        "idle (STOP)": (lambda h: h.files.__setitem__(f"{H5}/STOP", b""), "h5_idle"),
    }
    for name, (mut, alert) in cases.items():
        h, p = world(), Poster()
        mut(h)
        go(h, tmp_path / (name.replace(" ", "_") + ".json"), p)
        assert p.posts and f"ALERT {alert}" in p.posts[0][1], name


def test_restarts_and_stop_are_events_between_runs(tmp_path):
    h, p, st = world(), Poster(), tmp_path / "state.json"
    go(h, st, p)
    assert p.posts == []
    h.h5_props["NRestarts"] = "3"
    go(h, st, p, NOW + 300)
    assert len(p.posts) == 1 and "ALERT h5_restarts: the unit restarted 3 time(s)" in p.posts[0][1] and "0 -> 3" in p.posts[0][1]
    go(h, st, p, NOW + 600)  # no further restarts: the alert clears
    assert "RESOLVED h5_restarts" in p.posts[-1][1]
    n = len(p.posts)
    h.files[f"{H5}/STOP"] = b""
    go(h, st, p, NOW + 900)
    assert any("EVENT STOP placed" in c for _, c in p.posts[n:])
    del h.files[f"{H5}/STOP"]
    go(h, st, p, NOW + 1200)
    assert "EVENT STOP removed" in p.posts[-1][1]


def test_a_failed_post_keeps_the_state_so_the_alert_is_tried_again(tmp_path):
    h, st = world(files=None) if False else world(), tmp_path / "state.json"
    h.files[f"{H5}/HALT"] = b""
    bad = Poster(fail=True)
    assert go(h, st, bad) == 1 and not st.exists()
    good = Poster()
    assert go(h, st, good, NOW + 300) == 0 and len(good.posts) == 1 and "h5_halt_file" in good.posts[0][1]


def test_missing_or_bad_webhook_posts_nothing_and_never_prints_the_secret(tmp_path, capsys):
    p = Poster()
    for env in ({**ENV, "H5_WATCH_DISCORD_WEBHOOK": ""}, {k: v for k, v in ENV.items() if k != "H5_WATCH_DISCORD_WEBHOOK"},
                {**ENV, "H5_WATCH_DISCORD_WEBHOOK": "http://evil.example/api/webhooks/1/SECRET-TOKEN"}, {**ENV, "H5_WATCH_DISCORD_WEBHOOK": WEBHOOK + "/extra"}):
        assert go(world(), tmp_path / "s.json", p, env=env) == 2
    out = capsys.readouterr()
    assert p.posts == [] and "SECRET-TOKEN" not in out.out + out.err and not (tmp_path / "s.json").exists()


def test_the_webhook_is_never_in_a_post_failure_message_or_the_output(tmp_path, capsys, monkeypatch):
    def boom(req, timeout=0):
        raise OSError(f"cannot reach {req.full_url}")

    monkeypatch.setattr(hw.urllib.request, "urlopen", boom)
    h = world()
    h.files[f"{H5}/HALT"] = b""
    assert hw.run(h, ENV, hw.post_discord, tmp_path / "s.json", NOW, balance_fn=lambda w, e: h.balance_lamports) == 1
    out = capsys.readouterr()
    assert "SECRET-TOKEN" not in out.out + out.err and "post failed (OSError)" in out.err
    monkeypatch.setattr(hw.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no request may be made for a non-Discord URL")))
    for bad in ("http://discord.com/api/webhooks/1/x", "https://evil.example/api/webhooks/1/x", "https://discord.com/api/webhooks/abc/x", ""):
        try:
            hw.post_discord(bad, "x")
        except RuntimeError as e:
            assert "not a Discord webhook URL" in str(e)
        else:
            raise AssertionError(bad)


def test_config_errors_are_alerts_not_silence(tmp_path):
    p = Poster()
    assert go(world(), tmp_path / "s.json", p, env={k: v for k, v in ENV.items() if k != "H5_WATCH_FUNDED_SOL"}) == 0
    assert "ALERT watch_config" in p.posts[0][1]


def test_test_message_posts_one_line_and_exits(monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(hw, "post_discord", lambda w, c: sent.append((w, c)))
    monkeypatch.setenv("H5_WATCH_DISCORD_WEBHOOK", WEBHOOK)
    assert hw.main(["--test-message"]) == 0 and len(sent) == 1 and "test message" in sent[0][1]
    assert "SECRET-TOKEN" not in capsys.readouterr().out
    assert hw.main(["--bogus"]) == 2


def test_the_watch_script_is_stdlib_only_keyless_and_read_only():
    src = (FAST / "h5-watch.py").read_text()
    assert "solders" not in src and "probe-wallet" not in src and "load_keypair" not in src and "sendTransaction" not in src
    assert "/etc/mal-probe" not in src and "sudo" not in src
    tree = __import__("ast").parse(src)
    imported = {n.names[0].name.split(".")[0] for n in __import__("ast").walk(tree) if isinstance(n, __import__("ast").Import)}
    imported |= {n.module.split(".")[0] for n in __import__("ast").walk(tree) if isinstance(n, __import__("ast").ImportFrom) and n.module}
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported - set(sys.stdlib_module_names)
    assert '"--public-rpc"' in src and '"--skip-probe-state"' in src  # no Helius key in the watch, and the probe-state baseline stays with the daily run


# --- the watchdog's units -----------------------------------------------------------------------------------------------

def test_checker_accepts_the_shipped_watch_units_and_refuses_changes():
    c = _checker()
    svc, tmr = SERVICE.read_text(), TIMER.read_text()
    assert c.problems(svc, "watch-service") == [] and c.problems(tmr, "watch-timer") == []
    assert c.problems(svc, "watch-timer") and c.problems(tmr, "watch-service") and c.problems(svc, "base") and c.problems(UNIT.read_text(), "watch-service")
    muts = {
        "User": svc.replace("User=root", "User=mal-live"),
        "env file optional": svc.replace("EnvironmentFile=/etc", "EnvironmentFile=-/etc"),
        "second env file": svc.replace("Type=oneshot", "Type=oneshot\nEnvironmentFile=/var/lib/mal/fast-listener/helius.env"),
        "ExecStart elsewhere": svc.replace("/usr/local/lib/mal-h5-exec/current/h5-watch.py", "/var/lib/mal/fast-forward/src/scripts/mal-fast/h5-watch.py"),
        "no -I": svc.replace("python3 -I -S -B -u", "python3 -S -B -u"),
        "no -S": svc.replace("python3 -I -S -B -u", "python3 -I -B -u"),
        "python elsewhere": svc.replace("/usr/bin/python3", "/usr/local/bin/python3"),
        "key paths not fenced": svc.replace("InaccessiblePaths=-/etc/mal-probe -/etc/mal-probe-rpc -/run/credentials\n", ""),
        "credentials not fenced": svc.replace(" -/run/credentials", ""),
        "probe key dir not fenced": svc.replace("-/etc/mal-probe -/etc/mal-probe-rpc", "-/etc/mal-probe-rpc"),
        "credential": svc + "LoadCredential=probe-wallet:/etc/mal-probe/probe-wallet.json\n",
        "caps widened": svc.replace("CAP_DAC_READ_SEARCH", "CAP_SYS_ADMIN"),
        "no caps limit": svc.replace("CapabilityBoundingSet=CAP_DAC_READ_SEARCH\n", ""),
        "home visible rw": svc.replace("ProtectHome=read-only", "ProtectHome=false"),
        "system writable": svc.replace("ProtectSystem=strict", "ProtectSystem=full"),
        "rw paths": svc + "ReadWritePaths=/var/lib/mal-live\n",
        "no new privs off": svc.replace("NoNewPrivileges=true", "NoNewPrivileges=false"),
        "post start": svc + "ExecStartPost=/tmp/x\n",
        "pre extra": svc + "ExecStartPre=+/tmp/x\n",
        "state mode": svc.replace("StateDirectoryMode=0700", "StateDirectoryMode=0755"),
        "continuation": svc.replace("Type=oneshot", "Type=oneshot \\\n User=mal-live"),
    }
    for name, text in muts.items():
        assert text != svc and c.problems(text, "watch-service"), name
    tmuts = {"faster": tmr.replace("5min", "1s"), "target": tmr.replace("timers.target", "multi-user.target"), "extra": tmr.replace("[Install]", "OnCalendar=daily\n[Install]"),
             "persistent": tmr.replace("AccuracySec=30s", "AccuracySec=30s\nPersistent=true")}
    for name, text in tmuts.items():
        assert text != tmr and c.problems(text, "watch-timer"), name


def test_the_watch_service_holds_no_key_and_reads_only_what_it_must():
    svc = "\n".join(l for l in SERVICE.read_text().splitlines() if not l.lstrip().startswith("#"))
    assert "InaccessiblePaths=-/etc/mal-probe -/etc/mal-probe-rpc -/run/credentials" in svc.splitlines()  # the key paths are fenced off
    svc = svc.replace("InaccessiblePaths=-/etc/mal-probe -/etc/mal-probe-rpc -/run/credentials", "")  # the only place those paths may appear
    assert "LoadCredential" not in svc and "probe-wallet" not in svc and "/etc/mal-probe-rpc" not in svc and "/etc/mal-probe/" not in svc
    assert "ExecStart=/usr/bin/python3 -I -S -B -u /usr/local/lib/mal-h5-exec/current/h5-watch.py" in svc.splitlines()
    assert "EnvironmentFile=/etc/mal-h5-watch/watch.env" in svc and "ReadWritePaths" not in svc  # writes only its StateDirectory
    assert "StateDirectory=mal-h5-watch" in svc and "CapabilityBoundingSet=CAP_DAC_READ_SEARCH" in svc and "AF_UNIX" in svc  # AF_UNIX: systemctl show
    assert re.search(r"^ExecStartPre=\+/usr/bin/env -i /bin/sh -c 'test .*0:0:700.*0:0:600'$", svc, re.M)


def test_base_unit_orders_the_probe_stop_before_start_and_has_the_extra_hardening():
    c = _checker()
    unit = UNIT.read_text()
    assert re.search(r"^After=mal-probe-executor\.service$", unit, re.M) and re.search(r"^Conflicts=mal-probe-executor\.service$", unit, re.M)
    for d in ("SystemCallArchitectures=native", "RestrictNamespaces=true", "RestrictRealtime=true", "ProtectClock=true", "ProtectKernelLogs=true",
              "ProtectHostname=true", "UMask=0077"):
        assert re.search(rf"^{d}$", unit, re.M), d
        assert c.problems(unit.replace(d + "\n", ""), "base"), f"checker accepted a unit without {d}"
        key = d.split("=")[0]
        assert c.problems(unit.replace(d, f"{key}=false" if "=true" in d else f"{key}=0022" if key == "UMask" else f"{key}=x"), "base"), d
    assert c.problems(unit.replace("After=mal-probe-executor.service\n", ""), "base")
    # InaccessiblePaths for /etc/mal-probe-rpc would break the executor's own startup check of that directory; it must not be added
    assert "InaccessiblePaths" not in unit


def test_runbook_installs_the_watch_units_from_the_verified_copy_and_gates_going_live_on_it():
    t = (ROOT / "docs/runbooks/h5-executor.md").read_text()
    for s in ("mal-h5-watch.timer", "/etc/mal-h5-watch/watch.env", "H5_WATCH_DISCORD_WEBHOOK", "H5_WATCH_FUNDED_SOL", "H5_WATCH_SHADOW_DIR",
              "--watch-service", "--watch-timer", "h5-watch.py --test-message", "systemctl enable --now mal-h5-watch.timer"):
        assert s in t, s
    assert t.index("systemctl enable --now mal-h5-watch.timer") < t.index("## Going live (manager, then Helm)")
    going = t[t.index("## Going live (manager, then Helm)"):t.index("## Stop, halt, status")]
    assert "mal-h5-watch.timer" in going and "test message" in going.lower()
