"""Second create source for tools/meta_capture.py: pump.fun create rows from the arena tape.

Why: the PumpPortal socket returned HTTP 502 from 2026-10-25 ~11:45Z. The arena feed (tools/arena_feed.py) keeps writing
hourly tape-<YYYY-MM-DDTHH>.jsonl files; its `create` rows carry mint, signature, creator, name, symbol but NOT the metadata
URI (the feed drops it). The URI is therefore resolved from the create transaction: getTransaction(signature) -> the pump
CreateEvent in the logs -> its uri string. That costs one RPC call per new mint (the RPC URL comes from an env var NAME the
caller passes; the URL itself is never logged or printed).

Reads only: mint, signature, creator, name, symbol, t_recv_ms of `create`/`pumpfun` rows. No trade, reserve or other row
content is used beyond the type/prog filter.
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterator

from tools.pump_history_backfill import _CREATE_DISC, _read_string

log = logging.getLogger("mal.meta_arena")


def hour_files(tape_dir: Path, hours: list[str] | None = None) -> list[Path]:
    """Plain tape-<hour>.jsonl files (sorted); with `hours`, only those hours, and .jsonl.zst is accepted too."""
    out: list[Path] = []
    for p in sorted(tape_dir.glob("tape-*.jsonl*")):
        name = p.name
        if name.endswith(".tmp"):
            continue
        hk = name[len("tape-"):len("tape-") + 13]
        if hours is not None:
            if hk not in hours:
                continue
        elif not name.endswith(".jsonl"):
            continue
        out.append(p)
    return out


def create_payload(line: str) -> tuple[dict[str, Any], int] | None:
    """One tape line -> (PumpPortal-shaped create payload without uri, t_recv_ms), or None for any other row."""
    try:
        r = json.loads(line)
    except ValueError:
        return None
    if not isinstance(r, dict) or r.get("type") != "create" or r.get("prog") != "pumpfun":
        return None
    mint, t = r.get("mint"), r.get("t_recv_ms")
    if not isinstance(mint, str) or not mint or not isinstance(t, int):
        return None
    return ({"txType": "create", "mint": mint, "signature": r.get("signature"), "name": r.get("name"),
             "symbol": r.get("symbol"), "traderPublicKey": r.get("creator"), "slot": r.get("slot")}, t)


def iter_creates(path: Path) -> Iterator[tuple[dict[str, Any], int]]:
    """Create rows of one tape file (plain or .zst). A torn last line is skipped."""
    if path.name.endswith(".zst"):
        from tools.arena_zst import open_zst
        rd = open_zst(path)
        try:
            buf = b""
            while True:
                chunk = rd.read()
                if not chunk:
                    break
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for ln in lines:
                    got = create_payload(ln.decode("utf-8", errors="replace"))
                    if got:
                        yield got
        finally:
            rd.close()
        return
    with path.open("rb") as fh:
        for ln in fh:
            if not ln.endswith(b"\n"):
                break  # still being written
            got = create_payload(ln.decode("utf-8", errors="replace"))
            if got:
                yield got


class Tailer:
    """Tails the newest plain hour files. Keeps a byte offset per file; reads whole lines only."""

    def __init__(self, tape_dir: Path, newest: int = 2, start_ms: int | None = None) -> None:
        self.dir, self.newest, self.start_ms = tape_dir, newest, start_ms
        self.offsets: dict[str, int] = {}
        self.primed = False

    def poll(self) -> list[tuple[dict[str, Any], int]]:
        """New create rows since the last poll. Without start_ms the first poll starts at the END of the existing files,
        so a restart does not replay an hour of old creates; with start_ms rows from that instant on are returned."""
        files = hour_files(self.dir)[-self.newest:]
        out: list[tuple[dict[str, Any], int]] = []
        for p in files:
            key = p.name
            size = p.stat().st_size
            if key not in self.offsets and not self.primed and self.start_ms is None:
                self.offsets[key] = size
                continue
            pos = self.offsets.get(key, 0)
            if size < pos:
                pos = 0
            with p.open("rb") as fh:
                fh.seek(pos)
                data = fh.read()
            end = data.rfind(b"\n")
            if end < 0:
                self.offsets[key] = pos
                continue
            self.offsets[key] = pos + end + 1
            for ln in data[:end].split(b"\n"):
                got = create_payload(ln.decode("utf-8", errors="replace"))
                if got and (self.start_ms is None or got[1] >= self.start_ms):
                    out.append(got)
        self.primed = True
        return out


def uri_from_logs(logs: list[str]) -> str | None:
    """The metadata URI of the pump CreateEvent in a transaction's log messages."""
    for ln in logs:
        if "Program data: " not in ln:
            continue
        try:
            raw = base64.b64decode(ln.split("Program data: ", 1)[1].strip())
        except ValueError:
            continue
        if raw[:8] != _CREATE_DISC:
            continue
        _name, off = _read_string(raw, 8)
        _sym, off = _read_string(raw, off)
        uri, _ = _read_string(raw, off, limit=512)
        return uri
    return None


def make_rpc_resolver(rpc_url: str, timeout: float = 15.0) -> Callable[[str], str | None]:
    """Blocking resolver: signature -> uri via getTransaction. Raises on transport errors (counted by the caller).
    The URL is never logged."""
    def resolve(signature: str) -> str | None:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getTransaction", "params": [
            signature, {"encoding": "json", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}]}).encode()
        req = urllib.request.Request(rpc_url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - https URL from the operator's env
            doc = json.loads(resp.read(4_000_000))
        res = doc.get("result") if isinstance(doc, dict) else None
        logs = ((res or {}).get("meta") or {}).get("logMessages") or []
        return uri_from_logs(logs)
    return resolve
