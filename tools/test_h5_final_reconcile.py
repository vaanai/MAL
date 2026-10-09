"""Reconciliation of the live-unit tooling with the executor branch's final head (9c5618b): halt names, trigger refusals, the final configs,
the runbook's statements. The daily check and the watchdog read the executor's names from its ledger and counters; these tests keep the two
in step by reading the executor's source. No host, no network, no key."""
from __future__ import annotations

import datetime
import json
import re
from pathlib import Path

from tools import h5_executor
from tools.test_h5_daily_check import H5, NOW, FakeHost, alerts, dc, go, ledger
from tools.test_h5_watch import Poster, hw, world
from tools.test_h5_watch import go as watch_go

ROOT = Path(__file__).resolve().parent.parent
FAST = ROOT / "scripts/mal-fast"
RUNBOOK = ROOT / "docs/runbooks/h5-executor.md"
EXECUTOR_SRC = (ROOT / "tools/h5_executor.py").read_text()


def skips(reason: str, n: int, age_s: int = 600) -> list[dict]:
    return [{"kind": "skip", "reason": reason, "ts_ms": int((NOW - age_s) * 1000)} for _ in range(n)]


def ledger_world(*rows) -> FakeHost:
    h = FakeHost()
    h.files[f"{H5}/live/h5-ledger.jsonl"] = ledger({"kind": "start", "user": dc.WALLET, "ts_ms": int((NOW - 7200) * 1000)}, *rows)
    return h


# --- halts ------------------------------------------------------------------------------------------------------------------

def test_latched_halts_are_named_with_their_meaning_and_match_the_executor(tmp_path):
    in_code = set(re.findall(r'_latch\("([a-z0-9_]+)"', EXECUTOR_SRC))
    assert in_code and in_code == set(dc.HALT_MEANING), in_code ^ set(dc.HALT_MEANING)  # every halt the executor can latch is named, none is stale
    for name, meaning in dc.HALT_MEANING.items():
        h = FakeHost()
        h.files[f"{H5}/live/h5-counters.json"] = json.dumps({"halts": {name: {}}}).encode()
        rc, out = go(h, tmp=tmp_path / name)
        assert rc == 1 and f"ALERT h5_live_halt: latched: {name} ({meaning})" in out and "never followed by a retune" in out, name
    h = FakeHost()  # an unknown name is still shown; a hostile one is not
    h.files[f"{H5}/live/h5-counters.json"] = json.dumps({"halts": {"future_halt": {}, "x y\nSECRET": {}}}).encode()
    rc, out = go(h, tmp=tmp_path / "unknown")
    assert "latched: future_halt" in out and "SECRET" not in out


# --- refusals ---------------------------------------------------------------------------------------------------------------

def test_refusal_names_exist_in_the_executor():
    for name in (*dc.S0_REFUSALS, *dc.NEW_FIELD_REFUSALS, *dc.EXPECTED_REFUSALS):
        assert f'"{name}"' in EXECUTOR_SRC, name
    assert 'f"bad_intent:missing_{exc.args[0]}"' in EXECUTOR_SRC  # the pattern the schema alert keys on


def test_s0_refusals_alert_from_three_inside_the_six_hour_window(tmp_path):
    for n, alert in ((2, False), (3, True), (10, True)):
        rc, out = go(ledger_world(*skips("s0_recv_late", n - 1), *skips("s0_unverifiable", 1)), tmp=tmp_path / f"s0-{n}")
        assert ("h5_s0_refusals" in alerts(out)) == alert, (n, out)
    assert "s0_recv_late x" in out and "backlogged" in out
    rc, out = go(ledger_world(*skips("s0_recv_late", 5, age_s=7 * 3600)), tmp=tmp_path / "old")
    assert "h5_s0_refusals" not in alerts(out)


def test_feed_schema_alert_means_the_shadow_job_is_at_the_wrong_head(tmp_path):
    field = "bad_intent:base_breaks_unresolved_settled"
    rc, out = go(ledger_world(*skips("bad_intent:missing_s0_minus_announced_slots", 1)), tmp=tmp_path / "missing")
    assert "h5_feed_schema" in alerts(out) and "d3b69d0 or later" in out
    decision = {"kind": "decision", "ts_ms": int((NOW - 60) * 1000)}
    rc, out = go(ledger_world(*skips(field, 5), decision), tmp=tmp_path / "with_decision")
    assert "h5_feed_schema" not in alerts(out)  # refusals next to real decisions are data quality, not the wrong head
    rc, out = go(ledger_world(*skips(field, 4)), tmp=tmp_path / "four")
    assert "h5_feed_schema" not in alerts(out)
    rc, out = go(ledger_world(*skips(field, 3), *skips("bad_intent:s0_minus_announced_slots", 2)), tmp=tmp_path / "five")
    assert "h5_feed_schema" in alerts(out) and "base_breaks_unresolved_settled x3" in out


