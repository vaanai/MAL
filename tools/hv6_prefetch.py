"""hv6_prefetch: HV6's chain-fact prefetcher (fix C of smoke #1806, 2026-10-28). PAPER only; every RPC still goes through tools.hv4_chain.HV4Chain (the same
allow-list, 2 rps token bucket, daily call cap and credit counter). tools/hv4_prefetch.py is imported and subclassed, never edited (its blob pin is kept).

Why. Smoke #1806 refused 96 of 285 cards on chain facts (chain_stale 69, chain_unavailable 27). The policy-v2 prefetcher runs free: it spends as fast as demand
asks (about 1,440 calls/h measured) until the rolling hour reaches its cap (987 of HV6's 1,000/h), then makes NO call until the oldest minutes roll off. Twice in
the 2 h smoke that blackout lasted about 15 minutes; mint facts aged past the card's 15 min limit and the board scans inside it were refused (chain_stale), and
pools first seen in it got nothing (chain_unavailable). About 8 more chain_unavailable came in the first 10 minutes (the store was empty: the prefetch started with
the run) and about 7 at steady state (a pool tagged at the very scan that first saw it pass the filters has no fact stamped before that scan).

What this subclass changes (and nothing else; the order of service, the class reserves and part_at are HV4 policy v2's):
  1. budget: the runner passes 2,000 calls/h (HV4 v2's own figure, HOURLY_BUDGET_V2; was 1,000), owner note 2026-10-28 ("extra Helius credits for HV6").
  2. pacing: a token bucket over the plan cap. It holds at most PACE_BURST calls and refills at (hard - PACE_BURST) / 60 a minute, so no rolling hour can reach the
     base class's hard cap (hourly_budget - SAFETY_MARGIN = 1,980): any 60 minutes spend at most PACE_BURST + 60 x refill = hard. When demand is above the refill
     rate the passes keep serving in v2 priority order every few seconds (new and hot mint reads first) instead of spending the hour in a burst and then stopping.
     A refresh is skipped, not failed, when the bucket cannot pay its estimate; the rest of that pass waits too (strict priority), and the next pass retries.
  3. admit(): the runner hands over pools that pass the filters at the lookahead seconds between scans (and before the clock). They join the store at once (a new
     pool's mint read is class 0, the first served) and the worker is woken, so their facts are stamped before the next :02 scan. Nothing else of the store moves
     (hot, warm, the latest watch instant): the scan's own watch() stays the one source of those.
The Helius rate does not change: the RpcClient's bucket is 2 rps (hv4_chain.DEFAULT_RPS), far under the 50 rps plan, outside the walkers' four 10-rps flock slots.
"""

from __future__ import annotations

import threading
from typing import Any, Iterable

from tools import hv4_prefetch as PF

HOURLY_BUDGET = PF.HOURLY_BUDGET_V2       # 2,000 calls in any rolling hour (= 48,000 a day = hv4_chain.DEFAULT_DAILY_CALL_CAP)
PACE_BURST = 360                          # calls the bucket can hold: a cold store of about 120 pools (mint + Mayhem) plus the first hot holders reads


def hard_cap(hourly_budget: int) -> int:
    """The base class's plan stop in the rolling hour (as _run_once_v2 computes it)."""
    return hourly_budget - int(PF.SAFETY_MARGIN * min(1.0, hourly_budget / PF.HOURLY_BUDGET))


class PacedPrefetcher(PF.ChainPrefetcher):
    """HV4's ChainPrefetcher, policy v2, with a call bucket in front of every refresh and an admit() for pools seen between scans."""

    def __init__(self, *args: Any, pace_burst: int | None = None, **kw: Any) -> None:
        super().__init__(*args, **kw)
        if self.policy != PF.POLICY_V2:
            raise ValueError("HV6 paces policy v2 only")
        hard = hard_cap(self.hourly_budget)
        burst = PACE_BURST if pace_burst is None else int(pace_burst)
        if not 0 < burst < hard:
            raise ValueError("pace_burst must be between 0 and the hourly plan stop")
        self.pace_burst = burst
        self.pace_per_min = (hard - burst) / 60.0      # 27.0 at 2,000/h
        now = self.clock()
        # after a restart the rolling hour may already hold calls (spend file, counter's prior minutes): never start with more than the hour has left
        self._tokens = float(min(burst, max(0, hard - self._hour_calls(now))))
        self._tok_ms = now
        self._pass_blocked = False
        self._wake = threading.Event()

    # -- the bucket
    def _refill(self, now: int) -> float:
        """Called with the lock held."""
        if now > self._tok_ms:
            self._tokens = min(float(self.pace_burst), self._tokens + self.pace_per_min * (now - self._tok_ms) / PF.MIN_MS)
            self._tok_ms = now
        return self._tokens

    def tokens(self, now_ms: int | None = None) -> float:
        with self._lock:
            return self._refill(self.clock() if now_ms is None else now_ms)

    def run_once(self, now_ms: int | None = None) -> int:
        with self._lock:
            self._pass_blocked = False
        return super().run_once(now_ms)

    def _refresh(self, p: Any, now: int, need_mint: bool, need_hold: bool) -> None:
        est = ((PF.MINT_EST + (0 if p.mayhems else 1)) if need_mint else 0) + (PF.HOLDERS_EST if need_hold else 0)
        with self._lock:
            have = self._refill(max(now, self.clock()))
            if self._pass_blocked or have < est:
                self._pass_blocked = True                     # strict priority: nothing later in this pass jumps the queue
                self._n("paced_deferred")
                self._n("paced_deferred_" + ("mint" if need_mint else "hold"))
                return
        c0 = self._calls_total()
        try:
            super()._refresh(p, now, need_mint, need_hold)
        finally:
            c1 = self._calls_total()
            used = max(0, c1 - c0) if (c0 is not None and c1 is not None) else est
            with self._lock:
                self._tokens -= used                         # what was really sent (retries included); a small overrun is a short debt
                self._n("paced_calls", used)

    def _book(self, now: int, used: int, mint: bool = False, hot: bool = False) -> None:
        if int(used) <= 0:
            return  # a paced skip made no call: no zero bucket, no spend-file rewrite (the base would fsync one per skipped item)
        super()._book(now, used, mint=mint, hot=hot)

    # -- pools seen between scans
    def admit(self, items: Iterable[tuple[str, str | None]], now_ms: int) -> int:
        """Pools passing the filters at a lookahead instant that are not in the store: add them (first seen now) and wake the worker. Returns how many."""
        added = 0
        with self._lock:
            for pool, mint in items:
                if not mint or pool in self.pools:
                    continue
                self.pools[pool] = PF._Pool(pool, mint, now_ms, now_ms)
                self._dropped.pop(pool, None)
                added += 1
            if added:
                self._n("pools_admitted", added)
        if added:
            self._wake.set()
        return added

    # -- thread (as the base loop, plus the wake event)
    def start(self) -> None:
        if self._thread is not None:
            return

        def loop() -> None:
            while not self._stop.is_set():
                self._wake.clear()
                try:
                    self.run_once()
                except Exception:  # noqa: BLE001 - a refresh failure must never kill the thread; the next pass retries
                    self._n("pass_errors")
                self._wake.wait(PF.CYCLE_S)

        self._thread = threading.Thread(target=loop, name="hv6-chain-prefetch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def stats(self) -> dict[str, Any]:
        out = super().stats()
        out.update({"pace_burst": self.pace_burst, "pace_per_min": self.pace_per_min, "tokens": round(self.tokens(), 3)})
        return out
