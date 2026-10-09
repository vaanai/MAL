"""Tests for scripts/mal-fast/c1nf-daily-check.py and c1nf-watch.py (DEC-026 items 10 and 11), in the style of test_h5_daily_check.py: a
fake host (files, systemctl, getBalance). No sudo, no network, no key. The executor's unit files are not on main yet (claude/c1nf-executor-v2),
so the base unit and the live drop-in here are stand-ins and the unit checker is a fake."""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FAST = ROOT / "scripts/mal-fast"


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, FAST / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dc = _load("c1nf_daily_check", "c1nf-daily-check.py")
watch = _load("c1nf_watch", "c1nf-watch.py")

WALLET = "C1nfWa11etPub1icAddressXXXXXXXXXXXXXXXXXXXX"[:44]
FUNDED = 500_000_000
NOW = 1_792_200_000.0  # 2026-10-17T01:20Z: inside the CAP-PICK seal, before end_ms
UPTIME = 100_000.0
BASE = b"[Unit]\nDescription=stand-in for the executor PR's keyless base unit\n"
DROPIN = (f"[Service]\nLoadCredential={dc.CREDENTIAL_LINE}\nExecStart=\nExecStart={dc.PINNED_ROOT}/venv/bin/python -I -B -u "
          f"{dc.PINNED}/launcher.py --config {dc.LIVE_CONFIG} --live\n").encode()
FEED = b"[Service]\nBindReadOnlyPaths=-/home/claude/data/c1nf-shadow:/srv/mal-c1nf-shadow\n"
LIVE_EXEC = f"{{ path={dc.PINNED_ROOT}/venv/bin/python ; argv[]=... {dc.PINNED}/launcher.py --config {dc.LIVE_CONFIG} --live }}"
WSVC = (FAST / "mal-c1nf-watch.service").read_bytes()
WTMR = (FAST / "mal-c1nf-watch.timer").read_bytes()
WATCH_SVC, WATCH_TMR = dc.WATCH_FILES[0][0], dc.WATCH_FILES[1][0]
LIVE_CFG = {"mode": "live", "state_dir": dc.C1NF_DIR, "stake_lamports": 50_000_000, "buy_priority_lamports": 505_000, "end_ms": 1_792_801_800_000,
            "max_pick_age_s": 3.0, "wallet_floor_lamports": 50_000_000,
            # claude/c1nf-executor-v2 @ 156a941, scripts/mal-fast/c1nf-executor-live.json
            "jito_enabled": False, "jito_tip_lamports": 0, "entry_tolerance_bps": 1500, "feed_heartbeat_max_age_ms": 150_000}


def ledger(*rows) -> bytes:
    return "".join(json.dumps(r) + "\n" for r in rows).encode()


def ms(t: float) -> int:
    return int(t * 1000)


class FakeChecker:
    def __init__(self):
        self.bad: dict[str, list[str]] = {}

    def problems(self, data, kind):
        return self.bad.get(kind, [])


