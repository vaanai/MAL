#!/usr/bin/env python3
"""BOOST early warning for the Q50 book (library rows w2-pump-idl-1 and w2-pump-idl-2). Paper/ops only; no key, no websocket.

The Q50 book (frozen H5 BOOST-floor rule) depends on pump's BOOST buy-and-burn being live. This tool polls public RPC (getAccountInfo,
getSignaturesForAddress, getTransaction) and watches:
  1. the PumpSwap GlobalConfig account: boost_enabled (byte 939), boost_authority (907), admin (bytes 8..40) and the sha256 of the whole account;
  2. new signatures on the config account, the program admin and the boost authority. Every new transaction is fetched and scanned (top-level and
     inner instructions) for PumpSwap instructions with a watched discriminator (toggle_boost, set_boost_authority, update_admin, update_fee_config,
     disable, ...). toggle_boost emits no event, so the instruction discriminator is the only trace besides the account bytes.

One status line (JSON) per poll goes to ~/mal-data/boost-watch/boost_watch.log (override --log-dir). State (baseline, seen signatures) is in
state.json beside it, so a restart does not re-alert on old signatures and does not lose a change that happened while it was down.

EXIT CODES: 0 nothing changed; 3 CHANGE (boost_enabled is not true, layout/hash/admin/authority changed vs baseline, a watched instruction, or any new
write on the config account); 4 reads failing --max-fail polls in a row (blind, NOT a change); 2 usage.
The first run with no state records a baseline (exit 0) unless boost_enabled is already not true (exit 3).
After exit 3 the alert stays latched in state.json: every later run exits 3 again until a human runs `--ack` (which also drops the baseline, so the
next poll re-baselines on the new state).

NOT SEEN: pump (bonding-curve) program changes and the BOOST end of a single pool (tools/pump_structure_monitor.py covers program redeploys);
a toggle sent by an address other than admin/boost_authority is still visible through its write to the config account.
Any config-account write is an alert, including harmless fee updates: the owner decides, the tool does not classify.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from tools import pump_structure_monitor as m

SCHEMA = "boost_watch.v1"
DEFAULT_LOG_DIR = "~/mal-data/boost-watch"
DEFAULT_IDL = "/data/mal/hunt-1008/swarm-1013/w2-pump-idl/raw/idl_pump_amm.json"
CONFIG_ADDRESS = m.PUMPSWAP_GLOBAL_CONFIG
PROGRAM = m.PUMPSWAP_PROGRAM
ADMIN_OFFSET = 8  # after the 8-byte Anchor discriminator (IDL GlobalConfig.admin)
GC_DISC = bytes([149, 8, 156, 202, 160, 252, 176, 217])
# pinned from idl_pump_amm.json; load_idl_discriminators refuses to run if the IDL disagrees
PINNED = {
    bytes([117, 161, 160, 74, 223, 137, 118, 99]): "toggle_boost",
    bytes([227, 149, 76, 42, 130, 39, 234, 205]): "set_boost_authority",
}
WATCHED_NAMES = ("toggle_boost", "set_boost_authority", "update_admin", "update_fee_config", "update_buyback_config", "disable",
                 "toggle_mayhem_mode", "toggle_cashback_enabled", "update_creator_fee_config", "create_config", "admin_update_token_incentives")
SIG_LIMIT = 25


def load_idl_discriminators(path: str | os.PathLike) -> dict[bytes, str]:
    idl = json.loads(Path(path).read_text())
    out = {bytes(i["discriminator"]): i["name"] for i in idl["instructions"] if i["name"] in WATCHED_NAMES}
    for d, n in PINNED.items():
        if out.get(d) != n:
            raise ValueError(f"IDL disagrees with pinned discriminator for {n}")
    return out


def decode_config(data: bytes) -> dict[str, Any]:
    ok = len(data) == m.GLOBAL_CONFIG_LEN and data[:8] == GC_DISC
    g = m.decode_global_config(data)
    return {
        "layout_ok": ok, "len": len(data), "sha256": m.sha256_hex(data),
        "boost_enabled": g["boost_enabled"] if ok else None,
        "boost_authority": g["boost_authority"] if ok else None,
        "admin": m.b58encode(data[ADMIN_OFFSET:ADMIN_OFFSET + 32]) if ok else None,
    }


def scan_tx(tx: Mapping[str, Any] | None, watched: Mapping[bytes, str]) -> list[dict[str, Any]]:
    """Watched PumpSwap instructions in one getTransaction result (encoding json), top-level and inner."""
    if not tx:
        return []
    msg = tx["transaction"]["message"]
    meta = tx.get("meta") or {}
    loaded = meta.get("loadedAddresses") or {}
    keys = list(msg["accountKeys"]) + list(loaded.get("writable", [])) + list(loaded.get("readonly", []))
    ixs = list(msg["instructions"])
    for inner in meta.get("innerInstructions") or []:
        ixs += inner.get("instructions", [])
    found = []
    for ix in ixs:
        if keys[ix["programIdIndex"]] != PROGRAM:
            continue
        raw = m.b58decode(ix["data"])
        name = watched.get(raw[:8])
        if name:
            row: dict[str, Any] = {"ix": name, "failed": bool(meta.get("err"))}
            if name == "toggle_boost" and len(raw) >= 9:
                row["enabled"] = raw[8] == 1
            found.append(row)
    return found


def new_sigs(sigs: list[Mapping[str, Any]], seen: list[str]) -> list[str]:
    """Signatures (oldest first) from getSignaturesForAddress (newest first) not yet in `seen`."""
    s = set(seen)
    return [x["signature"] for x in reversed(sigs) if x["signature"] not in s]


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def poll(client: Any, state: Mapping[str, Any], watched: Mapping[bytes, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """One poll -> (status line, new state). Raises on RPC failure. `state` is not mutated."""
    st = json.loads(json.dumps(state))
    res = client.call("getAccountInfo", [CONFIG_ADDRESS, {"encoding": "base64", "commitment": "finalized"}])
    value = (res or {}).get("value")
    if not value:
        raise m.RpcError("global config account missing")
    cfg = decode_config(base64.b64decode(value["data"][0]))
    base = st.get("baseline")
    reasons: list[str] = []
    if st.get("alert"):
        reasons.append("unacked_alert")
    if not cfg["layout_ok"]:
        reasons.append("layout_changed")
    if cfg["boost_enabled"] is not True:
        reasons.append("boost_enabled_not_true")
    if base:
        for k in ("sha256", "admin", "boost_authority"):
            if cfg[k] != base[k]:
                reasons.append(f"{k}_changed")
    addrs = {"config": CONFIG_ADDRESS}
    for k in ("admin", "boost_authority"):
        if cfg[k]:
            addrs[k] = cfg[k]
    new_txs: list[dict[str, Any]] = []
    seen = st.setdefault("seen", {})
    for label, addr in addrs.items():
        sigs = client.call("getSignaturesForAddress", [addr, {"limit": SIG_LIMIT, "commitment": "finalized"}]) or []
        if addr not in seen:  # first sight of an address: record history, do not alert on it
            seen[addr] = [x["signature"] for x in sigs]
            continue
        for sig in new_sigs(sigs, seen[addr]):
            tx = client.call("getTransaction", [sig, {"encoding": "json", "commitment": "finalized", "maxSupportedTransactionVersion": 0}])
            if tx is None:
                break  # not retrievable yet: stays unseen, retried next poll (order kept)
            hits = scan_tx(tx, watched)
            new_txs.append({"addr": label, "sig": sig, "slot": tx.get("slot"), "hits": hits})
            seen[addr] = (seen[addr] + [sig])[-200:]
            if hits:
                reasons.append("watched_ix:" + "+".join(sorted({h["ix"] for h in hits})))
            elif label == "config":
                reasons.append("config_account_tx")
    if not base:
        st["baseline"] = {k: cfg[k] for k in ("sha256", "admin", "boost_authority", "boost_enabled")}
    change = sorted(set(reasons))
    if change:
        st["alert"] = change
    line = {"schema": SCHEMA, "utc": now_utc(), "boost_enabled": cfg["boost_enabled"], "config_sha256": cfg["sha256"], "admin": cfg["admin"],
            "boost_authority": cfg["boost_authority"], "layout_ok": cfg["layout_ok"], "baseline": "kept" if base else "set",
            "new_txs": new_txs, "change": change, "status": "CHANGE" if change else "OK"}
    return line, st


def run(argv: list[str] | None = None, *, client: Any | None = None, sleep: Callable[[float], None] = time.sleep) -> int:
    ap = argparse.ArgumentParser(description="BOOST-off early warning (Q50). Exit 3 on a change.")
    ap.add_argument("--rpc", default=os.environ.get("BOOST_WATCH_RPC", m.DEFAULT_RPC))
    ap.add_argument("--log-dir", default=DEFAULT_LOG_DIR)
    ap.add_argument("--idl", default=DEFAULT_IDL)
    ap.add_argument("--loop", action="store_true", help="poll every --interval s until exit 3 or 4")
    ap.add_argument("--interval", type=float, default=60.0)
    ap.add_argument("--max-fail", type=int, default=10)
    ap.add_argument("--max-polls", type=int, default=0, help="bound for tests; 0 = unbounded")
    ap.add_argument("--ack", action="store_true", help="human acknowledgement: clear the latched alert and baseline; does not poll")
    a = ap.parse_args(argv)
    d = Path(a.log_dir).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    sp, lp = d / "state.json", d / "boost_watch.log"
    if a.ack:
        s = json.loads(sp.read_text()) if sp.exists() else {}
        s.pop("baseline", None)
        s.pop("alert", None)
        sp.write_text(json.dumps(s))
        print("alert and baseline cleared; the next poll re-baselines")
        return 0
    watched = load_idl_discriminators(a.idl)
    client = client or m.RpcClient(a.rpc, min_interval=1.0, max_calls=10**9)
    fails = n = 0
    while True:
        n += 1
        state = json.loads(sp.read_text()) if sp.exists() else {}
        try:
            line, new_state = poll(client, state, watched)
        except (*m.ITEM_ERRORS, ValueError, KeyError, IndexError, TypeError) as exc:
            fails += 1
            line, new_state = {"schema": SCHEMA, "utc": now_utc(), "status": "READ_FAIL", "error": type(exc).__name__, "consecutive_fail": fails}, None
        else:
            fails = 0
        with lp.open("a") as f:
            f.write(json.dumps(line) + "\n")
        if new_state is not None:
            sp.write_text(json.dumps(new_state))
        if line["status"] == "CHANGE":
            print(json.dumps(line))
            return 3
        if fails >= a.max_fail:
            print(json.dumps(line))
            return 4
        if not a.loop or (a.max_polls and n >= a.max_polls):
            return 0
        sleep(a.interval)


if __name__ == "__main__":
    sys.exit(run())
