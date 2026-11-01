#!/usr/bin/env python3
"""arena-tip-equiv: md5 decision-equivalence of ArenaEngine on the tip ADAPTER's output against the same walker rows fed to the engine directly
(QP-RULING-1101-hv4-reads 4.1 (B) item 2). PAPER only; no key, no RPC, no network.

  ARM T (tip)       walker rows of exploration hours -> tip-format files (t_recv_ms = block_time x 1000 + LAG_MS, source "tip", singular V stamp null, bucketed by the
                    hour of t_recv_ms so rows cross hour files exactly at the hour marks; checkpoint.json says everything is done; status.json is the pinned, event-V one) ->
                    tools.arena_tip_reader.TipReader (the vendored reader) (floor lowered to the exploration hours: harness only, never on /var/lib/mal/sealed) -> tools.arena_tip_adapter.ArenaTipSource
                    (status gate, reader, adapter) -> ArenaEngine.on_row.
  ARM W (walker)    the same walker rows, same t_recv_ms, in the form the engine already accepts (type "trade", prog, every walker key kept, tx_index present, no files,
                    no reader, no adapter) -> ArenaEngine.on_row. creates and migrations are not fed to this arm (the engine ignores every type but trade and gap).
  Both arms use the SAME frozen configuration (FROZEN: one bot, deciding stake, feed_delay_slots, a deterministic order stream that is a function of the rows alone) and
  the same V map (the exploration views carry no V). Compared: the md5 of the ledger's `open` rows (the decisions), the md5 of its `trade` and `excluded` rows (the priced
  and the excluded positions), the engine counters, and the number of gaps held. PASS iff all are equal AND both arms made at least --min-decisions decisions
  (an equivalence of two empty sets proves nothing).
Output: md5s, counts, timings. No price, P&L, fill or exit state is printed or saved (the ledger rows stay in a scratch tree that is deleted). EXPLORATION hours only
(hunt23/RULES.md: /data/mal/clean-view/explore-0814, docs/HOLDOUT_LEDGER.md "Exploration expansion" row, exploration pool); no October hour; no sealed path.
Run as a MiScusi job on mal-research-0 inside systemd-run --user --scope (scripts/research/arena-tip-equiv.sh).
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.environ.setdefault("MAL_ARENA_TEST", "1")  # the scratch registry uses the test-only clock / sha overrides; nothing here touches the real registry

from tools import arena_tip_adapter as ta  # noqa: E402
from tools import arena_tip_reader as tr  # noqa: E402
from tools.arena_paper import MIN_FREE_PCT_FLOOR, ArenaEngine, ArenaRefused, StackConfig, register_bot  # noqa: E402

CLEAN = Path("/data/mal/clean-view/explore-0814")
WINDOWS = ("w7", "w6", "w5")
SCRATCH_PARENT = Path("/data2/mal/scratch/arena-tip-equiv")
EXPLORE_FIRST, EXPLORE_END = "2026-08-14T12", "2026-08-28T12"  # HOLDOUT_LEDGER "Exploration expansion" [2026-08-14T12, 2026-08-28T12)
LAG_MS = 1800
WSOL = "So11111111111111111111111111111111111111112"
FORBIDDEN = ("forward-", "truth-", "fresh-08", "exp011-", "exp012", "tip-tape", "/var/lib/mal/sealed", "meta-capture", "/data/mal/arena", "shadow", "canary", "probe", "walk",
             "_shakedown", "_smoke", "kalshi", "fwdv1")
# THE PINNED FROZEN CONFIGURATION (its md5 is printed in the report and asserted in the tests; a change is a new proof)
FROZEN = {"bot_id": "tipeq-A", "frozen_sha": "abc1234", "deciding_stake": 0.25, "feed_delay_slots": 2, "stake_sol": 0.25, "hot_every": 1500, "hot_every_bonding": 4000, "sparse_index": 5,
          "sparse_mod": 4, "quiet_slots": 5500, "quiet_min_prints": 4, "holds_ms": [20000, 45000, 90000, 300000], "v_lo": 17_500_000_000, "v_hi": 30_000_000_000}
FROZEN_MD5 = hashlib.md5(json.dumps(FROZEN, sort_keys=True).encode()).hexdigest()


class Refused(Exception):
    pass


# ------------------------------------------------------------------------------------------------------------------------------------ refusals
def check_path(p: str | os.PathLike, what: str) -> None:
    for form in (os.fspath(p), os.path.abspath(p), os.path.realpath(p)):
        low = form.lower()
        for w in FORBIDDEN:
            if w in low:
                raise Refused(f"{what}: path contains {w!r}")


def check_hours(h0: str, h1: str) -> None:
    for h in (h0, h1):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}", h):
            raise Refused(f"bad hour {h}")
    if not (EXPLORE_FIRST <= h0 < h1 <= EXPLORE_END):
        raise Refused(f"hours must lie inside the exploration block [{EXPLORE_FIRST}, {EXPLORE_END}); got [{h0}, {h1})")


def check_out(out: Path, parent: Path = SCRATCH_PARENT) -> None:
    if not os.path.realpath(str(out)).startswith(str(parent) + "/") or out.exists() and any(out.iterdir()):
        raise Refused(f"--out must be a new, empty dir under {parent}")


# ------------------------------------------------------------------------------------------------------------------------------------ the two forms of one row
def twin(kind: str, w: dict) -> dict:
    """The follower's output form of a walker row: t_recv_ms = block_time x 1000 + LAG_MS (all kinds of one slot share it), source "tip", the singular V stamp (null) on
    trades, and quote_mint WSOL where the old walker row only says quote_is_wsol (the current decoder writes both)."""
    r = {**w, "t_recv_ms": int(w["block_time"]) * 1000 + LAG_MS, "source": "tip"}
    if kind == "trade":
        r["virtual_quote_reserve"] = None
        if r.get("quote_is_wsol") is True and not r.get("quote_mint"):
            r["quote_mint"] = WSOL
    return r


def engine_row(t: dict) -> dict:
    """The form ArenaEngine already accepts for walker rows (exploration tape, tx_index-bearing): every walker key kept, type and prog added."""
    return {"type": "trade", "prog": ta.VENUE_PROG[t["venue"]], **t}


def rank(kind: str) -> int:
    return {"migration": 0, "create": 1, "trade": 2}[kind]


# ------------------------------------------------------------------------------------------------------------------------------------ the frozen order stream
class FrozenOrders:
    """Deterministic buys and exits, a function of the rows alone (the shape of tools/arena_prune_replay.OrderStream, with V from the map where a row has none)."""

    def __init__(self, eng: ArenaEngine, vmap: Any, cfg: dict = FROZEN) -> None:
        self.eng, self.vmap, self.cfg = eng, vmap, cfg
        self.n_elig = self.n_bond = self.n_buys = 0
        self.per_pool: dict[str, int] = {}
        self.last_slot: dict[str, int] = {}
        self.due: list[tuple[int, int, str]] = []
        self.stats: dict[str, int] = {"buy_attempts": 0, "buys_open": 0, "buys_refused": 0, "sells": 0, "sells_refused": 0}

    def _buy(self, pool: str, slot: int, rule: str) -> None:
        eng, c = self.eng, self.cfg
        self.stats["buy_attempts"] += 1
        self.n_buys += 1
        try:
            pid = eng.buy(c["bot_id"], pool, c["stake_sol"], int(eng.hw_ms or 0), int(slot), {"rule": rule, "n": self.n_buys})
        except ArenaRefused:
            self.stats["buys_refused"] += 1
            return
        self.stats["buys_open"] += 1
        heapq.heappush(self.due, (int(eng.hw_ms or 0) + c["holds_ms"][self.n_buys % len(c["holds_ms"])], self.n_buys, pid))

    def after_row(self, row: dict) -> None:
        eng, c = self.eng, self.cfg
        while self.due and self.due[0][0] <= (eng.hw_ms or 0):
            _, _, pid = heapq.heappop(self.due)
            try:
                eng.sell(c["bot_id"], pid, 1.0, int(eng.hw_ms or 0), int(eng.hw_slot), "hold")
                self.stats["sells"] += 1
            except ArenaRefused:
                self.stats["sells_refused"] += 1
        if row.get("type") != "trade" or row.get("slot") is None or row.get("side") not in ("buy", "sell"):
            return
        slot, prog = int(row["slot"]), row.get("prog")
        if prog == "pumpswap":
            pool = row.get("pool")
            v = row.get("virtual_quote_reserves")
            v = v if v is not None else self.vmap.get(pool)
            if not pool or v is None or not (c["v_lo"] <= v <= c["v_hi"]) or row.get("quote_mint") != WSOL:
                return
            self.n_elig += 1
            k = self.per_pool[pool] = self.per_pool.get(pool, 0) + 1
            prev = self.last_slot.get(pool)
            self.last_slot[pool] = max(slot, prev if prev is not None else slot)
            if self.n_elig % c["hot_every"] == 0:
                self._buy(pool, slot, "hot")
            elif k == c["sparse_index"] and sum(pool.encode()) % c["sparse_mod"] == 0:
                self._buy(pool, slot, "sparse")
            elif prev is not None and k >= c["quiet_min_prints"] and slot - prev > c["quiet_slots"]:
                self._buy(pool, slot, "quiet")
        elif prog == "pumpfun":
            pool = row.get("pool") or row.get("mint")
            if not pool or not row.get("quote_reserve") or not row.get("base_reserve"):
                return
            self.n_bond += 1
            if self.n_bond % c["hot_every_bonding"] == 0:
                self._buy(pool, slot, "hot")


class Arm:
    def __init__(self, name: str, scratch: Path, vmap: Any, cfg: dict = FROZEN) -> None:
        self.name, self.scratch = name, scratch
        scratch.mkdir(parents=True, exist_ok=True)
        register_bot(cfg["bot_id"], cfg["frozen_sha"], 0, scratch / "bots.json", deciding_stake=cfg["deciding_stake"], model_ids=[], now_ms=0, verify_sha=False)
        self.eng = eng = ArenaEngine(scratch / "ledger", scratch / "bots.json", StackConfig(feed_delay_slots=cfg["feed_delay_slots"]), vmap=vmap, now_fn=lambda: 0,
                                     code_state_fn=lambda: ("0" * 40, False), min_free_pct=MIN_FREE_PCT_FLOOR)
        eng.now_fn = lambda: eng.hw_ms or 0  # the call clock is the latest feed row: a bot called inline, thinking for no time
        eng.engine_id = "0" * 16
        self.orders = FrozenOrders(eng, vmap, cfg)
        self.n_rows = 0

    def feed(self, row: dict) -> None:
        self.eng.on_row(row)
        self.orders.after_row(row)
        self.n_rows += 1

    def digest(self) -> dict[str, Any]:
        """md5 of the decisions (`open` rows) and of the priced / excluded positions (`trade`, `excluded`), counts by kind. The `start` record (pid, code state) is left out."""
        h = {"open": hashlib.md5(), "trades": hashlib.md5()}
        n: dict[str, int] = {}
        for p in sorted((self.scratch / "ledger").rglob("*.jsonl")):
            for line in p.read_text().splitlines():
                r = json.loads(line)
                k = str(r.get("kind"))
                n[k] = n.get(k, 0) + 1
                c = json.dumps(r, sort_keys=True, separators=(",", ":")).encode() + b"\n"
                if k == "open":
                    h["open"].update(c)
                elif k in ("trade", "excluded"):
                    h["trades"].update(c)
        return {"decisions_md5": h["open"].hexdigest(), "trades_md5": h["trades"].hexdigest(), "ledger_kinds": n, "counters": dict(sorted(self.eng.counters.items())),
                "gaps_held": len(self.eng.gaps), "orders": dict(self.orders.stats), "rows_in": self.n_rows}


# ------------------------------------------------------------------------------------------------------------------------------------ building the tip dir
def expected_projection(kind: str, w: dict) -> tuple | None:
    """What the arena tape should carry for a walker create / complete / migration row (built here, independent of the adapter): (type, slot, tx_index, signature, event_index, mint, pool)."""
    if kind == "create":
        return ("create", w["slot"], w.get("tx_index"), w.get("signature"), w.get("event_index"), w.get("mint"), None)
    if kind == "migration":
        if w.get("type") == "complete":
            return ("complete", w["slot"], w.get("tx_index"), w.get("signature"), w.get("event_index"), w.get("mint"), None)
        return ("pool_create", w["slot"], w.get("tx_index"), w.get("signature"), w.get("event_index"), w.get("mint"), w.get("pool"))
    return None


def build_tip_dir(seq: Iterable[tuple[str, dict]], tip: Path, state: Path, *, gaps: Iterable[dict] = (), skip_every: int = 0) -> dict[str, Any]:
    """(kind, walker row) in slot order -> trades-/creates-/migrations-<hour of t_recv_ms>.jsonl, gaps.jsonl, skipped-slots-<hour>.jsonl, checkpoint.json, status.json. STREAMING: rows are
    written as they come. skip_every > 0 injects a recorded skipped slot (prev + 1, a slot with no walker row) after every skip_every-th hole in the slot sequence (H4)."""
    tip.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    fh: dict[str, Any] = {}
    n = {"trades": 0, "creates": 0, "migrations": 0}
    last = {"slot": 0, "t": 0}
    plural = {"trade": "trades", "create": "creates", "migration": "migrations"}
    proj_md5, proj_n, holes, skips, no_tx = hashlib.md5(), 0, 0, 0, 0

    def out(name: str):
        return fh.get(name) or fh.setdefault(name, open(tip / name, "ab"))

    for kind, w in seq:
        r = twin(kind, w)
        if skip_every and last["slot"] and int(r["slot"]) - last["slot"] >= 2:
            holes += 1
            if holes % skip_every == 0:
                sl = last["slot"] + 1
                out(f"skipped-slots-{tr.hour_key(last['t'] // 1000)}.jsonl").write(json.dumps({"slot": sl, "kind": "skipped", "t_recv_ms": last["t"]}, separators=(",", ":")).encode() + b"\n")
                skips += 1
        out(f"{plural[kind]}-{tr.hour_key(r['t_recv_ms'] // 1000)}.jsonl").write(json.dumps(r, separators=(",", ":")).encode() + b"\n")
        n[plural[kind]] += 1
        pj = expected_projection(kind, w)
        if pj is not None:
            proj_md5.update(json.dumps(pj, separators=(",", ":")).encode() + b"\n")
            proj_n += 1
        no_tx += kind == "trade" and w.get("venue") == "pumpswap" and w.get("tx_index") is None
        last["slot"], last["t"] = max(last["slot"], int(r["slot"])), max(last["t"], r["t_recv_ms"])
    for f in fh.values():
        f.close()
    gl = list(gaps)
    if gl:
        with open(tip / tr.GAP_NAME, "ab") as g:
            for row in gl:
                g.write(json.dumps(row, separators=(",", ":")).encode() + b"\n")
        last["slot"] = max([last["slot"]] + [int(x.get("to_slot") or x.get("slot") or 0) for x in gl])
    (state / "checkpoint.json").write_text(json.dumps({"last_done": last["slot"], "last_t_recv_ms": last["t"], "gaps": len(gl), "skipped": skips, "writing": None}))
    now = last["t"] + 1000
    (state / "status.json").write_text(json.dumps({"ts_ms": now, "decoder_blobs": dict(tr.PINNED_BLOBS), "decoder_blobs_pinned": True, "trade_event_v": True}))
    return {"rows": n, "files": len(fh), "last_slot": last["slot"], "status_ts_ms": now, "skips_injected": skips, "projection_md5": proj_md5.hexdigest(), "projection_n": proj_n,
            "walker_rows_no_tx_index": no_tx}


PROJECTED = ("create", "complete", "pool_create")


def tip_rows(tip: Path, state: Path, first_hour: str, status_ts_ms: int, stats: dict) -> Iterator[dict]:
    """The adapter's output for a tip dir, through the real reader and source (the status gate included)."""
    reader = tr.TipReader(tip, state, start_ms=tr.hour_epoch(first_hour) * 1000, catch_up_hours=0, min_hour="2026-08-01T00", lookback_h=None, fadvise=False)
    src = ta.ArenaTipSource(reader, state, now_ms=lambda: status_ts_ms, io_hook=lambda: True, require_io_idle=False)
    src.start()
    while True:
        rows = src.poll(50_000)
        if not rows and src.caught_up:
            break
        if not rows and src.problem:
            raise Refused(f"tip side problem: {src.problem}")
        yield from rows
    stats.update({"adapter": src.adapter.counts(), "reader": dict(reader.stats)})


