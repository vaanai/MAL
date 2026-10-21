"""arena_pool_table: a host-side, persistent pool table for rules that need more than ArenaState keeps. PAPER only. No key, no network, no model.

WHY. `tools/arena_state.ArenaState` evicts a pool idle for 6 h and keeps 60 min of events and 360 min of candles. A rule that asks "how old is this pool (24-200 h)",
"how much volume in the last 24 h" or "what was the spot 6 h ago" cannot be answered from it. This table is fed by the host (tools/arena_rules_v2.HostV2) with EVERY tape row
before any rule sees it (also rows before the clock and stale rows: the table is history, not a decision), it is independent of the state's eviction, and it is
checkpointed to disk so a restart does not lose it.

WHAT IT HOLDS (L1 FLOOR-OPTION v2, tools/arena_rule_l1.py; semantics copied from /data/mal/hunt-1008/push-1016/hunt24/l4-audit/l1_rule.py)
  * seen[pool]   first print time (event_ts, s) of every WSOL PumpSwap pool, ever (a pool seen once is never "new" again).
  * uni[pool]    the universe base time M (s) of a pool that qualified at its FIRST print, else absent. Two modes:
      "first_print"  M = the pool's first print time; first print V in band and first print >= table t0 + 3 h (l1_rule.py proxy_first_print path, lines 86-88).
      "complete"     M = the mint's `complete` row event_ts; needs a `create` row for the mint, not mayhem, and first print in [M-5, M+600] (l1_rule.py lines 90-91).
    Both: V of the first print in [17.5e9, 17.7e9] and the FIRST pool per mint is the canonical one (l1_rule.py lines 83, 93-96).
  * prints[pool] for universe pools: parallel arrays (event_ts, sol_lamports, post-trade spot, arrival hour index) of the prints in the last 26 arrival hours, the visible
    history of the kit (hist deque maxlen 26). ~24 bytes a print.
  * last_exit[pool]  the ms of the last exit decision (the 60 s re-entry cooldown outlives the position).
  * hwm_ms / hwm_keys  the newest t_recv_ms applied and the rows at that instant, so a row fed twice (checkpoint overlap with the state warm-up) is applied once.

NOT HERE: engine positions. See tools/arena_rules_v2.py (restart section).
"""

from __future__ import annotations

import json
import math
import os
import pickle
import subprocess
from array import array
from pathlib import Path
from typing import Any, Mapping

WSOL = "So11111111111111111111111111111111111111112"
FEE = 0.0125
V_LO, V_HI = 17_500_000_000, 17_700_000_000
HIST_HOURS = 26
HOUR_MS = 3_600_000
AGE_DROP_S = 200 * 3600 + 3600  # past this age a pool can never be a candidate: its prints are freed
VERSION = 1
MODES = ("first_print", "complete")


class PoolPrints:
    """Arrival-ordered prints of one pool (parallel arrays). `lo` is the index of the first print still inside the visible window."""

    __slots__ = ("bt", "sol", "spot", "hidx")

    def __init__(self) -> None:
        self.bt, self.sol, self.spot, self.hidx = array("l"), array("d"), array("d"), array("l")

    def __len__(self) -> int:
        return len(self.bt)

    def add(self, bt: int, sol: float, spot: float, hidx: int) -> None:
        self.bt.append(bt)
        self.sol.append(sol)
        self.spot.append(spot)
        self.hidx.append(hidx)

    def trim(self, min_hidx: int) -> None:
        from bisect import bisect_left

        k = bisect_left(self.hidx, min_hidx)
        if k:
            del self.bt[:k], self.sol[:k], self.spot[:k], self.hidx[:k]


def spot_after(row: Mapping[str, Any]) -> float:
    """l1_rule.py lines 125-127: post-trade spot (q_post + V) / b_post with the kit's 1.25% approximation. NaN when V is missing."""
    sol, tok = float(row["sol_lamports"]), float(row["token_raw"])
    q, b = float(row["quote_reserve"]), float(row["base_reserve"])
    v = row.get("virtual_quote_reserves")
    buy = row["side"] == "buy"
    qp = q + sol * (1 - FEE) if buy else q - sol / (1 - FEE)
    bp = b - tok if buy else b + tok
    if v is None or bp == 0:
        return float("nan")
    return (qp + float(v)) / bp


