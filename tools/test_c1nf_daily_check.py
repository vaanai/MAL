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
            # claude/c1nf-executor-v2 @ 32265af, scripts/mal-fast/c1nf-executor-live.json
            "jito_enabled": False, "jito_tip_lamports": 0, "entry_tolerance_bps": 1500, "feed_heartbeat_max_age_ms": 150_000,
            # DEC-026 Amendment 1 item B (scripts/mal-fast/c1nf-executor-live.json); a healthy canary inside the seal window needs one
            "pick_file": "/srv/mal-cap-pick/picks.jsonl"}
A3_PATH = "/data/mal/structure-monitor/daily.jsonl"


def a3_record(**over) -> dict:
    """The shape of tools/pump_structure_monitor.py's record (fixed keys only), clean by default."""
    flags = {n: {"halt": False, "evaluated": True, "reason": "r"} for n in ("pins_changed", "boost_disabled", "boost_share_low",
                                                                            "boost_last_slice_early", "boost_budget_or_slices_changed",
                                                                            "synthetic_share_high")}
    rec = {"schema": "a3", "run_utc": "2026-10-17T00:41:07Z", "run_unix": int(NOW - 2400), "slot_time": {"ms_per_slot_median": 218.2},
           "halt": {"any": False, "flags": flags}, "warn": {"any": False, "flags": {"program_changed": {"warn": False, "evaluated": True, "reason": "r"}}}}
    for k, v in over.items():
        rec[k] = v
    return rec


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
            dc.COUNTERS_FILE: json.dumps({"halts": {}, "sells_landed": 3, "sells_late": 0, "tier_attempts": 3,
                                          "tier_state": {"tier": "T1", "since_ms": ms(NOW - 7200)}}).encode(),
            dc.EXTRA_FILE: json.dumps({"schema": "c1nf_extra_v1", "late_window": [0, 0, 0], "late_alert_on": False, "counts": {}}).encode(),
            dc.LEDGER_FILE: ledger({"kind": "start", "user": WALLET, "ts_ms": ms(NOW - 7200)}, {"kind": "decision", "ts_ms": ms(NOW - 600)}),
            WATCH_SVC: WSVC, WATCH_TMR: WTMR, f"{dc.PINNED}/{dc.WATCH_SERVICE}": WSVC, f"{dc.PINNED}/{dc.WATCH_TIMER}": WTMR,
            dc.WATCH_STATE: json.dumps({"ts": NOW - 120}).encode(),
        }
        self.modes = {dc.C1NF_DIR: "mal-live:mal-live:700", dc.C1NF_ETC: "root:root:755", dc.LIVE_OK: "root:root:644", dc.TIER_FILE: "root:root:644",
                      dc.FINAL_MARKER: "root:root:644"}
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

    def read_tail(self, path, n):
        self.read_log.append(path)
        data = self.files.get(path)
        return None if data is None else data[-n:]

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
             "reason": None if i % 3 else "buy_failed", "mint": f"M{i}"}
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
    host.files[dc.EXTRA_FILE] = json.dumps({"late_window": [0] * 17 + [1] * 3}).encode()
    host.files[dc.STATE_FILE] = json.dumps({"attempts": 9, "realized_lamports": -2_000_000, "pending": {},
                                            "open": {"A": {"stuck": True, "spend": 1}, "B": {"spend": 1}, "C": {"spend": 1}}}).encode()
    a = alerts(go(host, balance=lambda w, e: FUNDED - 2_000_003)[1])
    assert {"c1nf_live_halt", "c1nf_stuck_position", "c1nf_open_cap", "c1nf_late_sells"} <= set(a)


def test_budget_stop_and_feed_refusals():
    host = FakeHost()
    host.files[dc.LEDGER_FILE] = ledger(*[{"kind": "skip", "ts_ms": ms(NOW - 60 - i), "reason": r} for i, r in
                                          enumerate(["total_loss_stop", "feed_stale", "feed_stale", "feed_stale", "stale_pick", "stale_pick", "stale_pick"])])
    a = alerts(go(host)[1])
    assert {"c1nf_budget_stop_total_loss_stop", "c1nf_feed_refusals", "c1nf_stale_picks"} <= set(a)


