import hashlib
import json
import os
import sys
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pytest

pytest.importorskip("cryptography")
import kalshi_live_caps as kc
import kalshi_live_cli as cli
import kalshi_live_client as kcl
import kalshi_live_guard as kgd
import kalshi_live_kill as kk
import kalshi_live_log as kl
import kalshi_live_orders as ko
import kalshi_live_testutil as tu

NOW = 1_800_000_000.0   # 2027-01-15, a UTC day
DAY = "2027-01-15"
ACK = {"order": {"order_id": "o-1", "client_order_id": "lip-1", "status": "resting"}}


def intent(**kw):
    base = dict(ticker="A", outcome="yes", price=D("0.40"), count=D("25"), client_order_id="lip-1",
                expiration_ts=int(NOW) + 450, order_group_id="grp-1")
    base.update(kw)
    return ko.OrderIntent(**base)


class Routes:
    """Transport answering by (method, path); records every request."""

    def __init__(self, balance_cents=25000, positions=(), resting=()):
        self.reqs = []
        self.balance = balance_cents
        self.positions, self.resting = list(positions), list(resting)

    def __call__(self, req):
        self.reqs.append(req)
        path = req.url.split("/trade-api/v2")[1].split("?")[0]
        body = {"/portfolio/balance": {"balance": self.balance},
                "/portfolio/positions": {"market_positions": self.positions, "cursor": ""},
                "/portfolio/events/orders": ({"orders": self.resting, "cursor": ""} if req.method == "GET"
                                             else ACK if req.method == "POST" else {"cancelled_order_ids": []}),
                }[path]
        return kcl.HttpResponse(200, json.dumps(body).encode(), {})

    def sent(self):
        return [(r.method, r.url.split("/trade-api/v2")[1].split("?")[0]) for r in self.reqs]


@pytest.fixture(scope="module")
def key():
    return tu.make_rsa_key()


def build(tmp_path, key, routes, baseline=(D("250"), D("250"))):
    import kalshi_live_gate as kg
    d, _ = tu.make_key_dir(tmp_path / "kd", key=key)
    armed = kg.ArmedContext(tu.FAKE_KEY_ID, tu.FAKE_BASE_URL, "demo", d / "kalshi-private.pem", "0" * 64)
    log = kl.EventLog(tmp_path / "ev.jsonl", clock=lambda: NOW)
    client = kcl.KalshiClient.from_armed(armed, log, transport=routes, sleep=lambda s: None)
    kill = kk.KillSwitch(tmp_path / "STOP", tmp_path / "hb.json", client.cancel_all, log, clock=lambda: NOW)
    kk.write_heartbeat(tmp_path / "hb.json", clock=lambda: NOW)
    store = kc.BaselineStore(tmp_path / "base.json")
    if baseline:
        store.init(baseline[0], DAY, baseline[1])
    g = kgd.GuardedOrders(client, kill, kc.Caps(), store, log, clock=lambda: NOW, marks_fn=lambda ps: {"A": D("0.5")})
    return g, log


def test_happy_path_reads_state_then_places(tmp_path, key):
    r = Routes()
    g, log = build(tmp_path, key, r)
    assert g.place(intent()).order_id == "o-1"
    assert r.sent() == [("GET", "/portfolio/positions"), ("GET", "/portfolio/events/orders"),
                        ("GET", "/portfolio/balance"), ("POST", "/portfolio/events/orders")]
    assert [e["event"] for e in log.read() if e["event"].startswith("order")] == ["order_allowed", "order_placed"]


def test_stop_file_cancels_all_and_nothing_is_placed(tmp_path, key):
    r = Routes()
    g, log = build(tmp_path, key, r)
    (tmp_path / "STOP").write_text("")
    res = g.place(intent())
    assert res.code == "stop_file"
    assert r.sent() == [("DELETE", "/portfolio/events/orders")]


