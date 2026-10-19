"""arena_declog: the hash-chained, flock'd, fsync'd decision log of the arena rule-bot harness (DEC-031 section 1.2). PAPER only. Pure stdlib plus
`tools.arena_paper.canon`; no model, no network, no key.

`GENESIS`, `LogChainError` and `DecisionLog` are copied BYTE FOR BYTE from `tools/hv4_runner.py` (that module is a frozen book's code and pulls model
code, so it is not imported here). `tools/test_arena_rules_declog.py` proves the copied source text is identical to hv4_runner's, so a log written by
this module and one written by HV4 verify with the same chain rule. `sha256_hex` and `utc_day` are the two helpers the copied class calls; they are
copied from `tools/hv4_prompts.py` and `tools/hv4_budget.py` for the same reason (the test checks their behaviour against those originals).

One file per bot and UTC day, `<root>/decisions/<bot_id>/<day>.jsonl`. Every row carries seq, prev and sha (sha256 of prev + the canonical row without
sha). A decision row is appended BEFORE the order it describes is sent, so before any fill is priced.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from tools.arena_paper import canon


def sha256_hex(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def utc_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


GENESIS = "0" * 64


class LogChainError(RuntimeError):
    """The decision log's hash chain is broken (or a line is torn). The runner refuses to start."""


class DecisionLog:
    def __init__(self, root: str | os.PathLike) -> None:
        self.dir = Path(root) / "decisions"
        self._head: dict[str, tuple[str, int]] = {}

    def files(self, name: str) -> list[Path]:
        d = self.dir / name
        return sorted(d.glob("*.jsonl")) if d.exists() else []

    def rows(self, name: str) -> list[dict]:
        out = []
        for f in self.files(name):
            for line in f.read_bytes().splitlines():
                if line.strip():
                    out.append(json.loads(line))
        return out

    def verify(self, name: str) -> tuple[str, int]:
        prev, seq = GENESIS, -1
        for f in self.files(name):
            data = f.read_bytes()
            if data and not data.endswith(b"\n"):
                raise LogChainError(f"{name}: torn last line in {f.name}")
            for line in data.splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    sha = row.pop("sha")
                except (ValueError, KeyError):
                    raise LogChainError(f"{name}: unreadable row in {f.name}") from None
                if row.get("prev") != prev or row.get("seq") != seq + 1 or sha256_hex(prev + canon(row)) != sha:
                    raise LogChainError(f"{name}: chain broken at seq {seq + 1} in {f.name}")
                prev, seq = sha, row["seq"]
        self._head[name] = (prev, seq)
        return prev, seq

    def head(self, name: str) -> tuple[str, int]:
        return self._head.get(name) or self.verify(name)

    def append(self, name: str, ms: int, body: Mapping[str, Any]) -> dict:
        prev, seq = self.head(name)
        row = {"seq": seq + 1, "arm": name, "ms": int(ms), "prev": prev, **body}
        row["sha"] = sha256_hex(prev + canon(row))
        path = self.dir / name / f"{utc_day(ms)}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, canon(row).encode() + b"\n")
            os.fsync(fd)
        finally:
            os.close(fd)
        self._head[name] = (row["sha"], row["seq"])
        return row

    def md5(self) -> str:
        h = hashlib.md5()
        for d in sorted(p for p in self.dir.iterdir() if p.is_dir()) if self.dir.exists() else []:
            for f in sorted(d.glob("*.jsonl")):
                h.update(f.name.encode() + b"\0" + f.read_bytes())
        return h.hexdigest()

    def day_sha256(self, name: str, day: str) -> str | None:
        f = self.dir / name / f"{day}.jsonl"
        return hashlib.sha256(f.read_bytes()).hexdigest() if f.exists() else None