def peak_rss_mb() -> float:
    import resource

    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)  # Linux: kilobytes


def run_equiv(seq: Any, scratch: Path, vmap: Any, first_hour: str, *, cfg: dict = FROZEN, gaps: Iterable[dict] = (), skip_every: int = 0, min_decisions: int = 1,
              require_no_tx: bool = False, keep: bool = False) -> dict[str, Any]:
    """Both arms over one sequence of (kind, walker row). `seq` is a LIST or a zero-argument callable returning a fresh iterator; the sequence is read TWICE (ARM W, then the tip
    build), one hour at a time when `seq` is load_walker, and ARM W's engine is digested and dropped before ARM T exists (memory: H3 of QP on #1165)."""
    import gc

    t0 = time.monotonic()
    fresh = seq if callable(seq) else (lambda: iter(seq))
    w = Arm("W", scratch / "W", vmap, cfg)
    for kind, row in fresh():
        if kind == "trade":
            w.feed(engine_row(twin(kind, row)))
    dw = w.digest()
    del w
    gc.collect()
    rss_w = peak_rss_mb()
    built = build_tip_dir(fresh(), scratch / "tip", scratch / "state", gaps=gaps, skip_every=skip_every)
    t = Arm("T", scratch / "T", vmap, cfg)
    stats: dict[str, Any] = {}
    proj, proj_n = hashlib.md5(), 0
    for r in tip_rows(scratch / "tip", scratch / "state", first_hour, built["status_ts_ms"], stats):
        t.feed(r)
        if r["type"] in PROJECTED:
            proj.update(json.dumps((r["type"], r["slot"], r.get("tx_index"), r.get("signature"), r.get("event_index"), r.get("mint"), r.get("pool")), separators=(",", ":")).encode() + b"\n")
            proj_n += 1
    dt = t.digest()
    ad = stats["adapter"]
    equal = {k: dw[k] == dt[k] for k in ("decisions_md5", "trades_md5", "counters", "gaps_held")}
    equal["projection"] = proj.hexdigest() == built["projection_md5"] and proj_n == built["projection_n"]
    equal["no_tx_index_rows"] = ad.get("rows_no_tx_index", 0) == built["walker_rows_no_tx_index"]
    equal["skips"] = ad.get("skips", 0) == built["skips_injected"]
    equal["no_gap_rows"] = ad.get("gap_rows", 0) == len(list(gaps)) and ad.get("pool_slots_mixed", 0) == 0
    ok = all(equal.values()) and dw["orders"]["buys_open"] >= min_decisions and (built["walker_rows_no_tx_index"] > 0 or not require_no_tx)
    rep = {"schema": "arena_tip_equiv_v2", "config": cfg, "config_md5": hashlib.md5(json.dumps(cfg, sort_keys=True).encode()).hexdigest(), "lag_ms": LAG_MS,
           "built": {k: v for k, v in built.items() if k != "status_ts_ms"}, "arm_walker": dw, "arm_tip": dt, "tip_stats": stats, "equal": equal, "projection_n": proj_n,
           "min_decisions": min_decisions, "require_no_tx": require_no_tx, "peak_rss_mb_after_arm_w": rss_w, "peak_rss_mb": peak_rss_mb(), "verdict": "PASS" if ok else "FAIL",
           "seconds": round(time.monotonic() - t0, 1), "note": "md5s, counts, timings and RSS only; no price, P&L, fill or exit state is reported."}
    if not keep:
        for d in ("W", "T", "tip", "state"):
            shutil.rmtree(scratch / d, ignore_errors=True)
    return rep


