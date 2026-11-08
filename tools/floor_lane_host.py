#!/usr/bin/env python3
"""floor_lane_host: the standalone, KEYLESS decision host of the FLOOR live-learning lane ("floor-lane-1", DEC-035). PAPER decisions only: no wallet, no key, no send.

It runs the FROZEN rule `l1-floor-v2` (tools/arena_rule_l1.py, FLOOR frozen sha 5e12f8f5, DEC-031 Am.9) on the live arena feed and appends `entry` / `exit` rows to a LANE-OWNED
decision root, in the format tools/floor_arena_executor.FloorBridge reads (the arena rule host's own hash-chained DecisionLog: `<root>/decisions/l1-floor-v2/<day>.jsonl`, plus a
`start` row whose `frozen_sha` is the full FROZEN_SHA, which the bridge pins). It is NOT the registered FLOOR G1 arena book: it has its own root, ledger, registry and clock, never
reads or writes /data/mal/arena/{bots.json,ledger,rules}, and nothing it produces is a read of G1. Nothing here says the rule makes money.

WHAT IS NEW AND WHAT IS NOT
  * The decision machinery is tools/arena_rules_v2.HostV2 (rule host + pool table + paper ArenaEngine on a lane ledger), subclassed here, not edited. arena_rule_l1.py is not edited.
    Entry/exit rows are byte-for-byte what HostV2 writes for the same rule and tape (tools/test_floor_lane_host.py: md5 of the decision files equal on a fixture).
  * PINS. The decision-relevant files must match the sha256 pinned below at start; a mismatch refuses to start (HostError). The files marked "5e12f8f5" are byte-identical to that
    commit's blobs (the test checks it against git when the commit is present). arena_rules_v2.py and arena_stack_v2.py were changed after 5e12f8f5 only by the DEC-031 Am.8
    `--stack-version` plumbing (git diff 5e12f8f5..main, 12 + 102 added lines); they are pinned at their current hashes and the real-block replay owed by the PR covers them.
    Because the lane host is a NEW file at a later commit, the host's `binding` is {"git_sha": FROZEN_SHA, "dirty": False, "pins": {...}, "host_head": ..., "host_dirty": ...}: git_sha
    states that the pinned files equal the frozen content (checked by sha256), NOT that HEAD is 5e12f8f5. host_head / host_dirty record the checkout that ran.
  * GAPS (new, fail closed). The rule has no gap logic. This host wraps it (GapGuardRule): when the rule would Buy and a data-loss feed gap on the pumpswap feed (or on 'all': process
    restart, disk reserve), or a host-down span of this host, overlaps [trigger t_recv_ms - gap_lookback_s, trigger t_recv_ms], the Buy is DROPPED and a `entry_refused_gap` row is
    logged (rule sha, pool, mint, trigger slot / ms, reason, the span). The lookback default is 6 h = the rule's own dependence window (previous print within 6 h, spot ratio against
    the print 6 h back); it can be raised to 24 h (the 24 h volume) and never lowered below 6 h. The bridge ignores unknown row kinds, so the refusal is evidence, not an order.
    Exits are not gated: a take-profit missed in a gap is not guessed; the executor's own 24 h deadline clock sells.
  * SUBSET. Every lane entry is an entry of the paper rule at the same trigger; the guard only drops Buys. A dropped Buy on pool P (gap, or a restart that lost the paper view) blocks P
    until trigger + BLOCK_MS = 24 h + 1.9 s + 60 s cooldown + 1 s tick + 5 s margin (extended by every later refused Buy on P), persisted in the checkpoint; a restart blocks every pool the log shows open; a host_down span refuses all pools
    for 24 h + 1.9 s. A missing checkpoint beside an existing decision log refuses to start. `run` refuses a dirty or unknown checkout and records this file's sha256 in the start row.
  * TABLE SEED. `run` on a first start requires --since-hour (the table is built from tape hours at or after it, via symlinks; nothing older is opened). `replay --seed-from-hour`
    seeds the table the same way and must be at least 201 h before --from-ms (FLOOR ages run 24 to 200 h from the pool's base time).
  * RESTART. HostV2's pool table checkpoint + the decision log resume the book; ArenaState itself is rebuilt by the live warm-up replay of the last `warm_min` minutes of feed (a few
    seconds), with dispatch off. This host adds `<root>/lane/checkpoint.json` (atomic): last tick instant, the log head, the pins and the gap spans seen (so a gap older than the
    warm-up stays known for lookback). On start the downtime [checkpoint tick, now] is itself recorded as a `host_down` span. Open positions CANNOT resume as normal engine positions
    (HostV2 / ArenaEngine.recover marks them restarted and sells them at the first tick as `resume_reopen`): the executor does not follow that reason (floor_arena_executor item 5).
    Plan: no planned restart while a position is open.

Modes (CLI): register | run | replay | verify. `run` is live (tails the feed dir named in the config). `replay` steps a closed feed slice with the sim clock and refuses outputs inside
/data/mal/arena. `verify` walks the decision chain and prints the head. No key of any kind is read.
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python tools/floor_lane_host.py`
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tools import arena_rules as R
from tools.arena_declog import GENESIS, DecisionLog, LogChainError
from tools.arena_paper import ArenaEngine, canon
from tools.arena_pool_table import PoolTable
from tools.arena_rule_l1 import BOT_ID, COOLDOWN_MS as L_COOLDOWN_MS, HOLD_MS as L_HOLD_MS  # L_HOLD_MS: 24 h + 1.9 s, the paper position's longest life
from tools.arena_rules import Buy, Ctx, Rule
from tools.arena_rules_v2 import HostV2, load_rules_v2, rebuild_table
from tools.arena_stack_v2 import stack_config_for
from tools.arena_state import WARM_MIN_DEFAULT, ArenaState

REPO = Path(__file__).resolve().parent.parent
FROZEN_SHA = "5e12f8f597571d6d5191a7ba38125c67ad723b7d"  # FLOOR's frozen sha (DEC-031 Am.9); equals tools/floor_arena_executor.FROZEN_SHA, which the bridge pins on the start row
CKPT_SCHEMA = "floor_lane_host_ckpt_v1"
CKPT_EVERY_MS = 60_000  # tick time
LOOKBACK_MIN_S = 6 * 3600  # the rule's own dependence window (l1_rule.py:133 previous print within 6 h; :139-143 spot vs the print 6 h back)
LOOKBACK_MAX_S = 24 * 3600  # the 24 h volume window; ArenaState keeps gap spans for 24 h
GAP_PROG = "pumpswap"
HOUR_MS_ = 3_600_000
SEED_MIN_H = 201  # FLOOR ages run 24 to 200 h from the pool's base time; the table needs the tape back to base time
DECISION_MARGIN_MS = 5_000  # decision-delay margin
BLOCK_MS = L_HOLD_MS + L_COOLDOWN_MS + 1_000 + DECISION_MARGIN_MS  # paper hold + 60 s re-entry cooldown + one tick + margin: the earliest a paper re-entry on the pool can follow
KIND_GAP_REFUSED = "entry_refused_gap"

# sha256 of the decision-relevant files. IDENTICAL_AT_FROZEN: byte-equal to the blob at FROZEN_SHA. The rest: pinned at the lane host's baseline (see module docstring).
PINS: dict[str, str] = {
    "tools/arena_rule_l1.py": "de6e371e9479f28dbb610895e2cffb7a6704a762d3f02d3a9c49a08e3019500c",
    "tools/arena_pool_table.py": "78955dacb9f8c041cace52119ddea6758570b3b9425b37ee68b0c7c6487c7692",
    "tools/arena_rules.py": "048dd78901c52e0c14fe9b1611f354c49ac6d0bb37fad8e5be3e27c756e3e2af",
    "tools/arena_state.py": "8af345618e51ca480ee7538717aa001b4ad7ed5ef38941faaec812bbe217f639",
    "tools/arena_paper.py": "9d2fdb04a3da9bb38de9e30c91154f5a9ab2de513e6f4d7936c1eb1a54863514",
    "tools/arena_stack.py": "a39b2ba68dd46b7d3a555fb8e06580718300f354020c1f91280ab7a0e0856778",
    "tools/arena_rules_v2.py": "2cdba9151d33127913b8a3bd454cc81e10f149f5913c8440c7ee51186611d840",
    "tools/arena_stack_v2.py": "95876c94d7e029e3959d8a3aed05ee64f1d461bc002ec62d035e74763587acb3",
    "tools/arena_declog.py": "b4b67f8d203722a16b2d5238f4a0f2b38776995130cd4b5691ee649c043a0783",
}
IDENTICAL_AT_FROZEN = ("tools/arena_rule_l1.py", "tools/arena_pool_table.py", "tools/arena_rules.py", "tools/arena_state.py", "tools/arena_paper.py", "tools/arena_stack.py")


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def verify_pins(repo: Path = REPO, pins: Mapping[str, str] = PINS) -> dict[str, str]:
    """The sha256 of each pinned file, or R.HostError naming the first that differs (the host does not start)."""
    out: dict[str, str] = {}
    for rel, want in pins.items():
        try:
            got = _sha256_file(Path(repo) / rel)
        except OSError as e:
            raise R.HostError(f"pinned file unreadable: {rel} ({type(e).__name__})") from e
        if got != want:
            raise R.HostError(f"pin mismatch: {rel} sha256 {got[:12]} != pinned {want[:12]}")
        out[rel] = got
    return out


def lane_binding(pins: Mapping[str, str], repo: Path = REPO, hb: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The start row's binding for `run`. Refuses unless the checkout is genuinely clean (git says dirty False; unknown counts as dirty). `git_sha` states that the pinned files equal
    the frozen content (verify_pins), NOT that HEAD is the frozen commit; the lane host is a later file, so its own sha256 and HEAD are recorded instead."""
    hb = dict(hb) if hb is not None else dict(R.head_binding(repo))
    if hb.get("dirty") is not False or not hb.get("git_sha"):
        raise R.HostError("the checkout is dirty or its state is unknown: run mode needs a clean checkout")
    return {"git_sha": FROZEN_SHA, "dirty": False, "pins": dict(pins), "host_head": hb["git_sha"], "host_dirty": False, "host_sha256": _sha256_file(Path(__file__).resolve()),
            "kind": "floor_lane_pinned_files"}