def test_the_sealed_stub_and_other_refusals_are_not_alerts_and_hostile_reasons_are_not_printed(tmp_path):
    h = ledger_world(*skips("bad_intent:suppressed", 500), *skips("bad_intent:v_missing", 20), *skips("max_open", 3), *skips("bad_intent:gap", 5))
    h.files[f"{H5}/live/h5-ledger.jsonl"] += ledger({"kind": "skip", "reason": "SECRET value with spaces\nand more", "ts_ms": int((NOW - 60) * 1000)})
    rc, out = go(h, tmp=tmp_path)
    assert rc == 0 and "refusals in the last 6 h" in out and "bad_intent:suppressed x500" in out and "SECRET" not in out
    assert not {"h5_feed_schema", "h5_s0_refusals"} & set(alerts(out))


def test_refusal_and_halt_alerts_reach_discord_and_the_sealed_stub_does_not(tmp_path):
    h, p = world(), Poster()
    h.files[f"{H5}/live/h5-ledger.jsonl"] = ledger({"kind": "start", "user": dc.WALLET, "ts_ms": 1}, *skips("bad_intent:suppressed", 300))
    watch_go(h, tmp_path / "stub.json", p)
    assert p.posts == []
    h.files[f"{H5}/live/h5-ledger.jsonl"] = ledger({"kind": "start", "user": dc.WALLET, "ts_ms": 1}, *skips("s0_recv_late", 4))
    watch_go(h, tmp_path / "s0.json", p)
    assert len(p.posts) == 1 and "ALERT h5_s0_refusals" in p.posts[0][1]
    p2 = Poster()
    h.files[f"{H5}/live/h5-ledger.jsonl"] = ledger({"kind": "start", "user": dc.WALLET, "ts_ms": 1}, *skips("bad_intent:missing_base_breaks_unresolved", 1))
    watch_go(h, tmp_path / "schema.json", p2)
    assert "ALERT h5_feed_schema" in p2.posts[0][1] and "d3b69d0" in p2.posts[0][1]
    p3 = Poster()
    h.files[f"{H5}/live/h5-counters.json"] = json.dumps({"halts": {"late_sells_gt_5pct": {}}}).encode()
    watch_go(h, tmp_path / "halt.json", p3)
    assert any("latched: late_sells_gt_5pct (more than 5% of landed sells late)" in c for _, c in p3.posts)
    assert hw.WEBHOOK_RE.match("https://discord.com/api/webhooks/1/x")


# --- the final configs and the runbook -----------------------------------------------------------------------------------------

def test_pinned_configs_are_the_executor_branchs_final_ones():
    """9c5618b: intents_file, heartbeat 150000, live end_ms 2026-10-16T00:30Z, code constants not configurable, LIVE_OK pinned."""
    live = json.loads((FAST / "h5-executor-live.json").read_text())
    dry = json.loads((FAST / "h5-executor.json").read_text())
    for cfg in (live, dry):
        assert cfg["intents_file"] == "/srv/mal-h5-shadow" and cfg["feed_heartbeat_max_age_ms"] == 150000
        assert not {"late_sell_min_n", "sell_priority_lamports", "escalated_priority_lamports"} & set(cfg)
        assert not set(h5_executor.PINNED_PATH_KEYS) & set(cfg)
        h5_executor.H5Limits.from_config(cfg)  # the executor accepts both files as shipped
        assert not {"stake_lamports", "max_open", "max_trades_per_day", "daily_loss_lamports", "total_loss_lamports"} & set(cfg)  # the tier table owns these five
        for tier, table in h5_executor.TIERS.items():  # and what is shipped clamps no tier below its table (what config_clamps_tier would report)
            lim = h5_executor.H5Limits.from_config(cfg, tier)
            assert all(getattr(lim, k) == v for k, v in table.items()), tier
    assert live["end_ms"] == 1792110600000 and "end_ms" not in dry
    assert datetime.datetime.fromtimestamp(live["end_ms"] / 1000, datetime.timezone.utc).isoformat() == "2026-10-16T00:30:00+00:00"
    for key in ("late_sell_min_n", "sell_priority_lamports", "escalated_priority_lamports"):
        try:
            h5_executor.H5Limits.from_config({**live, key: 1})
        except ValueError as exc:
            assert "not configurable" in str(exc)
        else:
            raise AssertionError(key)


def test_runbook_states_the_final_facts():
    t = RUNBOOK.read_text()
    assert t.count("d3b69d0") >= 3 and "or later" in t and "2026-10-16T00:30Z" in t and "1792110600000" in t
    assert "s0_recv_late" in t and "s0_unverifiable" in t and "bad_intent:suppressed" in t and "h5_feed_schema" in t and "h5_s0_refusals" in t
    for name in set(re.findall(r'_latch\("([a-z0-9_]+)"', EXECUTOR_SRC)):
        assert f"`{name}`" in t, name  # every latched halt is in the runbook's table
    assert "live_ok=live_ok_missing (/etc/mal-h5/LIVE_OK)" in t and "live_ok=valid (/etc/mal-h5/LIVE_OK)" in t
    # the status strings the runbook expects are what the executor prints
    assert "live_ok={live_ok_valid() or 'valid'} ({LIVE_OK_PATH})" in EXECUTOR_SRC and "stop_file={Path(" in EXECUTOR_SRC
    assert "late_sell_min_n" in t and "code constants" in t
    going = t[t.index("## Going live (manager, then Helm)"):t.index("## Stop, halt, status")]
    assert "d3b69d0 or later" in going
