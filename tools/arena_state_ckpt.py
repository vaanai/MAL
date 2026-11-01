#!/usr/bin/env python3
"""arena_state_ckpt: checkpoint and restore of tools.arena_state.ArenaState, with its tape cursor (P35 item (b); QP-RULING-1026 Q3: ArenaState is
checkpointed from the start, never rebuilt by a warm-up across a hand-off). PAPER only. No key, no network.

tools/arena_state.py is NOT edited. `StateCkpt` subclasses `ArenaState` and changes ONE thing: `_zst_chunks` is the same chunking, made an
instance method so the reader knows how many decompressed bytes of a compacted hour it has taken (`_zst_pos`). ArenaState itself never needs
that (it never stops mid-file); a checkpoint does. Everything else is inherited unchanged.

WHY a checkpoint and not a warm-up: `gaps_total` and `loss_spans` are never pruned, a key's `gaps_at_first_seen` is relative to them, and a key's
`net` / `events` / `n_trades` / `ath` cover its whole life until it has been idle 6 h. A 365-min warm-up rebuilds none of that for a pool that
trades for longer (FLOOR's pools are 24-200 h old).

STATE CARRIED: every Pool (all slots; events, net, candles in order), the mint index, gap spans (keyed (prog, kind, from_ms)), gaps_total,
loss_spans, last_hb, counters, now_ms, start_ms, the sweep clock, evict_idle_ms, and the TAPE CURSOR: for every hour the reader has touched, the
bytes taken (the .jsonl offset, or the decompressed offset inside the .jsonl.zst) and its done flag, plus the rows read but not yet applied
(`_buf`). A restored reader continues at exactly the next unread line: from the .jsonl when it still exists, else from the .zst at the same
decompressed offset (compaction is byte-exact). Callbacks (on_trade) are not state: the host sets them again.

DIGEST: sha256 of the canonical JSON of that state. restore() recomputes it from the rebuilt object and refuses a mismatch (CheckpointInvalid).
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import collections
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterator

from tools.arena_paper_ckpt import CheckpointInvalid, _atomic_write, _canon, _sha
from tools.arena_state import READ_CHUNK, ArenaState, Pool, _Hour
from tools.arena_zst import open_zst

SCHEMA = "arena_state_ckpt_v1"
POOL_SLOTS = Pool.__slots__


def _pool_state(p: Pool) -> dict[str, Any]:
    d = {s: getattr(p, s) for s in POOL_SLOTS if s not in ("events", "net", "candles")}
    d["events"] = [list(e) for e in p.events]
    d["net"] = [[k, v] for k, v in p.net.items()]
    d["candles"] = [[m, list(c)] for m, c in p.candles.items()]
    return d


def _pool_restore(d: dict[str, Any]) -> Pool:
    p = Pool(d["id"], d["prog"], d["first_seen_ms"], d["gaps_at_first_seen"])
    for s in POOL_SLOTS:
        if s in ("events", "net", "candles"):
            continue
        setattr(p, s, d[s])
    p.events = collections.deque(tuple(e) for e in d["events"])
    p.net = {k: v for k, v in d["net"]}
    p.candles = collections.OrderedDict((int(m), list(c)) for m, c in d["candles"])
    return p


class StateCkpt(ArenaState):
    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        self._zst_pos: dict[str, int] = {}  # hour key -> decompressed bytes taken from its .jsonl.zst (only while a .zst is being read)

    def _zst_chunks(self, path: Path, skip: int) -> Iterator[bytes]:  # type: ignore[override]
        """ArenaState._zst_chunks, chunk for chunk, plus the position: after a chunk is yielded, _zst_pos[hour] = skip + the bytes up to and
        including the chunk's last newline (the final unterminated piece counts its own length)."""
        key = path.name[len("tape-"):-len(".jsonl.zst")]
        r = open_zst(path)
        try:
            left = skip
            while left > 0:
                b = r.read(min(left, 1 << 20))
                if not b:
                    break
                left -= len(b)
            pos = skip - max(left, 0)
            carry = b""
            while True:
                b = r.read(READ_CHUNK)
                if not b:
                    break
                data = carry + b
                cut = data.rfind(b"\n")
                if cut < 0:
                    carry = data
                    continue
                carry = data[cut + 1:]
                pos += cut + 1
                self._zst_pos[key] = pos
                yield data[:cut]
            if carry.strip():
                pos += len(carry)
                self._zst_pos[key] = pos
                yield carry
        finally:
            r.close()

    # -- checkpoint
    def _cursor(self) -> list[list[Any]]:
        out = []
        for key, st in self._hours.items():
            if st.done:
                out.append([key, None, True])  # a finished hour: its offset is never used again (and differs between a resumed and a continuous reader)
                continue
            off = self._zst_pos.get(key, st.off) if st.gen is not None else st.off
            out.append([key, off, False])
        return out

    def state(self) -> dict[str, Any]:
        return {
            "start_ms": self.start_ms, "now_ms": self.now_ms, "last_sweep": self._last_sweep, "evict_idle_ms": self.evict_idle_ms,
            "pools": [[k, _pool_state(p)] for k, p in self._pools.items()],
            "by_mint": [[m, sorted(s)] for m, s in self._by_mint.items()],
            "spans": [[list(k), v] for k, v in self._spans.items()],
            "gaps_total": [[k, v] for k, v in self.gaps_total.items()],
            "loss_spans": [[k, v] for k, v in self.loss_spans.items()],
            "last_hb": [[k, v] for k, v in self.last_hb.items()],
            "counters": [[k, v] for k, v in self.counters.items()],
            "cursor": self._cursor(),
            "buf": list(self._buf),
        }

    def state_digest(self) -> str:
        return _sha(_canon(self.state()))

    def checkpoint(self, path: str | os.PathLike, **meta: Any) -> dict[str, Any]:
        body = _canon(self.state())
        digest = _sha(body)
        env = {"schema": SCHEMA, "tape_dir": str(self.dir), "meta": meta, "state_digest": digest}
        data = _canon(env)[:-1] + b',"state":' + body + b"}\n"
        _atomic_write(Path(path), data)
        return {"sha256": _sha(data), "state_digest": digest, "bytes": len(data), "keys": len(self._pools), "buffered_rows": len(self._buf)}

    def restore(self, path: str | os.PathLike, expect_sha256: str | None = None) -> str:
        if self._pools or self._hours or self._buf or self.now_ms:
            raise CheckpointInvalid("restore into a state that has already read rows")
        try:
            data = Path(path).read_bytes()
            env = json.loads(data)
        except (OSError, ValueError) as exc:
            raise CheckpointInvalid(f"unreadable checkpoint: {type(exc).__name__}") from exc
        if expect_sha256 is not None and _sha(data) != expect_sha256:
            raise CheckpointInvalid("checkpoint sha256 differs from the recorded one")
        if env.get("schema") != SCHEMA:
            raise CheckpointInvalid(f"schema {env.get('schema')!r}")
        if env.get("tape_dir") != str(self.dir):
            raise CheckpointInvalid("the checkpoint follows another tape dir")
        st = env.get("state")
        if not isinstance(st, dict) or _sha(_canon(st)) != env.get("state_digest"):
            raise CheckpointInvalid("state digest mismatch (the file was changed)")
        self.start_ms, self.now_ms, self._last_sweep, self.evict_idle_ms = st["start_ms"], st["now_ms"], st["last_sweep"], st["evict_idle_ms"]
        self._pools = {k: _pool_restore(d) for k, d in st["pools"]}
        self._by_mint = {m: set(s) for m, s in st["by_mint"]}
        self._spans = {tuple(k): v for k, v in st["spans"]}
        self.gaps_total = collections.Counter({k: v for k, v in st["gaps_total"]})
        self.loss_spans = collections.Counter({k: v for k, v in st["loss_spans"]})
        self.last_hb = {k: v for k, v in st["last_hb"]}
        self.counters = collections.Counter({k: v for k, v in st["counters"]})
        self._hours = {}
        for key, off, done in st["cursor"]:
            h = _Hour()
            h.off, h.done = (0 if off is None else int(off)), bool(done)
            self._hours[key] = h
        self._zst_pos = {}
        self._buf = collections.deque(st["buf"])
        got = self.state_digest()
        if got != env["state_digest"]:
            raise CheckpointInvalid("restored state does not reproduce the digest")
        self.restored_from = {"state_digest": got, "sha256": _sha(data), "meta": env.get("meta")}
        return got


def main(argv: list[str] | None = None) -> int:
    """`digest <ckpt> --tape-dir D`: restore an ArenaState checkpoint in THIS (fresh) process and print its state digest."""
    import argparse

    ap = argparse.ArgumentParser(description="restore an ArenaState checkpoint in a fresh process and print the digest")
    ap.add_argument("mode", choices=["digest"])
    ap.add_argument("ckpt")
    ap.add_argument("--tape-dir", required=True)
    args = ap.parse_args(argv)
    st = StateCkpt(args.tape_dir, start_ms=0)
    try:
        d = st.restore(args.ckpt)
    except CheckpointInvalid as exc:
        print(json.dumps({"refused": str(exc)}))
        return 3
    print(json.dumps({"state_digest": d, "keys": len(st)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
