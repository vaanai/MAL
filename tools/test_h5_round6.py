"""Executor round 6 (claude/h5-executor 8dbb886): the daily check and the watchdog read the new alert and halt names (config_clamps_tier,
shadow_schema_mismatch, trigger_pre_unlinked, pre_unlinked_share) with their meanings, and the runbook says the final facts: the five tier-scaled
keys are not in the configs, every shadow key is required, and the shadow job runs at #477 head d3b69d0 or later. No host, no network, no key."""
from __future__ import annotations

import json
import re
from pathlib import Path

from tools import h5_executor
from tools.test_h5_daily_check import H5, NOW, FakeHost, alerts, dc, go, ledger
from tools.test_h5_watch import Poster, world
from tools.test_h5_watch import go as watch_go

ROOT = Path(__file__).resolve().parent.parent
EXECUTOR_SRC = (ROOT / "tools/h5_executor.py").read_text()
RUNBOOK = ROOT / "docs/runbooks/h5-executor.md"
NEW_ALERTS = ("config_clamps_tier", "shadow_schema_mismatch", "trigger_pre_unlinked")


def runbook() -> str:
    return RUNBOOK.read_text()


def alert_world(*names, n=1) -> FakeHost:
    h = FakeHost()
    rows = [{"kind": "alert", "alert": name, "ts_ms": int((NOW - 200) * 1000)} for name in names for _ in range(n)]
    h.files[f"{H5}/live/h5-ledger.jsonl"] = ledger({"kind": "start", "user": dc.WALLET, "ts_ms": 1}, {"kind": "decision", "ts_ms": int((NOW - 300) * 1000)}, *rows)
    return h


def test_the_new_names_exist_in_the_executor_and_have_meanings():
    for name in NEW_ALERTS:
        assert f'self._alert("{name}"' in EXECUTOR_SRC, name  # an alert row the executor really writes
        assert name in dc.ALERT_MEANING, name
    assert 'self._latch("pre_unlinked_share"' in EXECUTOR_SRC and "pre_unlinked_share" in dc.HALT_MEANING
    assert h5_executor.PRE_UNLINKED_MAX_SHARE == 0.15 and h5_executor.PRE_UNLINKED_MIN_LANDED == 20  # the numbers in the halt's meaning and the runbook
    assert "base_breaks_unresolved" in h5_executor.SHADOW_REQUIRED_KEYS and "base_breaks_unresolved_settled" in h5_executor.SHADOW_REQUIRED_KEYS
    assert "d3b69d0" in dc.ALERT_MEANING["shadow_schema_mismatch"] and "d3b69d0" in (ROOT / "scripts/mal-fast/h5-daily-check.py").read_text()


def test_each_new_alert_is_reported_with_its_meaning(tmp_path):
    for name in NEW_ALERTS:
        rc, out = go(alert_world(name, n=2), tmp=tmp_path / name)
        assert rc == 1 and f"ALERT h5_executor_alert_{name}: the executor wrote 2 ALERT {name} row(s)" in out, name
        assert dc.ALERT_MEANING[name] in out, name
    rc, out = go(alert_world("shadow_schema_mismatch"), tmp=tmp_path / "msg")
    assert "d3b69d0 or later" in out
    rc, out = go(alert_world("trigger_pre_unlinked"), tmp=tmp_path / "pre")
    assert "15% of landed buys" in out and "pre_unlinked_share" in out
    rc, out = go(alert_world("config_clamps_tier"), tmp=tmp_path / "clamp")
    assert "the shipped configs set none" in out


def test_the_pre_unlinked_share_halt_is_reported_with_its_meaning(tmp_path):
    h = FakeHost()
    h.files[f"{H5}/live/h5-counters.json"] = json.dumps({"halts": {"pre_unlinked_share": {"landed": 24, "pre_unlinked": 5, "share": 0.2083}}}).encode()
    rc, out = go(h, tmp=tmp_path)
    assert rc == 1 and "ALERT h5_live_halt: latched: pre_unlinked_share (" in out and "more than 15% of landed buys" in out and "never followed by a retune" in out


def test_a_shadow_record_missing_any_required_key_is_the_wrong_head(tmp_path):
    """Every shadow key is required now (including base_breaks_unresolved): one refusal as bad_intent:missing_<key> is enough for h5_feed_schema."""
    for key in h5_executor.SHADOW_REQUIRED_KEYS:
        rows = [{"kind": "skip", "reason": f"bad_intent:missing_{key}", "ts_ms": int((NOW - 100) * 1000)}]
        h = FakeHost()
        h.files[f"{H5}/live/h5-ledger.jsonl"] = ledger({"kind": "start", "user": dc.WALLET, "ts_ms": 1}, *rows)
        rc, out = go(h, tmp=tmp_path / key)
        assert "h5_feed_schema" in alerts(out) and "d3b69d0 or later" in out and f"bad_intent:missing_{key} x1" in out, key


def test_the_watchdog_posts_the_three_alerts_and_the_halt(tmp_path):
    h, p = world(), Poster()
    rows = [{"kind": "alert", "alert": name, "ts_ms": int((NOW - 100) * 1000)} for name in NEW_ALERTS]
    h.files[f"{H5}/live/h5-ledger.jsonl"] = ledger({"kind": "start", "user": dc.WALLET, "ts_ms": 1}, *rows)
    h.files[f"{H5}/live/h5-counters.json"] = json.dumps({"halts": {"pre_unlinked_share": {}}}).encode()
    assert watch_go(h, tmp_path / "s.json", p) == 0 and len(p.posts) == 1
    text = p.posts[0][1]
    for name in NEW_ALERTS:
        assert f"ALERT h5_executor_alert_{name}" in text and dc.ALERT_MEANING[name] in text, name
    assert "ALERT h5_live_halt: latched: pre_unlinked_share (" in text
    assert watch_go(h, tmp_path / "s.json", p, NOW + 300) == 0 and len(p.posts) == 1  # not repeated inside 6 h


def test_the_runbook_states_the_round_6_facts():
    t = runbook()
    assert "3dbe1de" not in t and "fe7eb43" not in t and t.count("d3b69d0") >= 4
    assert "#477 head, `d3b69d0` or later" in t and "every key the executor requires" in t and "`shadow_schema_mismatch`" in t
    # the five tier-scaled keys are the table's, not the configs'
    for key in ("stake_lamports", "max_open", "max_trades_per_day", "daily_loss_lamports", "total_loss_lamports"):
        assert f"`{key}`" in t, key
    assert "`config_clamps_tier`" in t and "the tier table owns those five" in t
    # the alert table and the halt table carry every new name with its meaning
    tbl = t[t.index("Executor `alert` rows the daily check"):t.index("Trigger refusals (`skip` rows")]
    for name in NEW_ALERTS:
        assert f"| `{name}` |" in tbl, name
    assert "| `pre_unlinked_share` |" in t and "15% of landed buys" in t and "judged from 20 landed" in t
    assert "| `bad_intent:missing_<key>` |" in t and "at most once per 10 minutes" in t
    for name in set(re.findall(r'self\._latch\("([a-z0-9_]+)"', EXECUTOR_SRC)):
        assert f"`{name}`" in t, name  # every latched halt is in the runbook's table
    going = t[t.index("## Going live (manager, then Helm)"):t.index("## Step up / step down a tier")]
    assert "d3b69d0 or later" in going
