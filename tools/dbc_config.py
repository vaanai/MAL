"""DBC PoolConfig / ConfigWithTransferHook account decoder from the IDL (bytemuck, sequential fields, explicit padding). Paper/research only."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from tools import dbc_decode as dd

IDL = Path(__file__).parent / "data" / "meteora_dbc_idl_v0.2.2.json"
_T: dict[str, Any] | None = None
_ACC: dict[bytes, str] = {}


def _load() -> None:
    global _T
    if _T is None:
        d = json.loads(IDL.read_text())
        _T = {t["name"]: t for t in d["types"]}
        for a in d["accounts"]:
            _ACC[bytes(a["discriminator"])] = a["name"]


_PRIM = {"u8": 1, "u16": 2, "u32": 4, "u64": 8, "u128": 16, "pubkey": 32, "i64": 8, "i32": 4, "bool": 1}


def _dec(t: Any, b: bytes, off: int) -> tuple[Any, int]:
    if isinstance(t, str):
        n = _PRIM[t]
        raw = b[off:off + n]
        if len(raw) < n:
            raise ValueError("short")
        return (dd.b58(raw) if t == "pubkey" else int.from_bytes(raw, "little", signed=t.startswith("i"))), off + n
    if "array" in t:
        et, n = t["array"]
        out = []
        for _ in range(n):
            v, off = _dec(et, b, off)
            out.append(v)
        return out, off
    name = t["defined"]["name"]
    out = {}
    for f in _T[name]["type"]["fields"]:  # type: ignore[index]
        out[f["name"]], off = _dec(f["type"], b, off)
    return out, off


def decode_account(data: bytes) -> dict[str, Any]:
    """-> {'kind': 'PoolConfig'|'ConfigWithTransferHook', 'config': {...}, 'transfer_hook_program'?}. Raises ValueError on unknown discriminator."""
    _load()
    kind = _ACC.get(bytes(data[:8]))
    if kind not in ("PoolConfig", "ConfigWithTransferHook"):
        raise ValueError(f"not a config account: {kind}")
    body = data[8:]
    if kind == "PoolConfig":
        c, _ = _dec({"defined": {"name": "PoolConfig"}}, body, 0)
        return {"kind": kind, "config": c}
    r, _ = _dec({"defined": {"name": "ConfigWithTransferHook"}}, body, 0)
    return {"kind": kind, "config": r["config"], "transfer_hook_program": r["transfer_hook_program"]}


def decode_b64(s: str) -> dict[str, Any]:
    return decode_account(base64.b64decode(s))


def curve_points(cfg: dict[str, Any]) -> list[tuple[int, int]]:
    """[(sqrt_price_upper, liquidity)] for the used segments (sqrt_price > 0). Segment i spans (prev upper, upper]; the first starts at sqrt_start_price."""
    return [(p["sqrt_price"], p["liquidity"]) for p in cfg["curve"] if p["sqrt_price"] > 0]