class FakeHost(dc.Host):
    def __init__(self):
        self.files: dict[str, bytes] = {
            f"{dc.PINNED}/{dc.C1NF_UNIT}.service": BASE, f"{dc.PINNED}/{dc.C1NF_UNIT}-live-pinned.conf": DROPIN,
            dc.UNIT_FILE: BASE, dc.DROPIN_LIVE: DROPIN, dc.DROPIN_FEED: FEED, dc.LIVE_OK: b"", dc.TIER_FILE: b"T1\n",
            dc.LIVE_CONFIG: json.dumps(LIVE_CFG).encode(),
            dc.STATE_FILE: json.dumps({"attempts": 3, "realized_lamports": -1_000_000, "open": {}, "pending": {}}).encode(),
            dc.COUNTERS_FILE: json.dumps({"halts": {}, "sells_landed": 3, "sells_late": 0}).encode(),
            dc.LEDGER_FILE: ledger({"kind": "start", "user": WALLET, "ts_ms": ms(NOW - 7200)}, {"kind": "decision", "ts_ms": ms(NOW - 600)}),
            WATCH_SVC: WSVC, WATCH_TMR: WTMR, f"{dc.PINNED}/{dc.WATCH_SERVICE}": WSVC, f"{dc.PINNED}/{dc.WATCH_TIMER}": WTMR,
            dc.WATCH_STATE: json.dumps({"ts": NOW - 120}).encode(),
        }
        self.modes = {dc.C1NF_DIR: "mal-live:mal-live:700", dc.C1NF_ETC: "root:root:755", dc.LIVE_OK: "root:root:644", dc.TIER_FILE: "root:root:644"}
        self.mtimes = {dc.LIVE_OK: NOW - 7200, dc.TIER_FILE: NOW - 7200}
        self.links: set[str] = set()
        self.read_log: list[str] = []
        self.argv_log: list[tuple[str, ...]] = []
        self.props = {"LoadState": "loaded", "ActiveState": "active", "SubState": "running", "UnitFileState": "enabled", "NRestarts": "0",
                      "Result": "success", "FragmentPath": dc.UNIT_FILE, "ActiveEnterTimestampMonotonic": str(int((UPTIME - 3600) * 1e6))}
        self.watch_tmr = {"LoadState": "loaded", "ActiveState": "active", "UnitFileState": "enabled", "FragmentPath": WATCH_TMR, "DropInPaths": ""}
        self.watch_svc = {"LoadState": "loaded", "ActiveState": "inactive", "Result": "success", "ExecMainStatus": "0", "FragmentPath": WATCH_SVC, "DropInPaths": ""}
        self.dropins = [dc.DROPIN_LIVE, dc.DROPIN_FEED]
        self.execstart = LIVE_EXEC
        self.feed = ("c1nf-events-2026-10-17T01.jsonl", NOW - 60)
        self.birth_t: float | None = NOW - 86400
        self.sudo = True

    def birth(self, path):
        return self.birth_t

    def uptime(self):
        return UPTIME

    def sudo_ok(self):
        return self.sudo

    def read(self, path, tail=None):
        self.read_log.append(path)
        return self.files.get(path)

    def exists(self, path):
        self.read_log.append(path)
        return path in self.files or path in self.modes

    def islink(self, path):
        return path in self.links

    def is_regular(self, path):
        return path in self.files and path not in self.links

    def stat(self, path):
        return self.modes.get(path)

    def mtime(self, path):
        return self.mtimes.get(path)

    def newest_hourly(self, directory):
        return self.feed

    def systemctl(self, *argv):
        self.argv_log.append(argv)
        if argv[0] == "cat":
            return 0, f"# {dc.UNIT_FILE}\n{self.files[dc.UNIT_FILE].decode()}" + "".join(f"\n# {p}\n{self.files.get(p, b'').decode()}" for p in self.dropins)
        unit = argv[1]

        def props(d):
            return "".join(f"{k}={v}\n" for k, v in d.items())

        if unit == dc.WATCH_TIMER:
            return 0, props(self.watch_tmr)
        if unit == dc.WATCH_SERVICE:
            return 0, props(self.watch_svc)
        if "DropInPaths" in argv and "--value" in argv:
            return 0, " ".join(self.dropins) + "\n"
        if "NRestarts" in argv and "--value" in argv:
            return 0, self.props["NRestarts"] + "\n"
        if "--value" in argv:
            return 0, self.execstart
        return 0, props(self.props)


def go(host, *extra, now=NOW, wallet=WALLET, checker=None, balance=None):
    lines: list[str] = []
    rc = dc.main(["--funded-sol", "0.5", "--wallet", wallet, "--shadow-dir", "/nowhere", "--window-hours", "24", *extra], host=host,
                 out=lines.append, balance_fn=balance or (lambda w, e: FUNDED - 1_000_000), now=now, checker=checker or FakeChecker())
    return rc, "\n".join(lines)


def alerts(out: str) -> list[str]:
    return re.findall(r"^ALERT (\w+):", out, re.M)