def test_stale_heartbeat_blocks_placing(tmp_path, key):
    r = Routes()
    g, _ = build(tmp_path, key, r)
    kk.write_heartbeat(tmp_path / "hb.json", clock=lambda: NOW - 91)
    assert g.place(intent()).code == "heartbeat_stale"
    assert ("POST", "/portfolio/events/orders") not in r.sent() and ("DELETE", "/portfolio/events/orders") in r.sent()


def test_exchange_inventory_over_i_max_refuses_before_any_post(tmp_path, key):
    r = Routes(positions=[{"ticker": "A", "position": 51, "market_exposure": 2000}])
    g, log = build(tmp_path, key, r)
    res = g.place(intent())
    assert res.code == "inventory_breach"
    assert ("POST", "/portfolio/events/orders") not in r.sent()
    ev = [e for e in log.read() if e["event"] == "refusal"][-1]
    assert ev["code"] == "inventory_breach" and ev["state_source"] == "exchange"


def test_daily_loss_stop_cancels_all_but_does_not_latch_stop(tmp_path, key):
    r = Routes(balance_cents=21000)  # 250 -> 210, loss 40 >= 30, trial loss 40 < 75
    g, _ = build(tmp_path, key, r)
    assert g.place(intent()).code == "daily_loss_stop"
    assert ("DELETE", "/portfolio/events/orders") in r.sent() and not (tmp_path / "STOP").exists()


def test_trial_loss_stop_cancels_all_and_latches_stop_file(tmp_path, key):
    r = Routes(balance_cents=17000)  # 250 -> 170, loss 80 >= 75
    g, log = build(tmp_path, key, r)
    assert g.place(intent()).code == "trial_loss_stop"
    assert (tmp_path / "STOP").exists()
    r.reqs.clear()
    assert g.place(intent()).code == "stop_file" and r.sent() == [("DELETE", "/portfolio/events/orders")]
    assert "stop_file_written" in [e["event"] for e in log.read()]


def test_no_baseline_refuses_and_new_day_rolls_day_baseline(tmp_path, key):
    r = Routes()
    g, _ = build(tmp_path, key, r, baseline=None)
    assert g.place(intent()).code == "no_baseline"
    store = kc.BaselineStore(tmp_path / "base.json")
    store.init(D("250"), "2027-01-14", D("250"))                    # yesterday's baseline
    r.balance = 21000                                               # 40 down since yesterday's start
    res = g.place(intent())
    assert res is not None and store.load().day == DAY and store.load().day_start_equity == D("210")
    assert getattr(res, "code", None) != "daily_loss_stop"          # rolled to today's equity; trial loss 40 < 75


def test_dry_run_guard_logs_reads_and_the_order_and_calls_no_transport(tmp_path):
    log = kl.EventLog(tmp_path / "ev.jsonl", clock=lambda: NOW)

    def boom(req):
        raise AssertionError("no transport in dry-run")
    client = kcl.KalshiClient(log, tu.FAKE_BASE_URL, transport=boom)
    kk.write_heartbeat(tmp_path / "hb.json", clock=lambda: NOW)
    kill = kk.KillSwitch(tmp_path / "STOP", tmp_path / "hb.json", client.cancel_all, log, clock=lambda: NOW)
    store = kc.BaselineStore(tmp_path / "base.json")
    store.init(D("250"), DAY, D("250"))
    state = kc.ExchangeState((), (), D("250"), {}, source="dry-run-fixture")
    g = kgd.GuardedOrders(client, kill, kc.Caps(), store, log, clock=lambda: NOW, dry_state=state)
    res = g.place(intent())
    assert isinstance(res, kcl.DryRun)
    evs = [(e["event"], e.get("op"), e.get("state_source")) for e in log.read()]
    assert ("order_allowed", None, "dry-run-fixture") in evs
    assert [op for ev, op, _ in evs if ev == "dry_run_request"] == [
        "get_positions", "get_resting_orders", "get_balance", "create_order"]


