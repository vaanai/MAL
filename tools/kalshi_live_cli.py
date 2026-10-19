"""Command line for the Kalshi LIP live core (OFFLINE core).  DRY-RUN unless ``--live`` AND the arming gate
passes; a failed gate exits 2 and never downgrades silently.  No default network transport is used in tests.

    kalshi_live_cli.py [--live --declaration DECLARE-LIVE-1.md] [--armed F] [--key-dir D] --state-dir S CMD

CMD: balance | positions | fills | resting | cancel ORDER_ID | cancel-all | init-baseline --equity X |
     place ... | watchdog [--interval N] [--max-ticks N] | heartbeat | stop
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import kalshi_live_caps as kc
import kalshi_live_client as kcl
import kalshi_live_gate as kg
import kalshi_live_guard as guard
import kalshi_live_kill as kk
import kalshi_live_log as kl
import kalshi_live_orders as ko


def build_parser():
    p = argparse.ArgumentParser(prog="kalshi_live_cli", description=__doc__)
    p.add_argument("--live", action="store_true", help="send for real (needs LIVE-ARMED + key dir); default dry-run")
    p.add_argument("--declaration")
    p.add_argument("--armed")
    p.add_argument("--key-dir", default=str(kg.KEY_DIR_DEFAULT))
    p.add_argument("--base-url", default=kcl.PLACEHOLDER_BASE_URL, help="dry-run only; live reads kalshi.env")
    p.add_argument("--state-dir", required=True)
    sub = p.add_subparsers(dest="cmd", required=True)
    for c in ("balance", "positions", "fills", "resting", "cancel-all", "heartbeat", "stop"):
        sub.add_parser(c)
    sub.add_parser("cancel").add_argument("order_id")
    sub.add_parser("init-baseline").add_argument("--equity", required=True)
    w = sub.add_parser("watchdog")
    w.add_argument("--interval", type=float, default=15.0)
    w.add_argument("--max-ticks", type=int, default=0)
    pl = sub.add_parser("place")
    pl.add_argument("--ticker", required=True)
    pl.add_argument("--outcome", required=True, choices=ko.OUTCOMES)
    pl.add_argument("--price", required=True)
    pl.add_argument("--count", required=True)
    pl.add_argument("--client-order-id", required=True)
    pl.add_argument("--expires-in", type=int, required=True, help="seconds from now (exchange-side dead-man)")
    pl.add_argument("--group", required=True)
    pl.add_argument("--mark", action="append", default=[], help="TICKER=conservative value of one held contract")
    return p


def main(argv=None, transport=None, clock=time.time, sleep=time.sleep) -> int:
    a = build_parser().parse_args(argv)
    sd = Path(a.state_dir)
    sd.mkdir(parents=True, exist_ok=True)
    log = kl.EventLog(sd / "events.jsonl", clock=clock)
    if a.live:
        try:
            armed = kg.arm(True, a.declaration, a.armed, a.key_dir)
            client = kcl.KalshiClient.from_armed(armed, log, transport=transport)
        except Exception as e:  # noqa: BLE001 - any failure to arm is a refusal
            log.emit("live_refused", code=getattr(e, "code", type(e).__name__))
            print(f"refused: {getattr(e, 'code', type(e).__name__)}", file=sys.stderr)
            return 2
        log.emit("live_armed", env=armed.env)
    else:
        client = kcl.KalshiClient(log, a.base_url, transport=transport)
    stop, hb = sd / "STOP", sd / "heartbeat.json"
    kill = kk.KillSwitch(stop, hb, client.cancel_all, log, clock=clock)
    store = kc.BaselineStore(sd / "baseline.json")

    if a.cmd == "balance":
        out = client.get_balance()
    elif a.cmd == "positions":
        out = client.get_positions()
    elif a.cmd == "fills":
        out = client.get_fills()
    elif a.cmd == "resting":
        out = client.get_resting_orders()
    elif a.cmd == "cancel":
        out = client.cancel_order(a.order_id)
    elif a.cmd == "cancel-all":
        out = client.cancel_all()
    elif a.cmd == "heartbeat":
        out = kk.write_heartbeat(hb, clock)
    elif a.cmd == "stop":
        stop.write_text("manual\n")
        log.emit("stop_file_written", reason="manual")
        out = "STOP written"
    elif a.cmd == "init-baseline":
        day = datetime.fromtimestamp(clock(), timezone.utc).strftime("%Y-%m-%d")
        out = store.init(a.equity, day, Decimal(a.equity))
    elif a.cmd == "watchdog":
        n = 0
        while True:
            out = kill.watchdog_tick()
            n += 1
            if a.max_ticks and n >= a.max_ticks:
                break
            sleep(a.interval)
    else:  # place
        marks = {k: Decimal(v) for k, v in (m.split("=", 1) for m in a.mark)}
        g = guard.GuardedOrders(client, kill, kc.Caps(), store, log, clock=clock, marks_fn=lambda ps: marks)
        intent = ko.OrderIntent(a.ticker, a.outcome, Decimal(a.price), Decimal(a.count), a.client_order_id,
                                int(clock()) + a.expires_in, a.group)
        out = g.place(intent)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