# ------------------------------------------------------------------------------------------------------------------------------------ exploration data
def hour_list(h0: str, h1: str) -> list[str]:
    t, end = tr.hour_epoch(h0), tr.hour_epoch(h1)
    return [tr.hour_key(x) for x in range(t, end, 3600)]


def _tagged(keyed: list, kind: str, rk: int) -> Iterator[tuple]:
    """(slot, rank, index, kind, raw line) for one kind: `kind` is bound per call (a generator expression inside the loop read it lazily, and labelled every create and migration a trade)."""
    for i, (k, line) in enumerate(keyed):
        yield (k[0], rk, i, kind, line)


def load_walker(clean: Path, windows: Iterable[str], h0: str, h1: str, max_items: int | None = None) -> Iterator[tuple[str, dict]]:
    """Walker rows of the hours [h0, h1) in (slot, migration | create | trade, tx_index, event_index) order, ONE HOUR AT A TIME and as raw lines (about 0.8 KB a row, not the 3 KB of a
    parsed dict): the rows are parsed once for the sort key and again when they are yielded. Reads <clean>/<w>/{trades,creates,migrations}/<kind>-<hour>.jsonl.zst through
    tools.arena_clean_adapter's reader (zstd -dc). max_items stops the stream after that many items (a truncated local run)."""
    from tools.arena_clean_adapter import hour_files, zlines

    dirs = [str(clean / w) for w in windows]
    files = {k: hour_files(dirs, k) for k in ("trades", "creates", "migrations")}
    n = 0
    for h in hour_list(h0, h1):
        if h not in files["trades"]:
            raise Refused(f"hour {h}: no trades file")
        parts = []
        for sub, kind in (("migrations", "migration"), ("creates", "create"), ("trades", "trade")):
            p = files[sub].get(h)
            keyed = []
            for line in (zlines(p) if p else ()):
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and isinstance(r.get("slot"), int) and isinstance(r.get("block_time"), int):
                    txi, evi = r.get("tx_index"), r.get("event_index")
                    keyed.append(((r["slot"], txi if isinstance(txi, int) else -1, evi if isinstance(evi, int) else -1), line))
            keyed.sort(key=lambda x: x[0])
            parts.append(_tagged(keyed, kind, rank(kind)))
        for _, _, _, kind, line in heapq.merge(*parts, key=lambda x: x[:3]):
            yield kind, json.loads(line)
            n += 1
            if max_items is not None and n >= max_items:
                return


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--hours", default="2026-08-16T12..2026-08-16T18", help="h0..h1 (h1 exclusive), inside the exploration block")
    ap.add_argument("--clean", default=str(CLEAN))
    ap.add_argument("--min-decisions", type=int, default=50)
    ap.add_argument("--max-items", type=int, default=None, help="stop after this many walker items (a truncated run: the report says so)")
    ap.add_argument("--skip-every", type=int, default=50, help="inject a recorded skipped slot after every Nth hole in the slot sequence (0 = none)")
    ap.add_argument("--expect-config-md5", default=FROZEN_MD5)
    ap.add_argument("--keep-scratch", action="store_true")
    a = ap.parse_args(argv)
    h0, h1 = a.hours.split("..")
    try:
        if a.expect_config_md5 != FROZEN_MD5:
            raise Refused(f"the frozen config md5 is {FROZEN_MD5}, not {a.expect_config_md5}")
        out = Path(a.out)
        check_path(a.out, "--out")
        check_path(a.clean, "--clean")
        check_hours(h0, h1)
        check_out(out)
        if os.path.realpath(a.clean) != os.path.realpath(str(CLEAN)):
            raise Refused(f"--clean must be {CLEAN}")
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    from tools import arena_screen as S

    vmap = S.VMap([str(p) for p in S.check_vmap_paths(S.VMAP_DEFAULT)]) if hasattr(S, "check_vmap_paths") else S.VMap(list(S.VMAP_DEFAULT))
    rep = run_equiv(lambda: load_walker(Path(a.clean), WINDOWS, h0, h1, a.max_items), out / "run", vmap, h0, min_decisions=a.min_decisions, skip_every=a.skip_every, require_no_tx=True,
                    keep=a.keep_scratch)
    rep.update({"hours": [h0, h1], "max_items": a.max_items, "truncated_run": a.max_items is not None, "git_head": os.popen(f"git -C {REPO} rev-parse HEAD").read().strip()})
    (out / "report.json").write_text(json.dumps(rep, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps({"verdict": rep["verdict"], "equal": rep["equal"], "decisions_md5_walker": rep["arm_walker"]["decisions_md5"], "decisions_md5_tip": rep["arm_tip"]["decisions_md5"],
                      "trades_md5_walker": rep["arm_walker"]["trades_md5"], "trades_md5_tip": rep["arm_tip"]["trades_md5"], "config_md5": rep["config_md5"],
                      "decisions": rep["arm_walker"]["orders"]["buys_open"], "rows_no_tx_index": rep["tip_stats"]["adapter"].get("rows_no_tx_index", 0), "peak_rss_mb": rep["peak_rss_mb"],
                      "seconds": rep["seconds"], "truncated_run": rep["truncated_run"]}, sort_keys=True))
    mres = os.environ.get("MISCUSI_RESULT")
    if mres:
        Path(mres).write_text(json.dumps({"ok": rep["verdict"] == "PASS", "verdict": rep["verdict"], "summary": f"arena tip-adapter equivalence {h0}..{h1}: {rep['verdict']}"}))
    return 0 if rep["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
