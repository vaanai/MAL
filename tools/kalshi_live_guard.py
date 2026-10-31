"""Guarded order path for the Kalshi LIP live lane (OFFLINE core).

``GuardedOrders.place`` is the only path from an :class:`OrderIntent` to ``client.create_order``:

    1. kill switch (STOP file, heartbeat): cancel-all and refuse if tripped;
    2. read exchange state (positions, resting orders, balance); in dry-run the reads are only logged and a
       caller-supplied ``dry_state`` stands in, labelled as such in the log;
    3. pre-trade caps (``kalshi_live_caps``); a refusal is logged, a loss-stop refusal also cancels all, and the
       trial loss stop latches by writing the STOP file;
    4. only then ``client.create_order``.

Not to be merged before a DEC amendment approved by the owner allows a Kalshi key on a host.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal

import kalshi_live_caps as kc
import kalshi_live_client as kcl


class GuardedOrders:
    def __init__(self, client, kill, caps: kc.Caps, baselines: kc.BaselineStore, log, clock=time.time,
                 marks_fn=None, dry_state: kc.ExchangeState | None = None):
        self.client, self.kill, self.caps, self.baselines = client, kill, caps, baselines
        self.log, self.clock = log, clock
        self.marks_fn = marks_fn or (lambda positions: {})
        self.dry_state = dry_state

    def today(self) -> str:
        return datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y-%m-%d")

    def read_state(self) -> kc.ExchangeState:
        positions = self.client.get_positions()
        resting = self.client.get_resting_orders()
        balance = self.client.get_balance()
        if any(isinstance(x, kcl.DryRun) for x in (positions, resting, balance)):
            if self.dry_state is not None:
                return self.dry_state
            b = self.baselines.load()  # nothing was read: assume flat, cash at the day baseline
            return kc.ExchangeState((), (), b.day_start_equity if b else Decimal(0), {},
                                    source="dry-run-assumed-flat-at-baseline")
        return kc.ExchangeState(tuple(positions), tuple(resting), balance, dict(self.marks_fn(positions)))

    def place(self, intent):
        v = self.kill.check()
        if not v.allowed:
            return kc.Refusal(v.reason, {"kill": True})
        state = self.read_state()
        eq = kc.equity(state)
        baseline = self.baselines.roll(self.today(), eq) if eq is not None else self.baselines.load()
        r = kc.check_order(intent, state, self.caps, baseline)
        if r is not None:
            self.log.emit("refusal", code=r.code, detail=r.detail, ticker=intent.ticker,
                          client_order_id=intent.client_order_id, state_source=state.source)
            if r.is_loss_stop:
                self.client.cancel_all()
                if r.code == "trial_loss_stop":
                    self.kill.stop_path.write_text("trial_loss_stop\n")
                    self.log.emit("stop_file_written", reason=r.code)
            return r
        self.log.emit("order_allowed", ticker=intent.ticker, client_order_id=intent.client_order_id,
                      state_source=state.source)
        return self.client.create_order(intent)