def test_healthy_canary_has_no_alert():
    rc, out = go(FakeHost())
    assert rc == 0, out
    assert alerts(out) == []
    assert "total stop: min(0.30, 0.35 x funded) = 0.175 SOL" in out  # DEC-026 section 6 at a 0.5 SOL wallet
    assert "c1nf_daily_check ALERTS=0" in out


def test_constants_follow_dec026():
    assert dc.END_MS == 1_792_801_800_000  # 2026-10-24T00:30Z
    assert dc.SEAL_START_S == 1_792_112_400  # 2026-10-16T01:00Z
    assert (dc.STAKE_LAMPORTS, dc.STAKE_CEILING_LAMPORTS, dc.PRIORITY_LAMPORTS) == (50_000_000, 100_000_000, 505_000)
    assert (dc.MAX_OPEN, dc.MAX_ATTEMPTS_DAY, dc.DAILY_STOP_LAMPORTS, dc.TOTAL_STOP_CEILING_LAMPORTS) == (2, 30, 200_000_000, 300_000_000)
    assert dc.effective_total_stop(500_000_000) == 175_000_000 and dc.effective_total_stop(1_000_000_000) == 300_000_000
    assert (dc.C1NF_DIR, dc.LIVE_OK, dc.TIER_FILE, dc.KEY_PATH) == ("/var/lib/mal-live/c1nf", "/etc/mal-c1nf/LIVE_OK", "/etc/mal-c1nf/TIER",
                                                                    "/etc/mal-c1nf-key/c1nf-wallet.json")
    assert dc.PINNED == "/usr/local/lib/mal-c1nf-exec/current" and dc.WALLET != dc.H5_WALLET


def test_sudoers_fixed_paths_only_and_nothing_of_h5_or_the_key():
    text = dc.sudoers_text("claude")
    assert "*" not in text and "MAL_C1NF_CHECK" in text
    assert "mal-h5" not in text and "/var/lib/mal-live/h5" not in text and "mal-probe" not in text and "c1nf-key" not in text
    for p in dc.PRIV_READ:
        assert p in dc.PRIV_STAT


def test_never_touches_h5_or_the_key():
    host = FakeHost()
    go(host)
    touched = " ".join(host.read_log) + " " + " ".join(" ".join(a) for a in host.argv_log)
    assert "mal-h5" not in touched and "/var/lib/mal-live/h5" not in touched and "/etc/mal-probe" not in touched
    assert dc.KEY_PATH not in host.read_log


def test_heartbeat_pattern_never_matches_picks_or_outcomes():
    assert re.match(dc.HOURLY_RE, "c1nf-events-2026-10-17T01.jsonl")
    for name in ("c1nf-picks-2026-10-17T01.jsonl", "c1nf-outcomes-2026-10-17T01.jsonl", "status.json"):
        assert not re.match(dc.HOURLY_RE, name)


# ---- class-blind (EXP-025 Amendment 2 item 5) ----

def _busy_ledger(with_class: bool) -> bytes:
    rows = [{"kind": "start", "user": WALLET, "ts_ms": ms(NOW - 7200)}]
    for i in range(30):
        r = {"kind": "pick_status", "ts_ms": ms(NOW - 3000 + i), "status": "unfilled" if i % 3 == 0 else "filled", "monitored": True,
             "reason": "filled" if i % 3 else "guard_revert", "mint": f"M{i}"}
        if with_class:
            r.update(synthetic=bool(i % 2), migration_class="synthetic" if i % 2 else "canonical", c1nf_outcome=0.1 * i)
        rows.append(r)
    rows += [{"kind": "skip", "ts_ms": ms(NOW - 100), "reason": "open_cap", **({"synthetic": True} if with_class else {})},
             {"kind": "alert", "ts_ms": ms(NOW - 90), "alert": "synthetic_share_high", **({"is_synthetic": 1} if with_class else {})},
             {"kind": "decision", "ts_ms": ms(NOW - 80), **({"class": "synthetic"} if with_class else {})}]
    return ledger(*rows)