class PoolTable:
    def __init__(self, mode: str = "first_print", t0: int | None = None) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.mode, self.t0 = mode, t0
        self.hwm_ms = 0
        self.hwm_keys: set[tuple] = set()
        self.seen: dict[str, int] = {}
        self.uni: dict[str, int] = {}
        self.canon: dict[str, str] = {}
        self.mig: dict[str, int] = {}
        self.have: set[str] = set()
        self.mayhem: set[str] = set()
        self.prints: dict[str, PoolPrints] = {}
        self.last_exit: dict[str, int] = {}
        self.rows_applied = 0
        self.skip_before = 0
        self.rate_h: dict[int, list] = {}  # arrival hour -> [min slot, max slot, min bt, max bt, n] over WSOL PumpSwap rows (the kit's per-hour slots_per_s)
        self.live_positions: set[str] = set()  # set by the host before sweep()
        self.marks: dict[str, tuple] = {}  # position_id -> (post-buy mark, net lamports, tokens): the landing state, derived once from the tape

    # ---- reading
    def base(self, pool: str) -> int | None:
        return self.uni.get(pool)

    def pool_prints(self, pool: str) -> PoolPrints | None:
        return self.prints.get(pool)

    def exit_ms(self, pool: str) -> int | None:
        return self.last_exit.get(pool)

    # ---- writing
    def rate(self, hidx: int, default: float = 2.5) -> float:
        """Slots per second of arrival hour `hidx` as the kit computes it ((max slot - min slot) / (max bt - min bt)), running within the hour; the previous hour's when the
        current one has fewer than 1000 rows."""
        for h in (hidx, hidx - 1):
            r = self.rate_h.get(h)
            if r and (r[4] >= 1000 or h != hidx or (hidx - 1) not in self.rate_h) and r[3] > r[2]:
                return (r[1] - r[0]) / (r[3] - r[2])
        return default

    def note_exit(self, pool: str, ms: int) -> None:
        self.last_exit[pool] = int(ms)

    def update(self, row: Mapping[str, Any]) -> bool:
        """Apply one tape row. Returns False for a row already applied (t_recv_ms at or below the high-water mark and seen) or not used."""
        t = row.get("t_recv_ms")
        if isinstance(t, bool) or not isinstance(t, int):
            return False
        key = (row.get("signature"), row.get("type"), row.get("event_index"))
        if self.skip_before:  # only right after a load / rebuild: rows the checkpoint already holds are skipped, once; normal flow never drops a row for being out of order
            if t < self.skip_before or (t == self.skip_before and key in self.hwm_keys):
                return False
            if t > self.skip_before:
                self.skip_before = 0
        if t > self.hwm_ms:
            self.hwm_ms, self.hwm_keys = t, set()
        if t == self.hwm_ms:
            self.hwm_keys.add(key)
        typ = row.get("type")
        if typ == "create":
            m = row.get("mint")
            if m:
                self.have.add(m)
                if row.get("is_mayhem_mode"):
                    self.mayhem.add(m)
            return True
        if typ == "complete":
            m, ts = row.get("mint"), row.get("event_ts")
            if m and isinstance(ts, (int, float)) and not isinstance(ts, bool) and m not in self.mig:
                self.mig[m] = int(ts)
            return True
        if typ != "trade" or (row.get("venue") or row.get("prog")) != "pumpswap":
            return False
        pool = row.get("pool")
        ts = row.get("event_ts")
        if (row.get("quote_mint") != WSOL or not pool or row.get("slot") is None or row.get("side") not in ("buy", "sell") or isinstance(ts, bool)
                or not isinstance(ts, (int, float)) or ts <= 0):
            return False
        if self.t0 is None:
            self.t0 = (t // HOUR_MS) * 3600
        bt = int(ts)
        hidx = t // HOUR_MS
        self.rows_applied += 1
        sl_ = int(row["slot"])
        rh = self.rate_h.get(hidx)
        if rh is None:
            self.rate_h[hidx] = [sl_, sl_, bt, bt, 1]
            for old in [h for h in self.rate_h if h < hidx - 2]:
                del self.rate_h[old]
        else:
            rh[0], rh[1], rh[2], rh[3], rh[4] = min(rh[0], sl_), max(rh[1], sl_), min(rh[2], bt), max(rh[3], bt), rh[4] + 1
        if pool not in self.seen:
            self.seen[pool] = bt
            self._admit(pool, row, bt)
        pp = self.prints.get(pool)
        if pp is not None:
            pp.add(bt, float(row["sol_lamports"]), spot_after(row), hidx)
            pp.trim(hidx - (HIST_HOURS - 1))
            m = self.uni.get(pool)
            if m is not None and bt - m > AGE_DROP_S:
                del self.prints[pool]
        return True

    def _admit(self, pool: str, row: Mapping[str, Any], bt0: int) -> None:
        """Decide the universe once, at the pool's first print (l1_rule.py lines 74-97)."""
        v0 = row.get("virtual_quote_reserves")
        ok = isinstance(v0, (int, float)) and not isinstance(v0, bool) and V_LO <= v0 <= V_HI
        mint = row.get("mint") or ""
        M = None
        if ok:
            if self.mode == "first_print":
                ok = bt0 >= (self.t0 or 0) + 3 * 3600
                M = bt0
            else:
                M = self.mig.get(mint)
                ok = M is not None and mint in self.have and mint not in self.mayhem and (M - 5 <= bt0 <= M + 600)
        if ok:
            if mint in self.canon and self.canon[mint] != pool:
                ok = False
            else:
                self.canon[mint] = pool
        if ok:
            self.uni[pool] = int(M)
            self.prints[pool] = PoolPrints()

    def sweep(self, now_ms: int) -> int:
        """Free prints older than the visible window and of pools past the age limit; forget old exit stamps. Returns pools whose arrays were dropped."""
        hid = now_ms // HOUR_MS
        n = 0
        for pool in list(self.prints):
            pp = self.prints[pool]
            pp.trim(hid - (HIST_HOURS - 1))
            if len(pp) == 0 and now_ms // 1000 - self.uni[pool] > AGE_DROP_S:
                del self.prints[pool]
                n += 1
        for pid in [k for k in self.marks if k not in self.live_positions]:
            del self.marks[pid]
        for pool in [p for p, ms in self.last_exit.items() if now_ms - ms > 3_600_000]:
            del self.last_exit[pool]
        return n

    # ---- persistence
    def to_dict(self) -> dict[str, Any]:
        return {"version": VERSION, "mode": self.mode, "t0": self.t0, "hwm_ms": self.hwm_ms, "hwm_keys": sorted(map(tuple, self.hwm_keys), key=repr), "seen": self.seen,
                "uni": self.uni, "canon": self.canon, "mig": self.mig, "have": self.have, "mayhem": self.mayhem, "last_exit": self.last_exit, "rate_h": self.rate_h, "marks": self.marks,
                "prints": {p: (pp.bt, pp.sol, pp.spot, pp.hidx) for p, pp in self.prints.items()}, "rows_applied": self.rows_applied}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "PoolTable":
        if d.get("version") != VERSION:
            raise ValueError("pool table checkpoint: unknown version")
        t = cls(d["mode"], d["t0"])
        t.hwm_ms, t.hwm_keys = int(d["hwm_ms"]), {tuple(k) for k in d["hwm_keys"]}
        t.seen, t.uni, t.canon, t.mig = dict(d["seen"]), dict(d["uni"]), dict(d["canon"]), dict(d["mig"])
        t.have, t.mayhem, t.last_exit, t.rows_applied = set(d["have"]), set(d["mayhem"]), dict(d["last_exit"]), int(d["rows_applied"])
        t.rate_h, t.marks = {int(k): list(v) for k, v in d["rate_h"].items()}, {k: tuple(v) for k, v in d["marks"].items()}
        t.skip_before = t.hwm_ms
        for p, (bt, sol, spot, hidx) in d["prints"].items():
            pp = PoolPrints()
            pp.bt, pp.sol, pp.spot, pp.hidx = array("l", bt), array("d", sol), array("d", spot), array("l", hidx)
            t.prints[p] = pp
        return t

    def checkpoint(self, path: str | os.PathLike) -> None:
        """Atomic: temp file, fsync, rename, fsync the directory. A pickle of our own file (never read from an untrusted path)."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        with open(tmp, "wb") as fh:
            pickle.dump(self.to_dict(), fh, protocol=4)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
        fd = os.open(p.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @classmethod
    def load(cls, path: str | os.PathLike) -> "PoolTable":
        with open(path, "rb") as fh:
            return cls.from_dict(pickle.load(fh))

    # ---- rebuild from the tape
    def rebuild(self, tape_dir: str | os.PathLike, to_ms: int) -> int:
        """Apply every tape row with hwm_ms <= t_recv_ms < to_ms from the closed/open hour files of `tape_dir` (oldest first). Used when there is no checkpoint, or the
        checkpoint is older than the state warm-up. Rows already applied are skipped by update(). Returns rows applied."""
        n0 = self.rows_applied
        self.skip_before = self.hwm_ms
        files = []
        for f in Path(tape_dir).iterdir():
            nm = f.name
            if nm.startswith("tape-") and (nm.endswith(".jsonl") or nm.endswith(".jsonl.zst")):
                files.append((nm.split(".")[0], f))
        seen_h = set()
        for h, f in sorted(files, key=lambda x: (x[0], x[1].name.endswith(".zst"))):
            if h in seen_h:
                continue  # a .jsonl still exists beside its .zst: the .jsonl sorts first and is the same bytes
            seen_h.add(h)
            from datetime import datetime, timezone

            hs = int(datetime.strptime(h[5:], "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)
            if hs + HOUR_MS <= self.hwm_ms or hs >= to_ms:
                continue
            for line in _lines(f):
                if b'"trade"' not in line and b'"create"' not in line and b'"complete"' not in line:
                    continue
                if b"pumpfun" in line and b'"trade"' in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                t = row.get("t_recv_ms")
                if isinstance(t, int) and t < to_ms:
                    self.update(row)
        self.skip_before = self.hwm_ms  # the host's warm-up replay feeds these rows again
        return self.rows_applied - n0


def _lines(path: Path):
    if path.name.endswith(".zst"):
        z = subprocess.Popen(["zstd", "-dc", "-q", str(path)], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
        assert z.stdout is not None
        try:
            yield from z.stdout
        finally:
            z.stdout.close()
            if z.wait() != 0:
                raise RuntimeError(f"zstd failed on {path}")
    else:
        with open(path, "rb") as fh:
            yield from fh


def finite(x: float) -> bool:
    return math.isfinite(x)