def parse_hour(s: str) -> int:
    """'2026-11-01T00' -> epoch ms (UTC)."""
    try:
        return int(datetime.strptime(s, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except (ValueError, TypeError) as e:
        raise R.HostError(f"hour must look like 2026-11-01T00 (got {s!r})") from e


def rebuild_table_since(table: PoolTable, tape_dir: str, to_ms: int, since_ms: int) -> int:
    """PoolTable.rebuild (not edited) reads every hour file of a directory. Give it a temp directory of symlinks to only the hour files in [since hour, to_ms), so a start
    reads nothing older than the floor hour. The links are removed afterwards; nothing is written to the feed dir."""
    hours = []
    for f in Path(tape_dir).iterdir():
        nm = f.name
        if nm.startswith("tape-") and (nm.endswith(".jsonl") or nm.endswith(".jsonl.zst")):
            try:
                hs = parse_hour(nm.split(".")[0][5:])
            except R.HostError:
                continue
            if since_ms <= hs < to_ms:
                hours.append(f)
    with tempfile.TemporaryDirectory(prefix="floor-lane-seed-") as tmp:
        for f in hours:
            os.symlink(f, Path(tmp) / f.name)
        return table.rebuild(tmp, to_ms)


def _atomic_write(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, canon(obj).encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


class GapGuardRule(Rule):
    """The frozen rule, unedited, behind the lane guard. Everything but the Buy it returns is passed through unchanged. `refuse(ctx, row, buy)` returns True to drop that Buy
    (it has logged why); a Buy without a trigger instant is dropped, since no window can be tested."""

    lookback_ms = LOOKBACK_MIN_S * 1000

    def __init__(self, inner: Rule, refuse: Callable[[Ctx, Mapping[str, Any], Buy], bool]) -> None:
        self.inner = inner
        self.bot_id, self.deciding_stake, self.model_ids = inner.bot_id, inner.deciding_stake, inner.model_ids
        self.uses_ticks, self.universe = inner.uses_ticks, inner.universe
        self.table_mode = getattr(inner, "table_mode", None)
        self._refuse = refuse

    def on_row(self, ctx: Ctx, row: Mapping[str, Any]) -> list:
        orders = self.inner.on_row(ctx, row)
        if not any(isinstance(o, Buy) for o in orders):
            return orders
        t = row.get("t_recv_ms")
        if not isinstance(t, int) or isinstance(t, bool):
            return [o for o in orders if not isinstance(o, Buy)]
        return [o for o in orders if not (isinstance(o, Buy) and self._refuse(ctx, row, o))]

    def on_tick(self, ctx: Ctx, T: int) -> list:
        return self.inner.on_tick(ctx, T)


class LaneHost(HostV2):
    """HostV2 + lane checkpoint, host-down spans, per-pool blocks and refusal rows.

    SUBSET RULE. Every lane entry is an entry of the paper rule at the same trigger (the guard only drops Buys). A dropped Buy on pool P (a gap, or a restart that lost the
    paper host's view) would have left the paper book holding P for up to HOLD_MS, during which the paper rule makes no entry on P. So P is BLOCKED until trigger + HOLD_MS
    (the paper position's longest life; a take-profit may free it earlier, and then the lane skips an entry the paper rule would take: a subset, never a superset). Blocks are
    persisted in the checkpoint. A restart blocks every pool the log shows open (the engine restart sells them as resume_reopen and the book forgets them). Downtime is
    a pool-agnostic uncertainty: a paper entry on ANY pool during it is unseen, so entries are refused for HOLD_MS after a host_down span."""

    def __init__(self, *a: Any, lane_root: str | os.PathLike, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.ckpt_path = Path(lane_root) / "lane" / "checkpoint.json"
        self.lane_gaps: list[dict[str, Any]] = []
        self.blocked: dict[str, dict[str, Any]] = {}
        self.gap_refusals = 0
        self._last_lane_ckpt = 0
        self._last_T = 0

    # -- gap knowledge: the ArenaState's spans (24 h) + persisted ones + host-down spans
    def spans(self, t0: int, t1: int) -> list[dict]:
        out = [dict(s) for s in self.state.gap_spans(t0, t1, GAP_PROG)]
        out += [dict(s) for s in self.lane_gaps if s["to_ms"] >= t0 and s["from_ms"] <= t1]
        return sorted(out, key=lambda s: (s["from_ms"], s["to_ms"], str(s.get("kind"))))

    def judge(self, t: int, pool: str) -> tuple[str, list[dict], dict | None] | None:
        """(reason, gap spans, block) when the lane must not enter pool `pool` on a trigger at `t`, else None."""
        blk = self.blocked.get(pool)
        if blk and t < int(blk["until_ms"]):
            return f"pool_blocked_after_{blk['why']}", [], blk
        hit = [s for s in self.spans(t - self.rules[0].lookback_ms, t) if s.get("kind") != "host_down"]
        hit += [s for s in self.lane_gaps if s.get("kind") == "host_down" and s["to_ms"] >= t - L_HOLD_MS and s["from_ms"] <= t]
        return ("feed_gap", sorted(hit, key=lambda s: (s["from_ms"], s["to_ms"])), None) if hit else None

    def refuse(self, ctx: Ctx, row: Mapping[str, Any], buy: Buy) -> bool:
        """True = drop the Buy. Logs the refusal row (evidence, not an order). A write failure is fatal like any decision-log failure."""
        t = int(row["t_recv_ms"])
        j = self.judge(t, buy.pool)
        if j is None:
            return False
        reason, hit, blk = j
        body = {"kind": KIND_GAP_REFUSED, "reason": reason, "rule_sha": FROZEN_SHA, "pool": buy.pool, "mint": (buy.meta or {}).get("mint"), "trigger_slot": row.get("slot"),
                "trigger_ms": t, "n_gaps": len(hit)}
        if hit:
            body["gap"] = {k: hit[0].get(k) for k in ("prog", "kind", "from_ms", "to_ms")}
        if blk:
            body["blocked_until_ms"] = int(blk["until_ms"])
        try:
            self.log.append(self.rules[0].bot_id, int(ctx.now_ms), body)
        except (OSError, LogChainError) as e:
            self._set_fatal(e)
            return True
        # every refusal extends the pool's block: the paper book may have entered at THIS trigger too (after a take-profit and the cooldown), so its next re-entry is BLOCK_MS away
        cur = self.blocked.get(buy.pool)
        self.blocked[buy.pool] = {"until_ms": max(int(cur["until_ms"]) if cur else 0, t + BLOCK_MS), "why": cur["why"] if cur else "gap"}
        self.gap_refusals += 1
        self._count("entries_refused_gap")
        return True

    # -- restart: pools the log shows open are held by the paper book until their own deadline
    def _block_open_pools(self) -> int:
        opened: dict[str, tuple[str, int]] = {}
        for r in self.log.rows(self.rules[0].bot_id):
            k = r.get("kind")
            if k == "entry":
                opened[str(r["order_id"])] = (str(r["pool"]), int(r["ms"]))
            elif k == "exit" and r.get("reason") != "resume_reopen":
                opened.pop(str(r.get("order_id")), None)
            elif k == "order_refused" and r.get("side") == "buy":
                opened.pop(str(r.get("order_id")), None)
        n = 0
        for pool, ms in opened.values():
            until = ms + BLOCK_MS
            if until > int(self.blocked.get(pool, {}).get("until_ms", 0)):
                self.blocked[pool] = {"until_ms": until, "why": "restart"}
                n += 1
        return n

    def resume(self) -> dict[str, int]:
        n = self._block_open_pools()
        res = super().resume()
        if n or self.blocked:
            self.checkpoint(self._last_T or int(self.now_fn()))  # persist now: the resume_reopen / resume_dropped rows written next hide these positions from a second restart
        return {**res, "blocked_pools": len(self.blocked)}

    # -- checkpoint
    def checkpoint(self, T: int) -> None:
        head, seq = self.log.head(self.rules[0].bot_id)
        keep_from = T - LOOKBACK_MAX_S * 1000
        persisted = [s for s in self.spans(keep_from, T) if s.get("kind") != "host_down"]
        persisted += [s for s in self.lane_gaps if s["to_ms"] >= keep_from or (s.get("kind") == "host_down" and s["to_ms"] + L_HOLD_MS >= T)]
        uniq = {canon(s): s for s in persisted}
        blocked = {p: b for p, b in self.blocked.items() if int(b["until_ms"]) > T}
        _atomic_write(self.ckpt_path, {"schema": CKPT_SCHEMA, "hwm_ms": int(T), "bot_id": self.rules[0].bot_id, "frozen_sha": FROZEN_SHA, "log_head": head, "log_seq": seq,
                                       "gaps": sorted(uniq.values(), key=lambda s: (s["from_ms"], s["to_ms"], str(s.get("kind")))), "gap_refusals": self.gap_refusals,
                                       "blocked": blocked})

    def lane_start(self, now_ms: int) -> dict[str, Any]:
        """Load the checkpoint, add the downtime as a host_down span, refuse a checkpoint that does not belong to this book or whose log is shorter than it says."""
        info: dict[str, Any] = {"checkpoint": False}
        if not self.ckpt_path.exists():
            if self.log.files(self.rules[0].bot_id):
                raise R.HostError("a decision log exists but the lane checkpoint is missing: downtime and blocked pools are unknown; refusing to start (restore the checkpoint or "
                                  "use a new root)")
            return info
        ck = json.loads(self.ckpt_path.read_text())
        if ck.get("schema") != CKPT_SCHEMA or ck.get("bot_id") != self.rules[0].bot_id or ck.get("frozen_sha") != FROZEN_SHA:
            raise R.HostError("lane checkpoint is not this book's")
        _, seq = self.log.verify(self.rules[0].bot_id)
        if seq < int(ck["log_seq"]):
            raise R.HostError(f"decision log is behind its checkpoint (seq {seq} < {ck['log_seq']}): rows were lost")
        self.lane_gaps = [s for s in ck.get("gaps", []) if isinstance(s, dict)]
        self.blocked = {str(p): dict(b) for p, b in (ck.get("blocked") or {}).items()}
        self.gap_refusals = int(ck.get("gap_refusals", 0))
        hwm = int(ck["hwm_ms"])
        self._last_T = hwm
        if now_ms > hwm:
            self.lane_gaps.append({"prog": "all", "kind": "host_down", "from_ms": hwm, "to_ms": int(now_ms), "data_loss": True})
        info.update({"checkpoint": True, "hwm_ms": hwm, "down_ms": max(0, int(now_ms) - hwm), "carried_gaps": len(self.lane_gaps), "blocked_pools": len(self.blocked)})
        return info

    def step(self, T: int) -> None:
        super().step(T)
        self._last_T = T
        if T - self._last_lane_ckpt >= CKPT_EVERY_MS:
            self._last_lane_ckpt = T
            self.checkpoint(T)

    def run(self, t_from: int, t_to: int, sleep: Callable[[float], None] = time.sleep) -> None:
        try:
            super().run(t_from, t_to, sleep)
        finally:
            if self._last_T:
                self.checkpoint(self._last_T)


def build_rules(table_mode: str | None, lookback_s: int, host_ref: list) -> list[Rule]:
    """The frozen rule behind the lane guard. `host_ref` is filled with the LaneHost once built (the guard calls back into it)."""
    if not LOOKBACK_MIN_S <= int(lookback_s) <= LOOKBACK_MAX_S:
        raise R.HostError(f"gap_lookback_s must be in [{LOOKBACK_MIN_S}, {LOOKBACK_MAX_S}]")
    inner = load_rules_v2([BOT_ID])[0]
    if table_mode is not None:
        inner.table_mode = table_mode  # tests only: synthetic tapes have no create / complete rows
    g = GapGuardRule(inner, lambda c, r, b: host_ref[0].refuse(c, r, b))
    g.lookback_ms = int(lookback_s) * 1000
    return [g]


def make_lane_host(*, root: str, ledger_root: str, registry: str, tape_dir: str, clock_start_ms: int, live: bool, binding: Mapping[str, Any] | None, stack_version: int = 2,
                   feed_delay_slots: int = R.DEFAULT_FEED_DELAY_SLOTS, warm_min: int = WARM_MIN_DEFAULT, start_ms: int | None = None, lookback_s: int = LOOKBACK_MIN_S,
                   table_mode: str | None = None, engine: Any = None, now_fn: Callable[[], int] | None = None, table: PoolTable | None = None, verbose: bool = False) -> LaneHost:
    ref: list = [None]
    rules = build_rules(table_mode, lookback_s, ref)
    state = ArenaState(tape_dir, start_ms=start_ms, warm_min=warm_min)  # live: start_ms None = warm up on the last warm_min minutes, then tail
    eng = engine if engine is not None else ArenaEngine(ledger_root, registry, cfg=stack_config_for(stack_version, feed_delay_slots))
    cfg = R.HostConfig(root=root, registry=registry, frozen_sha=FROZEN_SHA, clock_start_ms=clock_start_ms, live=live, verbose=verbose)
    host = LaneHost(cfg, rules, state, eng, now_fn=now_fn, binding=binding, lane_root=root, table=table, table_path=Path(root) / "pooltable" / "pooltable.pkl")
    ref[0] = host
    return host


def decision_digest(root: str | os.PathLike, kinds: Sequence[str] = ("entry", "exit")) -> str:
    """md5 of the canonical entry/exit rows of the lane book (chain fields included: equal digests mean equal chains when no other kind is interleaved)."""
    rows = [r for r in DecisionLog(root).rows(BOT_ID) if r.get("kind") in kinds]
    return hashlib.md5(canon(rows).encode()).hexdigest()


# ------------------------------------------------------------------------------------------------------------------------------ CLI
def _load_cfg(path: str) -> dict[str, Any]:
    d = json.loads(Path(path).read_text())
    for k in ("root", "tape_dir", "clock_start_ms"):
        if not d.get(k):
            raise R.HostError(f"config {k} is required")
    if d.get("frozen_sha", FROZEN_SHA) != FROZEN_SHA:
        raise R.HostError("frozen_sha must be FLOOR's frozen sha")
    root = str(d["root"])
    d.setdefault("ledger_root", str(Path(root) / "ledger"))
    d.setdefault("registry", str(Path(root) / "bots.json"))
    for p in (d["root"], d["ledger_root"], d["registry"]):
        if R._inside(p, R.ARENA_OUTPUT_ROOT):
            raise R.HostError(f"the lane host never writes inside {R.ARENA_OUTPUT_ROOT} ({p}): it is not the G1 arena book")
    return d


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FLOOR lane decision host (keyless, paper decisions only).")
    ap.add_argument("mode", choices=("register", "run", "replay", "verify"))
    ap.add_argument("--config", required=True)
    ap.add_argument("--from-ms", type=int, default=None)
    ap.add_argument("--to-ms", type=int, default=None)
    ap.add_argument("--since-hour", default=None, help="run, first start (no checkpoint): REQUIRED floor hour, e.g. 2026-11-01T00; the table is built from tape hours at or after it only")
    ap.add_argument("--seed-from-hour", default=None, help=f"replay: seed the pool table from tape hours at or after this hour up to the warm-up start; must be at least {SEED_MIN_H} h "
                                                           "before --from-ms (FLOOR ages are 24 to 200 h)")
    a = ap.parse_args(argv)
    try:
        d = _load_cfg(a.config)
        if a.mode == "verify":
            head, seq = DecisionLog(d["root"]).verify(BOT_ID)
            print(canon({"head": head, "seq": seq, "genesis": head == GENESIS}))
            return 0
        pins = verify_pins()
        if a.mode == "register":
            rules = load_rules_v2([BOT_ID])
            regs = R.register_rules(rules, d["registry"], FROZEN_SHA, int(d["clock_start_ms"]))
            print(canon({"n_bots": len(regs), "registry": d["registry"]}))
            return 0
        live = a.mode == "run"
        if not live:
            if a.from_ms is None or a.to_ms is None:
                raise R.HostError("replay needs --from-ms and --to-ms")
        kw = dict(root=d["root"], ledger_root=d["ledger_root"], registry=d["registry"], tape_dir=d["tape_dir"], clock_start_ms=int(d["clock_start_ms"]), live=live,
                  stack_version=int(d.get("stack_version", 2)), feed_delay_slots=int(d.get("feed_delay_slots", R.DEFAULT_FEED_DELAY_SLOTS)),
                  warm_min=int(d.get("warm_min", WARM_MIN_DEFAULT)), lookback_s=int(d.get("gap_lookback_s", LOOKBACK_MIN_S)), verbose=True)
        binding = lane_binding(pins) if live else None
        has_ckpt = (Path(d["root"]) / "lane" / "checkpoint.json").exists() and (Path(d["root"]) / "pooltable" / "pooltable.pkl").exists()
        if live and not has_ckpt and not a.since_hour:
            raise R.HostError("first start (no checkpoint): --since-hour is required")
        seed_ms = None
        if not live and a.seed_from_hour:
            seed_ms = parse_hour(a.seed_from_hour)
            if seed_ms > a.from_ms - SEED_MIN_H * HOUR_MS_:
                raise R.HostError(f"--seed-from-hour must be at least {SEED_MIN_H} h before --from-ms")
        host = make_lane_host(binding=binding, start_ms=None if live else a.from_ms - kw["warm_min"] * R.MIN_MS, **kw)
        if live:
            warm_start = int(time.time() * 1000) - kw["warm_min"] * R.MIN_MS
            since = parse_hour(a.since_hour) if a.since_hour else None
            n = rebuild_table_since(host.table, d["tape_dir"], warm_start, since) if since is not None else rebuild_table(host.table, d["tape_dir"], warm_start)
            print(canon({"table_rebuilt_rows": n, "table_hwm_ms": host.table.hwm_ms, "since_hour": a.since_hour}), flush=True)
        elif seed_ms is not None:
            n = rebuild_table_since(host.table, d["tape_dir"], a.from_ms - kw["warm_min"] * R.MIN_MS, seed_ms)
            print(canon({"table_seeded_rows": n, "seed_from_hour": a.seed_from_hour}), flush=True)
        if live:
            print(canon({"lane_start": host.lane_start(int(time.time() * 1000))}), flush=True)
        print(canon({"resumed": host.resume()}), flush=True)
        if live:
            start = max(int(d["clock_start_ms"]), int(time.time() * 1000))
            host.run(start - start % R.TICK_MS, int(d.get("end_ms") or 2 ** 62))
        else:
            host.run(a.from_ms, a.to_ms)
        print(canon({"done": host.status(), "decision_md5": decision_digest(d["root"])}), flush=True)
        return 0
    except (R.HostError, R.HostFatal, LogChainError) as e:
        print(f"floor_lane_host: {type(e).__name__}: {str(e)[:300]}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