def test_output_is_identical_with_and_without_class_fields():
    a, b = FakeHost(), FakeHost()
    a.files[dc.LEDGER_FILE] = _busy_ledger(False)
    b.files[dc.LEDGER_FILE] = _busy_ledger(True)
    out_a, out_b = go(a)[1], go(b)[1]
    assert out_a == out_b
    stripped = out_b.replace("synthetic_share_high", "")
    assert not re.search(r"synth|migration_class", stripped, re.I), out_b
    assert "c1nf_executor_alert_synthetic_share_high" in alerts(out_b)  # the A3 structure alert is alert-only, by name
    assert "c1nf_fill_rate" in alerts(out_b)  # 10 of 30 unfilled > 28.9%


def test_a_reason_naming_the_class_is_never_printed():
    host = FakeHost()
    host.files[dc.LEDGER_FILE] = ledger({"kind": "skip", "ts_ms": ms(NOW - 60), "reason": "synthetic_pool_refused"},
                                        {"kind": "alert", "ts_ms": ms(NOW - 50), "alert": "Synthetic_split_mean"},
                                        {"kind": "decision", "ts_ms": ms(NOW - 40)})
    rc, out = go(host)
    assert not re.search(r"synth", out, re.I), out
    assert "2 refusal or alert row(s) whose name is not printable" in out


def test_report_withholds_any_line_that_names_the_class():
    lines: list[str] = []
    rep = dc.Report(lines.append)
    rep.info("mean outcome on synthetic pools +3%")
    rep.ok("fine")
    rep.alert("x", "a split by Synthetic class")
    rep.alert("synthetic_mean", "y")
    assert rep.withheld == 3 and rep.alerts == 2
    assert not any(re.search("synth", ln, re.I) for ln in lines), lines
    assert dc.Report.names_class("synthetic_share_high") is False


def test_watch_refuses_to_post_a_line_naming_the_class(tmp_path, monkeypatch):
    posted: list[str] = []
    host = FakeHost()
    monkeypatch.setattr(watch, "collect", lambda daily, h, env, now, bf=None: ({"x": "per class: synthetic +5%"}, {"facts": {}}))
    env = {"C1NF_WATCH_DISCORD_WEBHOOK": "https://discord.com/api/webhooks/1/abc"}
    rc = watch.run(host, env, lambda w, c: posted.append(c), tmp_path / "s.json", now=NOW)
    assert rc == 1 and posted == []


# ---- limits, config, gate, wallet ----

@pytest.mark.parametrize("key,val", [("stake_lamports", 100_000_000), ("buy_priority_lamports", 55_000), ("end_ms", None), ("max_open", 3),
                                     ("total_loss_lamports", 400_000_000), ("max_pick_age_s", 4.0), ("wallet_floor_lamports", 10_000_000),
                                     ("state_dir", "/var/lib/mal-live/h5"),
                                     ("jito_enabled", True), ("jito_enabled", None), ("jito_enabled", 0), ("jito_tip_lamports", 1_000),
                                     ("jito_tip_lamports", None), ("jito_tip_lamports", False), ("entry_tolerance_bps", 2_000),
                                     ("feed_heartbeat_max_age_ms", 300_000), ("feed_heartbeat_max_age_ms", "150000")])
def test_live_config_outside_dec026_alerts(key, val):
    host = FakeHost()
    cfg = dict(LIVE_CFG)
    if val is None:
        cfg.pop(key)
    else:
        cfg[key] = val
    host.files[dc.LIVE_CONFIG] = json.dumps(cfg).encode()
    assert "c1nf_live_config" in alerts(go(host)[1])


def test_live_config_may_lower_a_limit():
    host = FakeHost()
    host.files[dc.LIVE_CONFIG] = json.dumps({**LIVE_CFG, "max_open": 1, "daily_loss_lamports": 100_000_000}).encode()
    rc, out = go(host)
    assert rc == 0, out