# ------------------------------------------------------------------ CLI and the arming gate end to end
def test_cli_default_is_dry_run_and_never_touches_transport(tmp_path, capsys):
    def boom(req):
        raise AssertionError("no transport without --live")
    rc = cli.main(["--state-dir", str(tmp_path), "--base-url", tu.FAKE_BASE_URL, "cancel-all"], transport=boom,
                  clock=lambda: NOW)
    assert rc == 0
    ev = kl.EventLog(tmp_path / "events.jsonl").read()[0]
    assert ev["event"] == "dry_run_request" and ev["method"] == "DELETE" and ev["sent"] is False


def test_cli_live_without_arming_exits_2_and_logs_code_only(tmp_path):
    def boom(req):
        raise AssertionError("must not send")
    rc = cli.main(["--live", "--state-dir", str(tmp_path / "s"), "--key-dir", str(tmp_path / "nokeys"), "balance"],
                  transport=boom, clock=lambda: NOW)
    assert rc == 2
    ev = kl.EventLog(tmp_path / "s" / "events.jsonl").read()
    assert ev[-1]["event"] == "live_refused" and ev[-1]["code"] == "no_declaration"


def test_cli_live_armed_sends_through_the_mock_and_log_stays_clean(tmp_path, key, capsys):
    kd, _ = tu.make_key_dir(tmp_path / "kd", key=key)
    decl = tmp_path / "DECLARE-LIVE-1.md"
    decl.write_text("frozen trial declaration\n")
    armed = tmp_path / "LIVE-ARMED"
    armed.write_text(f"DECLARE-LIVE-SHA256 {hashlib.sha256(decl.read_bytes()).hexdigest()}\n")
    os.chmod(armed, 0o644)
    r = Routes()
    rc = cli.main(["--live", "--declaration", str(decl), "--armed", str(armed), "--key-dir", str(kd),
                   "--state-dir", str(tmp_path / "s"), "balance"], transport=r, clock=lambda: NOW)
    assert rc == 0 and r.sent() == [("GET", "/portfolio/balance")]
    assert capsys.readouterr().out.strip() == "250"
    text = (tmp_path / "s" / "events.jsonl").read_text()
    assert tu.FAKE_KEY_ID not in text and r.reqs[0].headers["KALSHI-ACCESS-SIGNATURE"] not in text
    assert '"live_armed"' in text


def test_cli_stop_heartbeat_baseline_and_watchdog_ticks(tmp_path, capsys):
    base = ["--state-dir", str(tmp_path), "--base-url", tu.FAKE_BASE_URL]
    assert cli.main(base + ["init-baseline", "--equity", "250"], clock=lambda: NOW) == 0
    assert kc.BaselineStore(tmp_path / "baseline.json").load().day == DAY
    assert cli.main(base + ["heartbeat"], clock=lambda: NOW) == 0
    assert cli.main(base + ["watchdog", "--max-ticks", "2"], clock=lambda: NOW, sleep=lambda s: None) == 0
    assert cli.main(base + ["stop"], clock=lambda: NOW) == 0 and (tmp_path / "STOP").exists()
    cli.main(base + ["watchdog", "--max-ticks", "1"], clock=lambda: NOW)
    evs = [e["event"] for e in kl.EventLog(tmp_path / "events.jsonl").read()]
    assert "kill_trip" in evs and "dry_run_request" in evs   # dry-run cancel-all logged as the would-be request


def test_cli_place_dry_run_end_to_end(tmp_path, capsys):
    base = ["--state-dir", str(tmp_path), "--base-url", tu.FAKE_BASE_URL]
    cli.main(base + ["init-baseline", "--equity", "250"], clock=lambda: NOW)
    cli.main(base + ["heartbeat"], clock=lambda: NOW)
    rc = cli.main(base + ["place", "--ticker", "A", "--outcome", "no", "--price", "0.37", "--count", "25",
                          "--client-order-id", "lip-9", "--expires-in", "450", "--group", "grp-1"], clock=lambda: NOW)
    assert rc == 0
    evs = kl.EventLog(tmp_path / "events.jsonl").read()
    post = [e for e in evs if e.get("op") == "create_order"][0]
    assert post["body"]["side"] == "ask" and post["body"]["price"] == "0.6300" and post["body"]["post_only"] is True
    assert post["body"]["expiration_time"] == int(NOW) + 450
