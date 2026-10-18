"""hv4_review_replay: the skip pricer of the HV4 nightly reviewer (DESIGN section 4: "up to 60 skipped cards, each with the v0 default plan's outcome replayed in
arena_paper, if that exit finished before 00:00Z"). PAPER only. No network, no key.

What it does. For skipped cards read from the runner's card store, it replays the arena tape through a SCRATCH copy of the machinery the live runner uses:
a scratch ArenaState, a scratch ArenaEngine (own temp ledger and temp registry, never /data/mal/arena), and a scratch hv4_runner.Runner in sim mode
whose X arm (CON-X or BIG-X: v0 default exit, no LLM) opens each card at its as_of_ms and exits it with the runner's own code exits (stop, target, trail,
plan time, max hold; `Runner.exits`). The trip is priced by the engine at the arena's deciding stack, and the 0.25 SOL stake on the flat 15% leg at 1.9 s
under the strand rule "total" is read back from the scratch ledger (the same key path as hv4_review.load_trade_records). The pressure leg is never read.

Cutoff. A tick at T may decide an exit only while T + EXIT_FILL_MS < cutoff_ms, so a replay whose exit fill would land at or after the cutoff is not an input
(None). After the last such tick the tape is read for a short settle window (SETTLE_MS) only so that exits already decided can be priced from the prints that
follow their landing slot; no exit is decided from it. A position not exited by then is None (unfinished at the cutoff).

Conventions. The decision instant of a replayed buy is the card's as_of_ms with no LLM latency (as the X arms do); the engine charges its own fill latency.
The replay starts reading the tape LOOKBACK_MS before the first card so the engine's pool history is warm. Everything here is deterministic in (tape,
cards, d).

This module imports the arena stack (tools.hv4_runner, tools.arena_paper, tools.arena_state). tools.hv4_review does not import it at import time.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tools import hv4_arms as arms
from tools import hv4_review as RV

LOOKBACK_MS = 6 * 3_600_000
SETTLE_MS = 3 * 60_000
TICK_MS = 1_000
STAKE = RV.STAKE_SOL


class _NoChain:
    """The scratch runner never builds a card (the cards come from the store), so it never asks the chain anything."""

    def chain_part(self, pool_id, mint, now_ms, slot):
        return None

    def mint_gate(self, mint, now_ms, slot):
        return None

    def credit_report(self):
        return {"calls": 0}


class ArenaReplayPricer:
    """hv4_review.SkipPricer over an arena tape directory. `feed_delay_slots` is the stack's d (the manager pins it, as for the runner)."""

    def __init__(self, tape_dir: str | Path, feed_delay_slots: int, *, sps_fn: Callable[[], float | None] | None = None, lookback_ms: int = LOOKBACK_MS) -> None:
        self.tape_dir = Path(tape_dir)
        self.d = int(feed_delay_slots)
        self.sps_fn = sps_fn
        self.lookback_ms = int(lookback_ms)
        self.name = f"arena_replay_d{self.d}"

    def price_many(self, items: Sequence[tuple[str, Mapping[str, Any]]], persona: str, cutoff_ms: int) -> dict[str, dict | None]:
        if persona not in ("CON", "BIG"):
            raise RV.ReviewError("skip_pricing_is_for_lane_g_personas")
        out: dict[str, dict | None] = {k: None for k, _ in items}
        pend = sorted(((int(c["as_of_ms"]), k, dict(c)) for k, c in items), key=lambda x: (x[0], x[1]))
        if not pend:
            return out
        from tools import hv4_runner as R
        from tools.arena_paper import ArenaEngine
        from tools.arena_state import ArenaState
        from tools.hv4_model import StubModel

        arm = f"{persona}-X"
        bot = R.bot_id(arm)
        last_decision = cutoff_ms - RV.EXIT_FILL_MS
        with tempfile.TemporaryDirectory(prefix="hv4-skip-replay-") as tmp:
            t = Path(tmp)
            (t / "bots.json").write_text(json.dumps({"kind": "register", "bot_id": bot, "frozen_sha": "0" * 40, "verified_sha": None, "clock_start_ms": 0,
                                                     "registered_at_ms": 0, "deciding_stake": STAKE, "model_ids": [], "time_source": "scratch_replay"}) + "\n")
            state = ArenaState(self.tape_dir, start_ms=pend[0][0] - self.lookback_ms)
            engine = ArenaEngine(t / "ledger", t / "bots.json", cfg=R.stack_config(feed_delay_slots=self.d), sps_fn=self.sps_fn)
            run = R.Runner(R.RunnerConfig(root=str(t / "hv4"), frozen_sha="0" * 40, clock_start_ms=0, live=False), state, engine, StubModel(), _NoChain())
            pid_of: dict[str, str] = {}
            T = pend[0][0]
            while T < last_decision:
                run.sim_now = max(run.sim_now, T)
                run.poll_to(T)
                while pend and pend[0][0] <= T:
                    _ms, key, card = pend.pop(0)
                    pid = run._open(arm, card, STAKE, None, T, {}, {})
                    if pid is not None:
                        pid_of[key] = pid
                run.exits(T)
                if not pend and not run.books[arm].open:
                    break
                T += TICK_MS
            run.poll_to(T + SETTLE_MS)  # price the exits already decided; no exit is decided from these prints
            engine.flush()
            rows = {}
            for f in sorted((t / "ledger" / bot).glob("trades-*.jsonl")) if (t / "ledger" / bot).exists() else []:
                for r in RV._jsonl(f):
                    if r.get("kind") == "trade" and abs(float(r.get("stake_sol", 0)) - STAKE) < 1e-9:
                        rows[r["position_id"]] = r
        for key, pid in pid_of.items():
            r = rows.get(pid)
            ms = (r or {}).get("decision_ms") or {}
            if r is None or not isinstance(ms.get("entry"), int) or not isinstance(ms.get("exit"), int):
                continue  # unfinished, excluded (gap, restart) or unpriced: not an input
            close = ms["exit"] + RV.EXIT_FILL_MS
            if close >= cutoff_ms:
                continue
            net = r["legs"][RV.FLAT_RULE][RV.FLAT_LEG]["net_lamports"]
            out[key] = {"close_ms": close, "exit_reason": str(r.get("exit_reason") or ""), "hold_s": int(round((ms["exit"] - ms["entry"]) / 1000.0)),
                        "net_pct": round(100.0 * float(net) / (STAKE * 1e9), 4)}
        return out