def test_wrong_credential_alerts():
    host = FakeHost()
    host.files[dc.DROPIN_LIVE] = host.files[f"{dc.PINNED}/{dc.C1NF_UNIT}-live-pinned.conf"] = DROPIN.replace(
        dc.CREDENTIAL_LINE.encode(), b"probe-wallet:/etc/mal-probe/probe-wallet.json")
    assert "c1nf_wrong_credential" in alerts(go(host)[1])


def test_live_without_credential_alerts():
    host = FakeHost()
    host.dropins = [dc.DROPIN_FEED]
    assert "c1nf_unit_files" in alerts(go(host)[1])


def test_missing_unit_checker_fails_closed(monkeypatch):
    host = FakeHost()
    lines: list[str] = []
    monkeypatch.setattr(dc, "load_unit_checker", lambda d: None)
    dc.main(["--funded-sol", "0.5", "--wallet", WALLET, "--shadow-dir", "/x"], host=host, out=lines.append, balance_fn=lambda w, e: FUNDED - 1_000_000, now=NOW)
    assert "check-c1nf-unit.py is not beside this script" in "\n".join(lines)


def test_h5_wallet_is_refused():
    assert "c1nf_wallet_is_h5" in alerts(go(FakeHost(), wallet=dc.H5_WALLET)[1])


def test_wallet_gap_and_daily_wallet_fact():
    host = FakeHost()
    assert "wallet_gap" in alerts(go(host, balance=lambda w, e: FUNDED - 50_000_000)[1])
    rep = dc.run_checks(dc.build_parser().parse_args(["--funded-sol", "0.5", "--wallet", WALLET, "--shadow-dir", "/x"]), FakeHost(),
                        lambda s: None, lambda w, e: FUNDED - 1_000_000, NOW, FakeChecker())
    assert rep.facts["wallet_line"].startswith("wallet 0.499000 SOL")


@pytest.mark.parametrize("content,mode,expect", [(b"T2\n", "root:root:644", "c1nf_tier_t2"), (b"T0\n", "root:root:644", "c1nf_tier_file"),
                                                 (b"T1\n", "claude:claude:644", "c1nf_tier_file")])
def test_tier_file(content, mode, expect):
    host = FakeHost()
    host.files[dc.TIER_FILE], host.modes[dc.TIER_FILE] = content, mode
    assert expect in alerts(go(host)[1])


def test_tier_absent_means_t1():
    host = FakeHost()
    del host.files[dc.TIER_FILE], host.modes[dc.TIER_FILE]
    rc, out = go(host)
    assert rc == 0 and "executor runs T1" in out


def test_live_ok_with_wallet_wide_stop_is_idle():
    host = FakeHost()
    host.modes[dc.WALLET_STOP] = "root:root:644"
    assert "c1nf_idle" in alerts(go(host)[1])


def test_halt_and_stuck_and_open_cap():
    host = FakeHost()
    host.files[dc.COUNTERS_FILE] = json.dumps({"halts": {"fill_selection_adverse": {}}, "sells_landed": 20, "sells_late": 3}).encode()
    host.files[dc.STATE_FILE] = json.dumps({"attempts": 9, "realized_lamports": -2_000_000, "pending": {},
                                            "open": {"A": {"stuck": True, "spend": 1}, "B": {"spend": 1}, "C": {"spend": 1}}}).encode()
    a = alerts(go(host, balance=lambda w, e: FUNDED - 2_000_003)[1])
    assert {"c1nf_live_halt", "c1nf_stuck_position", "c1nf_open_cap", "c1nf_late_sells"} <= set(a)


def test_budget_stop_and_feed_refusals():
    host = FakeHost()
    host.files[dc.LEDGER_FILE] = ledger(*[{"kind": "skip", "ts_ms": ms(NOW - 60 - i), "reason": r} for i, r in
                                          enumerate(["total_loss_stop", "feed_stale", "feed_stale", "feed_stale", "pick_stale", "pick_stale", "pick_stale"])])
    a = alerts(go(host)[1])
    assert {"c1nf_budget_stop_total_loss_stop", "c1nf_feed_refusals", "c1nf_stale_picks"} <= set(a)