def v2_refusal(t_ms: int, reason: str, i: int = 0) -> list[dict]:
    """What v2 (32265af) writes for one refusal: C1NFExecutor._refuse -> H5's skip row, then _set_status's pick_status row, same reason."""
    return [{"kind": "skip", "ts_ms": t_ms, "mint": f"M{i}", "reason": reason},
            {"kind": "pick_status", "ts_ms": t_ms, "mint": f"M{i}", "pick_id": f"p{i}", "status": "unfilled", "reason": reason, "monitored": True}]


@pytest.mark.parametrize("reason", ["bad_pick:ref_state_missing", "bad_pick:ref_state", "bad_pick:model_sha", "bad_pick:missing_q_lamports",
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


def attempt_rows(n_failed: int, n: int = 30, fail_reason: str = "buy_failed") -> list[dict]:
    """v2's pick_status rows of n buy attempts: _resolve_pick writes filled, or unfilled with buy_failed / buy_expired."""
    return [{"kind": "pick_status", "ts_ms": ms(NOW - 600 + i), "monitored": True, "status": "unfilled" if i < n_failed else "filled",
             "reason": fail_reason if i < n_failed else None} for i in range(n)]


def test_fill_rate_under_line_is_info():
    host = FakeHost()
    host.files[dc.LEDGER_FILE] = ledger(*attempt_rows(8))
    rc, out = go(host)
    assert "c1nf_fill_rate" not in alerts(out) and "22 of the last 30 buy attempts filled" in out


@pytest.mark.parametrize("reason", ["buy_failed", "buy_expired"])
def test_fill_rate_over_the_line_alerts(reason):
    host = FakeHost()
    host.files[dc.LEDGER_FILE] = ledger(*attempt_rows(9, fail_reason=reason))  # 9 / 30 = 0.30 > 0.289
    assert "c1nf_fill_rate" in alerts(go(host)[1])


def test_fill_rate_counts_attempts_not_refusals():
    # rule 8 is over buy attempts: a refusal before the send (feed outage, stale pick, price guard) is monitored by rule 1 but is no attempt
    host = FakeHost()
    refusals = [r for i, why in enumerate(["feed_stale"] * 20 + ["stale_pick"] * 10 + ["price_moved"] * 10)
                for r in v2_refusal(ms(NOW - 900 + i), why, i)]
    host.files[dc.LEDGER_FILE] = ledger(*refusals, *attempt_rows(2))
    rc, out = go(host)
    assert "c1nf_fill_rate" not in alerts(out) and "28 of the last 30 buy attempts filled" in out
    host.files[dc.LEDGER_FILE] = ledger(*refusals)
    rc, out = go(host)
    assert "c1nf_fill_rate" not in alerts(out) and "buy attempts" not in out


# ---- seal inputs, tier, late sells, A3 (re-review of 7ce15d3) ----

@pytest.mark.parametrize("now,expect", [(dc.SEAL_START_S - dc.SEAL_WARN_S - 60, None), (dc.SEAL_START_S - 3600, "c1nf_seal_no_pick_file"),
                                        (NOW, "c1nf_seal_no_pick_file")])
def test_live_config_without_pick_file(now, expect):
    host = FakeHost()
    host.files[dc.LIVE_CONFIG] = json.dumps({k: v for k, v in LIVE_CFG.items() if k != "pick_file"}).encode()
    rc, out = go(host, now=now)
    assert ("c1nf_seal_no_pick_file" in alerts(out)) is (expect is not None)
    if expect is None:
        assert "has no pick_file yet" in out


def test_final_marker_missing_in_the_seal_window():
    host = FakeHost()
    del host.modes[dc.FINAL_MARKER]
    assert "c1nf_seal_final_missing" in alerts(go(host)[1])
    assert "c1nf_seal_final_missing" not in alerts(go(host, now=dc.SEAL_START_S - 60)[1])  # before the window it is not needed
    for d in (host.files, host.modes, host.mtimes):
        d.pop(dc.LIVE_OK, None)
    assert "c1nf_seal_final_missing" not in alerts(go(host)[1])  # gate closed: nothing would trade anyway
    assert dc.FINAL_MARKER in dc.PRIV_STAT and dc.FINAL_MARKER == "/var/lib/mal-live/c1nf/FINAL_WRITTEN"


def test_tier_report_and_unapplied_alert():
    host = FakeHost()
    rc, out = go(host)
    assert "tier: executor=T1 file=T1 (applies T1)" in out and "attempts_in_tier=3" in out
    host.files[dc.COUNTERS_FILE] = json.dumps({"halts": {}, "tier_state": {"tier": "T2", "since_ms": ms(NOW - 7200)}}).encode()
    assert "c1nf_tier_unapplied" in alerts(go(host)[1])  # the file applies T1 since 2 h, the executor holds T2
    host.mtimes[dc.TIER_FILE] = NOW - 300
    assert "c1nf_tier_unapplied" not in alerts(go(host)[1])  # inside the 15 minute grace
    host.mtimes[dc.TIER_FILE] = NOW - 7200
    host.files[dc.TIER_FILE] = b"T2\n"  # T2 is inactive: the file still applies T1, and T2 in the file is its own alert
    a = alerts(go(host)[1])
    assert "c1nf_tier_unapplied" in a and "c1nf_tier_t2" in a
    host.props["ActiveState"] = "inactive"
    assert "c1nf_tier_unapplied" not in alerts(go(host)[1])  # only while it runs live


def test_late_sells_follow_the_executors_window_not_the_lifetime_counters():
    host = FakeHost()
    host.files[dc.COUNTERS_FILE] = json.dumps({"halts": {}, "sells_landed": 40, "sells_late": 8}).encode()  # 20% lifetime, all long ago
    host.files[dc.EXTRA_FILE] = json.dumps({"late_window": [1] * 2 + [0] * 18, "late_alert_on": False}).encode()  # 10%: not above
    rc, out = go(host)
    assert "c1nf_late_sells" not in alerts(out) and "late sells: 2 of the last 20 landed" in out
    host.files[dc.EXTRA_FILE] = json.dumps({"late_window": [1] * 3 + [0] * 17}).encode()
    assert "c1nf_late_sells" in alerts(go(host)[1])
    host.files[dc.EXTRA_FILE] = json.dumps({"late_window": [1] * 3, "late_alert_on": True}).encode()  # the executor's own latch
    assert "c1nf_late_sells" in alerts(go(host)[1])


def test_extra_file_is_read_through_fixed_keys_only():
    a, b = FakeHost(), FakeHost()
    base = {"late_window": [0, 1, 0], "counts": {"pre_window": 4, "seal_bad_pick": 1}, "outcomes_unpriced": 2}
    a.files[dc.EXTRA_FILE] = json.dumps(base).encode()
    b.files[dc.EXTRA_FILE] = json.dumps({**base, "picks": {f"M{i}:1": {"mint": f"M{i}", "status": "filled", "outcome_pct": 12.5 * i,
                                                                      "synthetic": True, "migration_class": "synthetic"} for i in range(9)},
                                         "last_exit_ms": {"M1": 1}, "otail": {"path": "/srv/x"}}).encode()
    out_a, out_b = go(a)[1], go(b)[1]
    assert out_a == out_b and "pre_window x4, seal_bad_pick x1; shadow outcomes without the pinned leg x2" in out_a
    assert "12.5" not in out_b and not re.search("synth", out_b.replace("synthetic_share_high", ""), re.I)
    assert dc.EXTRA_FILE in dc.PRIV_READ and dc.EXTRA_FILE in dc.PRIV_STAT


def test_extra_file_gone_after_a_run_alerts():
    host = FakeHost()
    del host.files[dc.EXTRA_FILE]
    host.files[dc.COUNTERS_FILE] = json.dumps({"halts": {}, "sells_landed": 0, "plans": {}}).encode()
    assert "c1nf_extra_missing" not in alerts(go(host)[1])  # the counters show no run yet (v2's extra_reset_problem: nothing to guard)
    host.files[dc.COUNTERS_FILE] = json.dumps({"halts": {}, "sells_landed": 3, "tail_path": "/srv/mal-c1nf-shadow/c1nf-picks-x.jsonl"}).encode()
    assert "c1nf_extra_missing" in alerts(go(host)[1])


def test_executor_start_alerts_have_a_meaning():
    host = FakeHost()
    host.files[dc.LEDGER_FILE] = ledger({"kind": "alert", "ts_ms": ms(NOW - 60), "alert": "seal_oracle_missing"},
                                        {"kind": "alert", "ts_ms": ms(NOW - 60), "alert": "seal_final_marker_missing"},
                                        {"kind": "decision", "ts_ms": ms(NOW - 40)})
    rc, out = go(host)
    assert {"c1nf_executor_alert_seal_oracle_missing", "c1nf_executor_alert_seal_final_marker_missing"} <= set(alerts(out))
    assert "every buy from then is refused" in out and "every buy in the seal window is refused" in out


def test_halt_meaning_lists_only_what_the_executor_latches():
    assert set(dc.HALT_MEANING) == {"fill_selection_adverse", "landing_p50_gt_1_9s", "out_of_rule_entry", "stuck_position", "model_sha_mismatch"}
    for gone in ("twin_divergence", "pins_changed", "program_changed", "ms_per_slot_out_of_range", "model_hash_changed", "late_sells_gt_5pct"):
        assert gone not in dc.HALT_MEANING
    h5src = (ROOT / "tools/h5_executor.py").read_text()
    assert '_latch("out_of_rule_entry"' in h5src and '_latch("stuck_position"' in h5src
    v2 = ROOT / "tools/c1nf_executor.py"
    if v2.is_file():  # the executor PR's names, once it is on the branch
        src = v2.read_text()
        for name in ("fill_selection_adverse", "landing_p50_gt_1_9s", "model_sha_mismatch", "exit_late_share_gt_10pct"):
            assert f'"{name}"' in src, name


def test_a3_not_given_is_an_info_line():
    rc, out = go(FakeHost())
    assert rc == 0 and "A3 not read (no --a3-file)" in out


@pytest.mark.parametrize("change,expect", [
    ({}, []),
    ({"halt": {"flags": {**a3_record()["halt"]["flags"], "pins_changed": {"halt": True, "evaluated": True, "reason": "x"}}}}, ["c1nf_a3_halt"]),
    ({"warn": {"flags": {"program_changed": {"warn": True, "evaluated": True, "reason": "x"}}}}, ["c1nf_a3_halt"]),
    ({"slot_time": {"ms_per_slot_median": 470.0}}, ["c1nf_a3_halt"]),
    ({"slot_time": {"ms_per_slot_median": 149.0}}, ["c1nf_a3_halt"]),
    ({"slot_time": {}}, ["c1nf_a3_not_evaluated"]),
    ({"warn": {"flags": {"program_changed": {"warn": False, "evaluated": False, "reason": "x"}}}}, ["c1nf_a3_not_evaluated"]),
    ({"halt": {"flags": {**a3_record()["halt"]["flags"], "synthetic_share_high": {"halt": True, "evaluated": True, "reason": "x"},
                         "boost_disabled": {"halt": True, "evaluated": True, "reason": "x"}}}}, ["c1nf_a3_alert"]),
    ({"run_unix": int(NOW - 40 * 3600)}, ["c1nf_a3_stale"]),
])
def test_a3_rule6(change, expect):
    host = FakeHost()
    host.files[A3_PATH] = (json.dumps(a3_record(run_utc="old")) + "\n" + json.dumps(a3_record(**change)) + "\n").encode()
    rc, out = go(host, "--a3-file", A3_PATH)
    got = [a for a in alerts(out) if a.startswith("c1nf_a3")]
    assert got == expect, out
    if not expect:
        assert "no rule 6 halt" in out and rc == 0
    if "c1nf_a3_halt" in expect:
        assert "sudo touch /var/lib/mal-live/c1nf/STOP" in out
    if expect == ["c1nf_a3_alert"]:
        assert "synthetic_share_high, boost_disabled (alert only" in out and "class_blind" not in out


@pytest.mark.parametrize("data,expect", [(None, "c1nf_a3_missing"), (b"", "c1nf_a3_unreadable"), (b"{not json\n", "c1nf_a3_unreadable"),
                                         (b"[1, 2]\n", "c1nf_a3_unreadable")])
def test_a3_missing_or_unreadable(data, expect):
    host = FakeHost()
    if data is not None:
        host.files[A3_PATH] = data
    assert expect in alerts(go(host, "--a3-file", A3_PATH)[1])


def test_real_host_read_tail(tmp_path):
    f = tmp_path / "a3.jsonl"
    f.write_bytes(b"x" * 100 + b"\nlast\n")
    assert dc.Host().read_tail(str(f), 5) == b"last\n"
    assert dc.Host().read_tail(str(tmp_path / "none"), 5) is None
    (tmp_path / "link").symlink_to(f)
    with pytest.raises(dc.Unsafe):
        dc.Host().read_tail(str(tmp_path / "link"), 5)


@pytest.mark.parametrize("val", ["nan", "inf", "-0.5", "0"])
def test_invalid_funded_is_an_alert_not_a_crash(val):
    host = FakeHost()
    lines: list[str] = []
    rc = dc.main(["--funded-sol", val, "--wallet", WALLET, "--shadow-dir", "/x"], host=host, out=lines.append,
                 balance_fn=lambda w, e: FUNDED, now=NOW, checker=FakeChecker())
    out = "\n".join(lines)
    assert rc == 1 and "funded_invalid" in alerts(out) and "check_failed" not in out


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


@pytest.mark.parametrize("funded", ["nan", "inf", "-inf", "0", "-1"])
def test_watch_config_refuses_a_funded_value_that_is_not_finite_and_positive(funded):
    daily = watch.load_daily()
    env = {"C1NF_WATCH_FUNDED_SOL": funded, "C1NF_WATCH_SHADOW_DIR": "/x", "C1NF_WATCH_WALLET": WALLET}
    alerts_, obs = watch.collect(daily, FakeHost(), env, NOW, lambda w, e: FUNDED)
    assert "watch_config" in alerts_ and obs == {}


def test_watch_passes_the_a3_file_through():
    daily = watch.load_daily()
    env = {"C1NF_WATCH_FUNDED_SOL": "0.5", "C1NF_WATCH_SHADOW_DIR": "/x", "C1NF_WATCH_WALLET": WALLET}
    host = FakeHost()
    host.files[A3_PATH] = (json.dumps(a3_record(slot_time={"ms_per_slot_median": 470.0})) + "\n").encode()
    alerts_, _ = watch.collect(daily, host, {**env, "C1NF_WATCH_A3_FILE": A3_PATH}, NOW, lambda w, e: FUNDED - 1_000_000)
    assert "c1nf_a3_halt" in alerts_ and "watch_config" not in alerts_
    alerts_, _ = watch.collect(daily, host, env, NOW, lambda w, e: FUNDED - 1_000_000)
    assert not any(a.startswith("c1nf_a3") for a in alerts_)  # not configured: the engine's INFO line only
    alerts_, _ = watch.collect(daily, host, {**env, "C1NF_WATCH_A3_FILE": "daily.jsonl"}, NOW)
    assert "watch_config" in alerts_  # a relative path is refused


def test_watch_units_pass_their_allowlist_and_h5_units_do_not():
    chk = FAST / "check-c1nf-watch-unit.py"
    ok = [subprocess.run([sys.executable, "-I", str(chk), flag, str(FAST / f)], capture_output=True).returncode
          for flag, f in (("--watch-service", "mal-c1nf-watch.service"), ("--watch-timer", "mal-c1nf-watch.timer"))]
    assert ok == [0, 0]
    assert subprocess.run([sys.executable, "-I", str(chk), "--watch-service", str(FAST / "mal-h5-watch.service")], capture_output=True).returncode == 1
    assert b"/etc/mal-c1nf-key" in WSVC  # the second wallet's key directory is fenced off from the root watchdog


CAP_PICK = b"[Service]\nBindReadOnlyPaths=-/home/claude/data/cap-pick-oracle:/srv/mal-cap-pick\n"


def test_cap_pick_dropin_is_allowed_and_checked():
    """DEC-026 Amendment 1 item B: 20-cap-pick.conf is an expected drop-in, and it must pass check-c1nf-unit.py --cap-pick."""
    real = dc.load_unit_checker(FAST)  # the real check-c1nf-unit.py, not FakeChecker
    host = FakeHost()
    host.dropins = [dc.DROPIN_LIVE, dc.DROPIN_FEED, dc.DROPIN_CAP_PICK]
    host.files[dc.DROPIN_CAP_PICK] = CAP_PICK
    assert "c1nf_unit_files" not in alerts(go(host, checker=real)[1])
    for bad in (CAP_PICK.replace(b"/srv/mal-cap-pick", b"/srv/mal-c1nf-shadow"), CAP_PICK.replace(b"/home/claude/data", b"/var/lib/mal"),
                FEED):
        host = FakeHost()
        host.dropins = [dc.DROPIN_LIVE, dc.DROPIN_FEED, dc.DROPIN_CAP_PICK]
        host.files[dc.DROPIN_CAP_PICK] = bad
        assert "c1nf_unit_files" in alerts(go(host, checker=real)[1]), bad
