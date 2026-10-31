#!/usr/bin/env python3
"""arena_paper_ckpt: checkpoint and restore of the arena paper engine for a STATE-CARRYING runner hand-off (P35 item (b);
docs/research/p35-longbook-handoff/DESIGN.md section 3). PAPER only. No key, no network.

tools/arena_paper.py is NOT edited (a section 1.4 pin of registered books). `EngineCkpt` subclasses `ArenaEngine` and adds:
  checkpoint(path, **meta) -> {"sha256", "state_digest", "bytes"}   whole engine state as JSON, atomic (tmp, fsync, rename, fsync dir)
  restore(path, expect_sha256=None) -> state_digest                   rebuild that state in a NEW engine object (a fresh process in production)

STATE CARRIED (everything ArenaEngine.on_row / buy / sell / pricing reads): pools (PoolHist: prints, keys, dedupe set, V and V source, last_v_row, trim
floor), positions (every field; `restarted` stays as checkpointed, so a position that was continuous stays continuous), pending sells, priced entries
(late-print watch: trigger keys and spans), gaps, the feed high-water marks (hw_slot, hw_ms, hw_ms_slot), the slot clock's points, counters,
last_error, halted. NOT carried: engine_id, started_ms, the code state and the set of bots that have a start record (the new engine is a new
process: it writes its own start record; PR 5's host adds a `handoff` record linking the two engines), the Ledger object (it is the files on disk),
derived caches (PoolHist._np, the slot clock's fit cache, the code-state cache).

ORDER. Every map whose iteration order can change a decision or a write order (pools, positions, pending, priced, counters) is stored as a list of
[key, value] pairs in insertion order, so a restored engine iterates exactly as the checkpointed one did.

DIGEST. state_digest = sha256 of the canonical JSON of the state (the bytes written, without the envelope). restore() recomputes it from the
rebuilt engine and refuses a mismatch, a wrong schema, a stack config that differs from this engine's, or a file sha256 that differs from
expect_sha256 (the value the segment-end log row recorded). A refused restore raises CheckpointInvalid; the caller falls back to recover() (every
open position booked engine_restart: fail closed).
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import collections
import dataclasses
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from tools.arena_paper import ArenaEngine, Gap, PoolHist, Position

SCHEMA = "arena_engine_ckpt_v1"


class CheckpointInvalid(Exception):
    pass


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, separators=(",", ":"), allow_nan=False, ensure_ascii=True).encode()


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _tup(x: Any) -> Any:
    """JSON lists back to the tuples the engine uses as keys (one level; keys and spans are flat)."""
    return tuple(x) if isinstance(x, list) else x


def cfg_dict(cfg: Any) -> dict[str, Any]:
    return json.loads(_canon(dataclasses.asdict(cfg)))


def _pool_state(h: PoolHist) -> dict[str, Any]:
    return {"pool": h.pool, "mint": h.mint, "venue": h.venue, "keys": [list(k) for k in h.keys], "seen": sorted([list(s) for s in h.seen], key=lambda s: json.dumps(s)),
            "slot": list(h.slot), "isbuy": list(h.isbuy), "sol": list(h.sol), "tok": list(h.tok), "q": list(h.q), "b": list(h.b), "v": list(h.v),
            "vsrc": list(h.vsrc), "last_v_row": h.last_v_row, "floor": h.floor}


def _pool_restore(d: dict[str, Any]) -> PoolHist:
    h = PoolHist(d["pool"], d["mint"], d["venue"])
    h.keys = [tuple(k) for k in d["keys"]]
    h.seen = {tuple(s) for s in d["seen"]}
    h.slot, h.isbuy, h.sol, h.tok = list(d["slot"]), list(d["isbuy"]), list(d["sol"]), list(d["tok"])
    h.q, h.b, h.v, h.vsrc = list(d["q"]), list(d["b"]), list(d["v"]), list(d["vsrc"])
    h.last_v_row, h.floor = d["last_v_row"], d["floor"]
    return h


def _priced_state(e: dict[str, Any]) -> dict[str, Any]:
    out = dict(e)
    out["trigger"] = list(e["trigger"]) if e.get("trigger") is not None else None
    out["span"] = list(e["span"]) if e.get("span") is not None else None
    return out


def _priced_restore(e: dict[str, Any]) -> dict[str, Any]:
    out = dict(e)
    out["trigger"] = _tup(e.get("trigger"))
    out["span"] = _tup(e.get("span"))
    return out


def engine_state(eng: ArenaEngine) -> dict[str, Any]:
    """The carried state of an engine, JSON-able, order-preserving (see the module docstring)."""
    clk = eng.clock
    return {
        "pools": [[k, _pool_state(h)] for k, h in eng.pools.items()],
        "positions": [[k, dataclasses.asdict(p)] for k, p in eng.positions.items()],
        "pending": [[k, list(v)] for k, v in eng.pending.items()],
        "priced": [[k, [_priced_state(e) for e in v]] for k, v in eng.priced.items()],
        "gaps": [dataclasses.asdict(g) for g in eng.gaps],
        "hw": {"hw_slot": eng.hw_slot, "hw_ms": eng.hw_ms, "hw_ms_slot": eng.hw_ms_slot},
        "clock": {"window_s": clk.window_s, "min_span_s": clk.min_span_s, "min_points": clk.min_points, "pts": [list(p) for p in clk._pts]},
        "counters": [[k, v] for k, v in eng.counters.items()],
        "last_error": [[k, v] for k, v in eng.last_error.items()],
        "halted": [[k, v] for k, v in eng.halted.items()],
    }


def restore_engine(eng: ArenaEngine, st: dict[str, Any]) -> None:
    eng.pools = {k: _pool_restore(d) for k, d in st["pools"]}
    eng.positions = {k: Position(**d) for k, d in st["positions"]}
    eng.pending = {k: list(v) for k, v in st["pending"]}
    eng.priced = {k: [_priced_restore(e) for e in v] for k, v in st["priced"]}
    eng.gaps = [Gap(**g) for g in st["gaps"]]
    eng.hw_slot, eng.hw_ms, eng.hw_ms_slot = st["hw"]["hw_slot"], st["hw"]["hw_ms"], st["hw"]["hw_ms_slot"]
    c = st["clock"]
    clk = eng.clock
    clk.window_s, clk.min_span_s, clk.min_points = c["window_s"], c["min_span_s"], c["min_points"]
    clk._pts = collections.deque(tuple(p) for p in c["pts"])
    clk._dirty, clk._cache = True, None
    eng.counters = {k: v for k, v in st["counters"]}
    eng.last_error = {k: v for k, v in st["last_error"]}
    eng.halted = {k: v for k, v in st["halted"]}


def state_digest(eng: ArenaEngine) -> str:
    return _sha(_canon(engine_state(eng)))


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    dfd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


class EngineCkpt(ArenaEngine):
    """ArenaEngine plus checkpoint / restore. Behaviour is otherwise identical (no method of ArenaEngine is overridden)."""

    def checkpoint(self, path: str | os.PathLike, **meta: Any) -> dict[str, Any]:
        st = engine_state(self)
        body = _canon(st)
        digest = _sha(body)
        env = {"schema": SCHEMA, "engine_id": self.engine_id, "cfg": cfg_dict(self.cfg), "meta": meta, "state_digest": digest}
        data = _canon(env)[:-1] + b',"state":' + body + b"}\n"
        _atomic_write(Path(path), data)
        return {"sha256": _sha(data), "state_digest": digest, "bytes": len(data), "positions": len(self.positions), "pools": len(self.pools)}

    def restore(self, path: str | os.PathLike, expect_sha256: str | None = None) -> str:
        """Rebuild the checkpointed state into this (new, empty) engine. Returns the state digest. Raises CheckpointInvalid on any mismatch."""
        if self.pools or self.positions or self.gaps or self.hw_ms is not None:
            raise CheckpointInvalid("restore into an engine that has already seen rows")
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            raise CheckpointInvalid(f"unreadable checkpoint: {type(exc).__name__}") from exc
        if expect_sha256 is not None and _sha(data) != expect_sha256:
            raise CheckpointInvalid("checkpoint sha256 differs from the recorded one")
        try:
            env = json.loads(data)
        except ValueError as exc:
            raise CheckpointInvalid("checkpoint is not JSON") from exc
        if env.get("schema") != SCHEMA:
            raise CheckpointInvalid(f"schema {env.get('schema')!r}")
        if env.get("cfg") != cfg_dict(self.cfg):
            raise CheckpointInvalid("the stack config differs from this engine's")
        st = env.get("state")
        if not isinstance(st, dict) or _sha(_canon(st)) != env.get("state_digest"):
            raise CheckpointInvalid("state digest mismatch (the file was changed)")
        restore_engine(self, st)
        got = state_digest(self)
        if got != env["state_digest"]:
            raise CheckpointInvalid("restored state does not reproduce the digest")
        self.restored_from = {"engine_id": env.get("engine_id"), "state_digest": got, "sha256": _sha(data), "meta": env.get("meta")}
        return got


def main(argv: list[str] | None = None) -> int:
    """`digest <ckpt>`: restore a checkpoint into a fresh engine in THIS process and print its state digest (the fresh-process restore test).
    The engine is built with the checkpoint's own stack config and a throwaway ledger root under the checkpoint's directory."""
    import argparse

    from tools.arena_paper import StackConfig

    ap = argparse.ArgumentParser(description="restore an engine checkpoint in a fresh process and print the digest")
    ap.add_argument("mode", choices=["digest"])
    ap.add_argument("ckpt")
    args = ap.parse_args(argv)
    env = json.loads(Path(args.ckpt).read_bytes())
    cfg_d = dict(env["cfg"])
    cfg_d["stakes_sol"] = tuple(cfg_d.get("stakes_sol") or ())
    cfg = StackConfig(**cfg_d)
    root = Path(args.ckpt).parent / ".digest-ledger"
    eng = EngineCkpt(ledger_root=root / "ledger", registry_path=root / "bots.json", cfg=cfg, code_state_fn=lambda: (None, None))
    try:
        d = eng.restore(args.ckpt)
    except CheckpointInvalid as exc:
        print(json.dumps({"refused": str(exc)}))
        return 3
    print(json.dumps({"state_digest": d, "positions": len(eng.positions), "pools": len(eng.pools)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