def v2_refusal(t_ms: int, reason: str, i: int = 0) -> list[dict]:
    """What v2 (156a941) writes for one refusal: C1NFExecutor._refuse -> H5's skip row, then _set_status's pick_status row, same reason."""
    return [{"kind": "skip", "ts_ms": t_ms, "mint": f"M{i}", "reason": reason},
            {"kind": "pick_status", "ts_ms": t_ms, "mint": f"M{i}", "pick_id": f"p{i}", "status": "unfilled", "reason": reason, "monitored": True}]


@pytest.mark.parametrize("reason", ["bad_pick:ref_state_missing", "bad_pick:ref_state", "bad_pick:missing_q_lamports",
                                    "bad_pick:missing_base_reserve", "bad_intent:missing_q_lamports"])
def test_guard_inputs_missing_alerts(reason):
    # v2's parse_pick reasons go to the ledger through H5's _bad_intent as a bare skip row (no pick_status: the pick never parsed)
    host = FakeHost()
    host.files[dc.LEDGER_FILE] = ledger(*[{"kind": "skip", "ts_ms": ms(NOW - 60 - i), "reason": reason} for i in range(5)])
    assert "c1nf_feed_schema" in alerts(go(host)[1])


def test_guard_inputs_below_the_line_do_not_alert():
    host = FakeHost()
    host.files[dc.LEDGER_FILE] = ledger(*[{"kind": "skip", "ts_ms": ms(NOW - 60 - i), "reason": "bad_pick:ref_state_missing"} for i in range(4)])
    assert "c1nf_feed_schema" not in alerts(go(host)[1])


def test_refusals_are_counted_once_with_v2s_paired_rows():
    host = FakeHost()
    host.files[dc.LEDGER_FILE] = ledger(*[r for i in range(2) for r in v2_refusal(ms(NOW - 60 - i), "feed_stale", i)])
    rc, out = go(host)
    assert "c1nf_feed_refusals" not in alerts(out)  # two real refusals, under REFUSAL_ALERT_N = 3
    assert "feed_stale x2" in out and "feed_stale x4" not in out
    host.files[dc.LEDGER_FILE] = ledger(*[r for i in range(3) for r in v2_refusal(ms(NOW - 60 - i), "feed_stale", i)])
    assert "c1nf_feed_refusals" in alerts(go(host)[1])


def seal_rows(now: float, before: int, after: int) -> list[dict]:
    """H5's tick writes a count-only seal_count row (seal_skips=<total>) at most once a minute; a seal refusal has no per-mint row."""
    return [{"kind": "seal_count", "ts_ms": ms(now - 30 * 3600), "mint": "", "seal_skips": before},
            {"kind": "seal_count", "ts_ms": ms(now - 120), "mint": "", "seal_skips": after}]


@pytest.mark.parametrize("now,expect", [(NOW, True), (dc.SEAL_START_S - 3600, False)])
def test_seal_pause_alerts_from_the_seal_start(now, expect):
    host = FakeHost()
    host.files[dc.LEDGER_FILE] = ledger(*seal_rows(now, 7, 11))
    host.files[dc.COUNTERS_FILE] = json.dumps({"seal_skips": 12}).encode()  # the counters file is fresher than the last ledger row
    rc, out = go(host, now=now)
    assert ("c1nf_oracle_unavailable" in alerts(out)) is expect
    assert "CAP-PICK seal x5 (count only)" in out


def test_seal_pause_from_the_ledger_alone_and_not_when_a_pick_was_acted_on():
    host = FakeHost()
    host.files[dc.LEDGER_FILE] = ledger(*seal_rows(NOW, 0, 4))
    assert "c1nf_oracle_unavailable" in alerts(go(host)[1])
    host.files[dc.LEDGER_FILE] = ledger(*seal_rows(NOW, 0, 4), {"kind": "buy", "ts_ms": ms(NOW - 60), "mint": "M"})
    assert "c1nf_oracle_unavailable" not in alerts(go(host)[1])
    host.files[dc.LEDGER_FILE] = ledger(*seal_rows(NOW, 4, 4))  # no rise in the window
    rc, out = go(host)
    assert "c1nf_oracle_unavailable" not in alerts(out) and "CAP-PICK seal" not in out


def test_seal_pause_needs_live_ok():
    host = FakeHost()
    host.files[dc.LEDGER_FILE] = ledger(*seal_rows(NOW, 0, 4))
    for d in (host.files, host.modes, host.mtimes):
        d.pop(dc.LIVE_OK, None)
    assert "c1nf_oracle_unavailable" not in alerts(go(host)[1])


def test_fill_rate_under_line_is_info():
    host = FakeHost()
    host.files[dc.LEDGER_FILE] = ledger(*[{"kind": "pick_status", "ts_ms": ms(NOW - 600 + i), "monitored": True,
                                           "status": "unfilled" if i < 8 else "filled"} for i in range(30)])
    rc, out = go(host)
    assert "c1nf_fill_rate" not in alerts(out) and "22 of the last 30 monitored picks filled" in out


def test_stale_feed_and_late_bind():
    host = FakeHost()
    host.feed = ("c1nf-events-2026-10-16T22.jsonl", NOW - 3600)
    host.birth_t = NOW - 60
    a = alerts(go(host)[1])
    assert {"c1nf_feed_stale", "c1nf_feed_bind_stale"} <= set(a)


def test_watchdog_files_and_staleness():
    host = FakeHost()
    host.files[WATCH_SVC] = WSVC + b"# edited\n"
    host.files[dc.WATCH_STATE] = json.dumps({"ts": NOW - 3600}).encode()
    a = alerts(go(host)[1])
    assert {"c1nf_watch_files", "c1nf_watch_stale"} <= set(a)


def test_sudo_failure_is_an_alert():
    host = FakeHost()
    host.sudo = False
    assert "sudo_unavailable" in alerts(go(host)[1])


# ---- the watchdog ----

def test_watch_posts_new_alerts_wallet_line_once_a_day_and_resolved():
    obs = {"facts": {"wallet_line": "wallet 0.499000 SOL, realized -0.001000, open 0"}, "restarts": 0, "stop": False}
    lines, st = watch.decide({"c1nf_idle": "x"}, obs, {}, NOW)
    assert any(x.startswith("ALERT c1nf_idle") for x in lines) and any(x.startswith("DAILY 2026-10-17 wallet") for x in lines)
    lines2, st2 = watch.decide({}, {**obs, "restarts": 2, "stop": True}, st, NOW + 300)
    assert "RESOLVED c1nf_idle" in lines2 and not any(x.startswith("DAILY") for x in lines2)
    assert any("c1nf_restarts" in x for x in lines2) and any("STOP placed" in x for x in lines2)


def test_watch_config_requires_a_wallet_that_is_not_h5(tmp_path):
    daily = watch.load_daily()
    env = {"C1NF_WATCH_FUNDED_SOL": "0.5", "C1NF_WATCH_SHADOW_DIR": "/x", "C1NF_WATCH_WALLET": daily.H5_WALLET}
    alerts_, _ = watch.collect(daily, FakeHost(), env, NOW)
    assert "watch_config" in alerts_
    alerts_, _ = watch.collect(daily, FakeHost(), {k: v for k, v in env.items() if k != "C1NF_WATCH_WALLET"}, NOW)
    assert "watch_config" in alerts_


def test_watch_units_pass_their_allowlist_and_h5_units_do_not():
    chk = FAST / "check-c1nf-watch-unit.py"
    ok = [subprocess.run([sys.executable, "-I", str(chk), flag, str(FAST / f)], capture_output=True).returncode
          for flag, f in (("--watch-service", "mal-c1nf-watch.service"), ("--watch-timer", "mal-c1nf-watch.timer"))]
    assert ok == [0, 0]
    assert subprocess.run([sys.executable, "-I", str(chk), "--watch-service", str(FAST / "mal-h5-watch.service")], capture_output=True).returncode == 1
    assert b"/etc/mal-c1nf-key" in WSVC  # the second wallet's key directory is fenced off from the root watchdog
