#!/usr/bin/env python3
"""A3 automatic redeploy check (QP ruling 2026-10-21, Q3 R2-R5; DEC-031 amendment pending). Paper only. No credential is read, no outcome is touched.

WHAT IT DECIDES. It reads the NEWEST line of the A3 daily record (tools/pump_structure_monitor.py output) and sorts it into one of three outcomes:
  NONE   no A3 halt flag in the line.
  PASS   the line's pins_changed covers ONLY the pump and/or PumpSwap PROGRAM deploy slots, every other flag is clear and evaluated, and every check below holds.
         Record-only: the redeploy is disclosed (slot, UTC, check job id) and the counted window continues.
  HALT   anything else. The verdict carries the halt instant (epoch ms):
           - the REDEPLOY instant (the earliest changed deploy_utc) when the redeploy check FAILS, ERRORS or TIMES OUT (a fixed rule, no judgment);
           - the flag's instant (the run's run_utc, as under the old rule) for every always-hard item: a fee / global config account hash change, a fees-program
             redeploy, any of boost_disabled / boost_share_low / boost_last_slice_early / boost_budget_or_slices_changed, synthetic_share_high, any other
             halt flag, or a core rule unevaluated on two consecutive daily runs.
There is NO override path: no flag, no environment variable and no file edit turns a FAIL into a PASS. The only memory is the append-only STATE file, which records the
verdict per (program, deploy slot); a PASSed slot is covered afterwards (one deploy is never classified twice, R5) and a FAILed slot stays failed.

THE CHECK (all must hold, else FAIL; a missing pin or a failed fetch is ERROR; both halt):
  a. the other five A3 flags (the four BOOST flags and synthetic_share_high) are clear AND evaluated;
  b. bonding_fee_config, pumpswap_fee_config and pumpswap_global_config sha256 equal the monitor pins (re-checked here from the record), fees deploy slot equal;
  c. decoder parity on the pinned SAMPLE RULE (constants below): the first SAMPLE_MIN_TXS successful txs that invoke the pump or PumpSwap program, in slot order, scanning
     from (newest changed deploy slot + SAMPLE_START_OFFSET_SLOTS). Events are the `Program data:` lines whose emitting program (log stack top) is pump or PumpSwap. Decoded
     with the SAME functions the H5 shadow uses (observe.trade_decode.decode_program_data(raw, event_v=True) and decode_extra_event, imported, never copied). Pass bar:
     0 undecodable events of a discriminator the shadow decodes, 0 unknown discriminators (not in observe.trade_decode.KNOWN_DISCS), and field-by-field equality of every
     decoded Trade / Buy / Sell / CreatePool / BOOST event with an INDEPENDENT decode driven by the IDL's own field list (offsets computed from the IDL, not from the shadow);
  d. the discriminators seen are a subset of the known set, TradeEvent / BuyEvent / SellEvent were each seen, and each discriminator's byte length lies inside the pinned
     [min_len, max_len] (a_3 redeploy pins file);
  e. BOOST: the daily record's InitBoost share of non-mayhem WSOL graduations, the median budget and the median slice count are each within +/-BOOST_TOL (20%) of the pins,
     and at least the pinned minimum of InitBoost events is decoded in the sample;
  f. IDL unchanged: sha256 of each pinned IDL URL (the MAIN branch of github.com/pump-fun/pump-public-docs, unauthenticated, fetched at run time) equals the pin.
     This is the credential-free route. The alternative (programdata-derived discriminator table) would need the program's Anchor IDL account or a decompile; the sample's
     discriminator set in (d) is the on-chain half of the same question. A fetch failure is ERROR, never a pass.
  g. the whole check finishes within CHECK_WINDOW_S (24 h) of the earliest changed redeploy instant; later is a FAIL (timeout).

PINS STILL TO BE FILLED (null in tools/a3_redeploy_pins.json => ERROR => hard halt; fail closed): IDL sha256 x3, per-discriminator length ranges, InitBoost share, the
minimum InitBoost events in the sample. `--observe` prints the observed values for the program-upgrade review (C2/C3) to pin; it writes no pin and decides nothing.

Exit codes: 0 NONE or PASS; 10 HALT (verdict.halt_at_ms is set); 11 operational error that is NOT an A3 flag (stale or unreadable daily record): no halt, the job is loud.
Counts and discriminators only: no price, mint, wallet or signature is written.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from observe import trade_decode as td
from tools import pump_structure_monitor as mon

SCHEMA = "a3_redeploy_check.v1"
DEFAULT_PINS = Path(__file__).with_name("a3_redeploy_pins.json")
DEFAULT_DAILY = "/data/mal/structure-monitor/daily.jsonl"

# ----- pinned rule constants (changing any of them changes the pinned tool blob) -----
CHECK_WINDOW_S = 24 * 3600  # the check must finish within this long of the earliest changed redeploy instant
SAMPLE_START_OFFSET_SLOTS = 150  # sample starts this many slots after the newest changed deploy slot
SAMPLE_MIN_TXS = 500  # first N successful txs that invoke the pump or PumpSwap program
SAMPLE_MAX_SLOTS = 300  # slots scanned at most (skipped slots included); short of SAMPLE_MIN_TXS by then => FAIL
SAMPLE_PROGRAMS = (td.PUMP_BONDING_PROGRAM, td.PUMPSWAP_PROGRAM)
BOOST_TOL = mon.BOOST_CHANGE_MAX  # 0.20
MAX_RECORD_AGE_S = 1800  # a newer daily line than this is "stale" (exit 11, no halt)
IDL_URL = "https://raw.githubusercontent.com/pump-fun/pump-public-docs/main/idl/{name}.json"
IDL_NAMES = ("pump", "pump_amm", "pump_fees")

PROGRAM_FLAG_ITEMS = ("pump", "pumpswap")  # the only items whose deploy-slot change may start the check
BOOST_FLAGS = ("boost_disabled", "boost_share_low", "boost_last_slice_early", "boost_budget_or_slices_changed")
OTHER_FIVE = BOOST_FLAGS + ("synthetic_share_high",)
ALL_HALT_FLAGS = ("pins_changed",) + OTHER_FIVE
REQUIRED_EVENTS = ("TradeEvent", "BuyEvent", "SellEvent")

# IDL event -> {IDL field: key in the shadow decoder's row}. "side" is handled apart (is_buy). Checked against the live IDL on 2026-10-21 (see the PR body).
EVENT_FIELDS: dict[str, dict[str, str]] = {
    "TradeEvent": {"mint": "mint", "sol_amount": "sol_lamports", "token_amount": "token_raw", "user": "trader", "timestamp": "event_ts",
                   "virtual_sol_reserves": "quote_reserve", "virtual_token_reserves": "base_reserve", "ix_name": "ix_name"},
    "BuyEvent": {"timestamp": "event_ts", "base_amount_out": "token_raw", "pool_base_token_reserves": "base_reserve", "pool_quote_token_reserves": "quote_reserve",
                 "quote_amount_in": "pool_quote_amount", "lp_fee": "lp_fee", "protocol_fee": "protocol_fee", "user_quote_amount_in": "sol_lamports", "pool": "pool",
                 "user": "trader", "coin_creator_fee": "creator_fee", "ix_name": "ix_name", "buyback_fee": "buyback_fee", "virtual_quote_reserves": "virtual_quote_reserves",
                 "creator_fee_unclaimed": "creator_fee_unclaimed"},
    "SellEvent": {"timestamp": "event_ts", "base_amount_in": "token_raw", "pool_base_token_reserves": "base_reserve", "pool_quote_token_reserves": "quote_reserve",
                  "quote_amount_out": "pool_quote_amount", "lp_fee": "lp_fee", "protocol_fee": "protocol_fee", "user_quote_amount_out": "sol_lamports", "pool": "pool",
                  "user": "trader", "coin_creator_fee": "creator_fee", "base_supply": "supply_raw", "buyback_fee": "buyback_fee",
                  "virtual_quote_reserves": "virtual_quote_reserves", "creator_fee_unclaimed": "creator_fee_unclaimed"},
    "CreatePoolEvent": {"pool": "pool", "base_mint": "base_mint", "quote_mint": "quote_mint"},
    "InitBoostEvent": {"timestamp": "event_ts", "mint": "mint", "bonding_curve": "bonding_curve", "pool": "pool", "virtual_quote_reserves": "virtual_quote_reserves",
                       "real_quote_reserves_after": "real_quote_reserves_after"},
    "BoostBuyAndBurnEvent": {"timestamp": "event_ts", "mint": "mint", "bonding_curve": "bonding_curve", "pool": "pool", "authority": "authority",
                             "quote_amount_in_requested": "quote_amount_in_requested", "quote_amount_in_used": "quote_amount_in_used",
                             "base_amount_burned": "base_amount_burned", "virtual_quote_reserves": "virtual_quote_reserves",
                             "real_quote_reserves_after": "real_quote_reserves_after", "base_reserves_after": "base_reserves_after",
                             "boost_vault_remaining": "boost_vault_remaining"},
    "PostCompleteBuyEvent": {"user": "trader", "mint": "mint", "bonding_curve": "bonding_curve", "quote_mint": "quote_mint", "timestamp": "event_ts", "base_out": "base_out",
                             "quote_in": "quote_in", "fee": "fee", "creator_fee": "creator_fee", "buyback_fee": "buyback_fee",
                             "pool_base_reserves_before": "pool_base_reserves_before", "pool_quote_reserves_before": "pool_quote_reserves_before",
                             "pool_base_reserves_after": "pool_base_reserves_after", "pool_quote_reserves_after": "pool_quote_reserves_after"},
}
SHADOW_DECODED_DISCS = {  # discriminators the shadow decodes: the pinned `read` set
    td._TRADE_DISC, td._BUY_DISC, td._SELL_DISC, td._CREATE_POOL_DISC, td._POST_COMPLETE_BUY_DISC, td._BOOST_BUY_AND_BURN_DISC,
    td._SWEEP_POOL_FEE_DISC, td._SWEEP_CURVE_FEE_DISC, td._INIT_BOOST_EVENT_DISC,
}
INIT_BOOST_DISC = td._INIT_BOOST_EVENT_DISC

Json = Mapping[str, Any]


class CheckError(Exception):
    """Something the check needed was missing or unreadable: verdict ERROR (hard halt), never a pass."""


def utc_ms(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)


def iso_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------------------------------------------------------------------ triage
def pins_diff(rec: Json, pins: Json) -> list[dict[str, Any]]:
    """Structured pins_changed items (the monitor's compare_pins, but typed): kind program|account, name, old, new, deploy_utc."""
    out: list[dict[str, Any]] = []
    for name, obs in (rec.get("accounts") or {}).items():
        if not obs.get("pinned"):
            continue
        want = ((pins.get("accounts") or {}).get(name) or {}).get("sha256")
        got = obs.get("sha256")
        if want is not None and got is not None and got != want:
            out.append({"kind": "account", "name": name, "old": want, "new": got})
    for name, obs in (rec.get("programs") or {}).items():
        want = ((pins.get("programs") or {}).get(name) or {}).get("deploy_slot")
        got = obs.get("deploy_slot")
        if want is not None and got is not None and got != want:
            out.append({"kind": "program", "name": name, "old": want, "new": got, "deploy_utc": obs.get("deploy_utc")})
    return out


def triage(rec: Json, pins: Json, prev: Json | None) -> dict[str, Any]:
    """Pure. Returns {action: none|redeploy|hard, run_ms, flag_halt_ms, hard_reasons, redeploys}.
    flag_halt_ms is set whenever ANY A3 halt flag is true (the OLD rule's instant, run_utc): books still under the old rule halt there."""
    flags = ((rec.get("halt") or {}).get("flags")) or {}
    run_ms = utc_ms(rec["run_utc"])
    any_halt = any(v.get("halt") for v in flags.values() if isinstance(v, Mapping))
    out: dict[str, Any] = {"run_utc": rec["run_utc"], "run_ms": run_ms, "flag_halt_ms": run_ms if any_halt else None, "hard_reasons": [], "redeploys": [], "action": "none"}
    hard = out["hard_reasons"]
    for k in BOOST_FLAGS + ("synthetic_share_high",):
        if (flags.get(k) or {}).get("halt"):
            hard.append(f"{k}: {(flags[k] or {}).get('reason')}")
    for k, v in flags.items():
        if k not in ALL_HALT_FLAGS and isinstance(v, Mapping) and v.get("halt"):
            hard.append(f"{k}: {v.get('reason')}")
    # a core rule unevaluated on two consecutive daily runs
    if prev is not None:
        pflags = ((prev.get("halt") or {}).get("flags")) or {}
        for k in ALL_HALT_FLAGS:
            if flags.get(k) and pflags.get(k) and not flags[k].get("evaluated", True) and not pflags[k].get("evaluated", True):
                hard.append(f"{k}: unevaluated on two consecutive daily runs")
    pc = flags.get("pins_changed") or {}
    if pc.get("halt"):
        diff = pins_diff(rec, pins)
        redeploys = [d for d in diff if d["kind"] == "program" and d["name"] in PROGRAM_FLAG_ITEMS]
        others = [d for d in diff if d not in redeploys]
        if not diff:
            hard.append(f"pins_changed: flag set but no typed difference found ({pc.get('reason')})")
        for d in others:
            hard.append(f"pins_changed: {d['kind']} {d['name']} changed" + (f" deploy_slot {d['old']} -> {d['new']}" if d["kind"] == "program" else " (account hash)"))
        out["redeploys"] = redeploys
    if hard:
        out["action"] = "hard"
    elif out["redeploys"]:
        out["action"] = "redeploy"
    return out


def redeploy_key(d: Json) -> str:
    return f"{d['name']}:{d['new']}"


def redeploy_instant_ms(redeploys: Sequence[Json]) -> int:
    ms = []
    for d in redeploys:
        if not d.get("deploy_utc"):
            raise CheckError(f"program {d['name']} has no deploy_utc in the record")
        ms.append(utc_ms(d["deploy_utc"]))
    return min(ms)


# ------------------------------------------------------------------------------------------------------------------------------ IDL decode
_SCALARS = {"bool": 1, "u8": 1, "i8": 1, "u16": 2, "i16": 2, "u32": 4, "i32": 4, "u64": 8, "i64": 8, "u128": 16, "i128": 16, "f32": 4, "f64": 8, "pubkey": 32, "publicKey": 32}


def _idl_types(idl: Json) -> dict[str, Json]:
    return {t["name"]: t for t in idl.get("types", [])}


def _read(raw: bytes, off: int, typ: Any, types: Mapping[str, Json]) -> tuple[Any, int]:
    """Read one IDL value; returns (python value, new offset). Raises CheckError on a layout this reader does not know."""
    if isinstance(typ, str):
        n = _SCALARS.get(typ)
        if n is not None:
            if off + n > len(raw):
                raise CheckError("event shorter than its IDL layout")
            chunk = raw[off:off + n]
            if typ == "bool":
                return chunk[0] == 1, off + n
            if typ in ("pubkey", "publicKey"):
                return td.b58encode(chunk), off + n
            return int.from_bytes(chunk, "little", signed=typ.startswith("i")), off + n
        if typ == "string":
            ln = int.from_bytes(raw[off:off + 4], "little")
            end = off + 4 + ln
            if len(raw) < end:
                raise CheckError("string runs past the event")
            return raw[off + 4:end].decode("utf-8", "replace"), end
        raise CheckError(f"unknown IDL scalar type {typ!r}")
    if isinstance(typ, Mapping):
        if "vec" in typ:
            ln = int.from_bytes(raw[off:off + 4], "little")
            off += 4
            vals = []
            for _ in range(ln):
                v, off = _read(raw, off, typ["vec"], types)
                vals.append(v)
            return vals, off
        if "array" in typ:
            inner, n = typ["array"]
            vals = []
            for _ in range(int(n)):
                v, off = _read(raw, off, inner, types)
                vals.append(v)
            return vals, off
        if "option" in typ:
            if off >= len(raw):
                raise CheckError("option tag past the event")
            if raw[off] == 0:
                return None, off + 1
            return _read(raw, off + 1, typ["option"], types)
        if "defined" in typ:
            name = typ["defined"]["name"] if isinstance(typ["defined"], Mapping) else typ["defined"]
            t = types.get(name)
            if t is None or "fields" not in t.get("type", {}):
                raise CheckError(f"IDL type {name} not a struct")
            vals = {}
            for f in t["type"]["fields"]:
                vals[f["name"]], off = _read(raw, off, f["type"], types)
            return vals, off
    raise CheckError(f"unknown IDL type {typ!r}")


def idl_event_table(idls: Mapping[str, Json]) -> dict[bytes, tuple[str, list[Json], dict[str, Json]]]:
    """disc -> (event name, field list, types) over the given IDLs. A discriminator given by the IDL wins; else sha256('event:<Name>')[:8]."""
    table: dict[bytes, tuple[str, list[Json], dict[str, Json]]] = {}
    for idl in idls.values():
        types = _idl_types(idl)
        for ev in idl.get("events", []):
            t = types.get(ev["name"])
            if t is None or "fields" not in t.get("type", {}):
                continue
            disc = bytes(ev["discriminator"]) if ev.get("discriminator") else hashlib.sha256(("event:" + ev["name"]).encode()).digest()[:8]
            table[disc] = (ev["name"], t["type"]["fields"], types)
    return table


def idl_decode(raw: bytes, entry: tuple[str, list[Json], dict[str, Json]], wanted: Sequence[str]) -> dict[str, Any]:
    """Independent decode: walk the IDL fields in order; stop once every wanted field is read (later layout is not needed)."""
    _, fields, types = entry
    out: dict[str, Any] = {}
    off = 8
    need = set(wanted)
    for f in fields:
        if not need:
            break
        val, off = _read(raw, off, f["type"], types)
        if f["name"] in need:
            out[f["name"]] = val
            need.discard(f["name"])
    if need:
        raise CheckError(f"IDL has no field(s) {sorted(need)}")
    return out


def shadow_decode(raw: bytes) -> dict[str, Any] | None:
    """The shadow's own decode path (event_v on, as the fresh-forward shadow runs it)."""
    ev = td.decode_program_data(raw, event_v=True)
    if ev is not None:
        return ev
    return td.decode_extra_event(raw)


def compare_event(raw: bytes, entry: tuple[str, list[Json], dict[str, Json]], row: Mapping[str, Any]) -> list[str]:
    """Field names (never values) on which the shadow row differs from the IDL decode. [] = equal."""
    name = entry[0]
    mapping = EVENT_FIELDS.get(name)
    if mapping is None:
        return []
    want = list(mapping)
    if name == "TradeEvent":
        want.append("is_buy")
    # decoder keys that only exist when the blob is long enough (event_v tail, creator_fee) are required when the IDL layout reaches them
    ref = idl_decode(raw, entry, want)
    bad: list[str] = []
    for fld, key in mapping.items():
        if key not in row:
            bad.append(f"{fld}:missing_in_shadow")
        elif row[key] != ref[fld]:
            bad.append(fld)
    if name == "TradeEvent" and row.get("side") != ("buy" if ref["is_buy"] else "sell"):
        bad.append("is_buy")
    return bad


# ------------------------------------------------------------------------------------------------------------------------------ sample
_INVOKE = re.compile(r"^Program (\S+) invoke \[\d+\]")
_END = re.compile(r"^Program (\S+) (?:success|failed)")


def program_data_events(logs: Sequence[str], programs: Sequence[str] = SAMPLE_PROGRAMS) -> list[bytes]:
    """`Program data:` blobs whose emitting program (the log stack top) is one of `programs`."""
    stack: list[str] = []
    out: list[bytes] = []
    for line in logs:
        if not isinstance(line, str):
            continue
        m = _INVOKE.match(line)
        if m:
            stack.append(m.group(1))
            continue
        if _END.match(line):
            if stack:
                stack.pop()
            continue
        if line.startswith("Program data: ") and stack and stack[-1] in programs:
            raw = td._program_data_bytes(line)
            if raw is not None:
                out.append(raw)
    return out


def tx_is_sample(tx: Json) -> bool:
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return False
    logs = meta.get("logMessages") or []
    return any((m := _INVOKE.match(x)) and m.group(1) in SAMPLE_PROGRAMS for x in logs if isinstance(x, str))


def fetch_sample(client: Any, start_slot: int) -> tuple[list[list[str]], int]:
    """First SAMPLE_MIN_TXS qualifying txs (their log lists) scanning start_slot upward, at most SAMPLE_MAX_SLOTS slots. Returns (logs, slots_scanned).
    The public-RPC client is mon.RpcClient; an RPC failure propagates (the caller turns it into ERROR)."""
    out: list[list[str]] = []
    scanned = 0
    for slot in range(start_slot, start_slot + SAMPLE_MAX_SLOTS):
        scanned += 1
        blk = client.call("getBlock", [slot, {"encoding": "json", "transactionDetails": "full", "rewards": False,
                                              "maxSupportedTransactionVersion": mon.MAX_TX_VERSION, "commitment": "finalized"}])
        if not blk:
            continue
        for tx in blk.get("transactions") or []:
            if tx_is_sample(tx):
                out.append(list(tx["meta"]["logMessages"]))
                if len(out) >= SAMPLE_MIN_TXS:
                    return out, scanned
    return out, scanned


def evaluate_sample(sample: Sequence[Sequence[str]], idls: Mapping[str, Json] | None) -> dict[str, Any]:
    """Counts only. disc_len: hex -> [min, max, n]. failures are the reasons that make the sample a FAIL (the pins comparison is separate)."""
    table = idl_event_table(idls) if idls else {}
    disc_len: dict[str, list[int]] = {}
    undecodable: dict[str, int] = {}
    unknown: dict[str, int] = {}
    mismatch: dict[str, int] = {}
    seen_names: set[str] = set()
    n_events = n_init_boost = 0
    for logs in sample:
        for raw in program_data_events(logs):
            if len(raw) < 8:
                continue
            n_events += 1
            disc = raw[:8]
            h = disc.hex()
            r = disc_len.setdefault(h, [len(raw), len(raw), 0])
            r[0], r[1], r[2] = min(r[0], len(raw)), max(r[1], len(raw)), r[2] + 1
            if h not in td.KNOWN_DISCS:
                unknown[h] = unknown.get(h, 0) + 1
                continue
            entry = table.get(disc)
            if entry:
                seen_names.add(entry[0])
            if disc not in SHADOW_DECODED_DISCS:
                continue
            row = shadow_decode(raw)
            if row is None:
                undecodable[h] = undecodable.get(h, 0) + 1
                continue
            if disc == INIT_BOOST_DISC:
                n_init_boost += 1
            if entry:
                try:
                    bad = compare_event(raw, entry, row)
                except CheckError as exc:
                    bad = [f"idl_decode:{exc}"]
                for b in bad:
                    key = f"{entry[0]}.{b}"
                    mismatch[key] = mismatch.get(key, 0) + 1
    return {"n_txs": len(sample), "n_events": n_events, "disc_len": disc_len, "undecodable": undecodable, "unknown": unknown, "idl_mismatch": mismatch,
            "seen_events": sorted(seen_names), "n_init_boost": n_init_boost}


# ------------------------------------------------------------------------------------------------------------------------------ the check
def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        obj = json.load(fh)
    if not isinstance(obj, dict):
        raise CheckError(f"{path} is not a JSON object")
    return obj


def need(value: Any, what: str) -> Any:
    if value is None:
        raise CheckError(f"pin missing: {what}")
    return value


def fetch_idls(http_get: Callable[[str, float], tuple[int, Mapping[str, str], bytes]], pins: Json) -> tuple[dict[str, Json], list[str]]:
    """Returns (parsed IDLs, FAIL reasons for hash differences). A fetch problem or a null pin raises CheckError (ERROR)."""
    out: dict[str, Json] = {}
    fails: list[str] = []
    for name in IDL_NAMES:
        want = need(((pins.get("idl") or {}).get(name) or {}).get("sha256"), f"idl.{name}.sha256")
        status, _h, body = http_get(IDL_URL.format(name=name), 30.0)
        if status != 200 or not body:
            raise CheckError(f"IDL {name}: http {status}")
        got = hashlib.sha256(body).hexdigest()
        if got != want:
            fails.append(f"idl {name} sha256 {want[:12]} -> {got[:12]}")
        try:
            out[name] = json.loads(body)
        except ValueError as exc:
            raise CheckError(f"IDL {name} is not JSON") from exc
    return out, fails


def check_redeploy(rec: Json, mon_pins: Json, pins: Json, tri: Json, *, client: Any, http_get: Callable, now_ms: Callable[[], int], deploy_ms: int) -> tuple[str, list[str], dict[str, Any]]:
    """Returns (verdict PASS|FAIL, reasons, detail). Raises CheckError for ERROR. Does not look at the deadline; the caller does."""
    fails: list[str] = []
    flags = ((rec.get("halt") or {}).get("flags")) or {}
    # a. the other five flags: clear and evaluated
    for k in OTHER_FIVE:
        f = flags.get(k)
        if f is None:
            fails.append(f"a: flag {k} absent from the record")
        elif f.get("halt"):
            fails.append(f"a: {k} is set")
        elif not f.get("evaluated", True):
            fails.append(f"a: {k} not evaluated")
    # b. accounts and fees program unchanged vs the monitor pins
    for name, p in (mon_pins.get("accounts") or {}).items():
        obs = (rec.get("accounts") or {}).get(name) or {}
        if obs.get("sha256") is None:
            fails.append(f"b: account {name} not read")
        elif obs["sha256"] != p.get("sha256"):
            fails.append(f"b: account {name} hash changed")
    fobs, fpin = (rec.get("programs") or {}).get("fees") or {}, (mon_pins.get("programs") or {}).get("fees") or {}
    if fobs.get("deploy_slot") is None or fobs.get("deploy_slot") != fpin.get("deploy_slot"):
        fails.append("b: fees program deploy slot unread or changed")
    # e (record half). BOOST share, budget, slices within +/-20% of the pins
    grads = (rec.get("graduations") or {}).get("boost_denominator") or {}
    n_den, n_ib = grads.get("n_nonmayhem_wsol", 0), grads.get("n_init_boost", 0)
    share_pin = need((pins.get("boost") or {}).get("init_boost_share"), "boost.init_boost_share")
    min_ib = need((pins.get("boost") or {}).get("min_init_boost_in_sample"), "boost.min_init_boost_in_sample")
    if n_den < mon.MIN_EVAL_N_DEFAULT:
        fails.append(f"e: only {n_den} non-mayhem WSOL graduations, share not evaluable")
    else:
        sh = n_ib / n_den
        if abs(sh / share_pin - 1.0) > BOOST_TOL:
            fails.append(f"e: InitBoost share {sh:.3f} vs pin {share_pin} beyond +/-{BOOST_TOL:.0%}")
    boost = rec.get("boost") or {}
    pb = mon_pins.get("boost") or {}
    for key, pin_key, label in (("budget_sol", "budget_sol", "budget"), ("slices", "slices", "slices")):
        cur = (boost.get(key) or {}).get("median")
        want = pb.get(pin_key)
        if cur is None or not want:
            fails.append(f"e: BOOST {label} median or pin unavailable")
        elif abs(cur / want - 1.0) > BOOST_TOL:
            fails.append(f"e: BOOST {label} median {cur} vs pin {want} beyond +/-{BOOST_TOL:.0%}")
    # f. IDL unchanged
    idls, idl_fails = fetch_idls(http_get, pins)
    fails += [f"f: {x}" for x in idl_fails]
    # c, d. the sample
    start_slot = max(d["new"] for d in tri["redeploys"]) + SAMPLE_START_OFFSET_SLOTS
    sample, scanned = fetch_sample(client, start_slot)
    detail: dict[str, Any] = {"sample_start_slot": start_slot, "slots_scanned": scanned}
    if len(sample) < SAMPLE_MIN_TXS:
        fails.append(f"c: sample has {len(sample)} txs after {scanned} slots, need {SAMPLE_MIN_TXS}")
    ev = evaluate_sample(sample, idls)
    detail.update({k: ev[k] for k in ("n_txs", "n_events", "n_init_boost", "seen_events")})
    detail["disc_len"] = ev["disc_len"]
    if ev["undecodable"]:
        fails.append(f"c: undecodable events of a decoded discriminator: {ev['undecodable']}")
    if ev["unknown"]:
        fails.append(f"c: unknown discriminators: {ev['unknown']}")
    if ev["idl_mismatch"]:
        fails.append(f"c: shadow decode differs from the IDL decode: {ev['idl_mismatch']}")
    for name in REQUIRED_EVENTS:
        if name not in ev["seen_events"]:
            fails.append(f"d: {name} not seen in the sample")
    ranges = pins.get("disc_len") or {}
    for h, (lo, hi, _n) in ev["disc_len"].items():
        pr = ranges.get(h)
        if pr is None or pr.get("min_len") is None or pr.get("max_len") is None:
            raise CheckError(f"pin missing: disc_len.{h}")
        if lo < pr["min_len"] or hi > pr["max_len"]:
            fails.append(f"d: discriminator {h} length {lo}-{hi} outside pinned {pr['min_len']}-{pr['max_len']}")
    if ev["n_init_boost"] < min_ib:
        fails.append(f"e: InitBoost events in sample {ev['n_init_boost']} below pinned minimum {min_ib}")
    return ("FAIL" if fails else "PASS"), fails, detail


def append_state(path: Path, entry: Json) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")


def read_state(path: Path) -> dict[str, dict[str, Any]]:
    """Per redeploy key, the FIRST verdict ever written (a verdict is never replaced)."""
    out: dict[str, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                e = json.loads(line)
                out.setdefault(e["key"], e)
    return out


def decide(rec: Json, prev: Json | None, mon_pins: Json, pins: Json, state_path: Path, *, client: Any, http_get: Callable, now_ms: Callable[[], int], job_id: str = "") -> dict[str, Any]:
    tri = triage(rec, mon_pins, prev)
    v: dict[str, Any] = {"schema": SCHEMA, "run_utc": rec["run_utc"], "action": tri["action"], "verdict": "NONE", "reasons": [], "redeploys": [],
                         "flag_halt_ms": tri["flag_halt_ms"], "halt_at_ms": None, "record_only": False, "job_id": job_id}
    if tri["action"] == "none":
        return v
    v["redeploys"] = [{"name": d["name"], "slot": d["new"], "deploy_utc": d.get("deploy_utc")} for d in tri["redeploys"]]
    if tri["action"] == "hard":
        v.update(verdict="HALT", reasons=tri["hard_reasons"], halt_at_ms=tri["run_ms"])
        return v
    # redeploy-only: decide at the redeploy instant
    try:
        deploy_ms = redeploy_instant_ms(tri["redeploys"])
    except CheckError as exc:
        v.update(verdict="ERROR", reasons=[str(exc)], halt_at_ms=tri["run_ms"])
        return v
    deadline = deploy_ms + CHECK_WINDOW_S * 1000
    v["redeploy_utc"], v["deadline_utc"] = iso_ms(deploy_ms), iso_ms(deadline)
    state = read_state(state_path)
    prior = [state.get(redeploy_key(d)) for d in tri["redeploys"]]
    if any(p and p["verdict"] != "PASS" for p in prior):
        bad = next(p for p in prior if p and p["verdict"] != "PASS")
        v.update(verdict=bad["verdict"], reasons=[f"state: {bad['key']} was {bad['verdict']} ({'; '.join(bad.get('reasons', []))[:300]})"], halt_at_ms=deploy_ms)
        return v
    if all(prior):
        v.update(verdict="PASS", record_only=True, reasons=["covered: every changed slot already PASSed"], covered=True)
        return v

    def finish(verdict: str, reasons: list[str], detail: dict[str, Any] | None = None) -> dict[str, Any]:
        v.update(verdict=verdict, reasons=reasons, detail=detail or {})
        if verdict == "PASS":
            v["record_only"] = True
        else:
            v["halt_at_ms"] = deploy_ms
        for d in tri["redeploys"]:
            if state.get(redeploy_key(d)) is None:
                append_state(state_path, {"key": redeploy_key(d), "program": d["name"], "slot": d["new"], "deploy_utc": d.get("deploy_utc"), "verdict": verdict,
                                          "reasons": reasons[:20], "checked_ms": now_ms(), "run_utc": rec["run_utc"], "job_id": job_id})
        return v

    if now_ms() > deadline:
        return finish("FAIL", [f"timeout: check started {iso_ms(now_ms())}, after the deadline {iso_ms(deadline)}"])
    try:
        verdict, reasons, detail = check_redeploy(rec, mon_pins, pins, tri, client=client, http_get=http_get, now_ms=now_ms, deploy_ms=deploy_ms)
    except (CheckError, mon.RpcError, mon.RpcUnreachable, mon.CallBudgetExceeded, OSError, ValueError, KeyError) as exc:
        return finish("ERROR", [f"{type(exc).__name__}: {str(exc)[:200]}"])
    if now_ms() > deadline:
        return finish("FAIL", [f"timeout: finished {iso_ms(now_ms())}, after the deadline {iso_ms(deadline)}"] + reasons, detail)
    return finish(verdict, reasons, detail)


# ------------------------------------------------------------------------------------------------------------------------------ CLI
def last_two_records(path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    recs = mon.read_tail_records(path)
    if not recs:
        raise CheckError(f"no daily record in {path}")
    return recs[-1], (recs[-2] if len(recs) > 1 else None)


def observe_report(rec: Json, client: Any, http_get: Callable, start_slot: int) -> dict[str, Any]:
    """For the program-upgrade review: what the pins file should hold. Writes nothing, decides nothing."""
    idl_sha, idls = {}, {}
    for name in IDL_NAMES:
        status, _h, body = http_get(IDL_URL.format(name=name), 30.0)
        if status != 200:
            raise CheckError(f"IDL {name}: http {status}")
        idl_sha[name] = hashlib.sha256(body).hexdigest()
        idls[name] = json.loads(body)
    sample, scanned = fetch_sample(client, start_slot)
    ev = evaluate_sample(sample, idls)
    den = (rec.get("graduations") or {}).get("boost_denominator") or {}
    return {"idl_sha256": idl_sha, "sample_start_slot": start_slot, "slots_scanned": scanned, "n_txs": ev["n_txs"], "disc_len": ev["disc_len"], "n_init_boost": ev["n_init_boost"],
            "init_boost_share": (den.get("n_init_boost", 0) / den["n_nonmayhem_wsol"]) if den.get("n_nonmayhem_wsol") else None,
            "undecodable": ev["undecodable"], "unknown": ev["unknown"], "idl_mismatch": ev["idl_mismatch"]}


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="A3 automatic redeploy check (no override path).")
    p.add_argument("--daily", default=DEFAULT_DAILY, help="A3 daily record JSONL (default %(default)s)")
    p.add_argument("--monitor-pins", default=str(mon.DEFAULT_PINS))
    p.add_argument("--pins", default=str(DEFAULT_PINS), help="a3_redeploy pins (default tools/a3_redeploy_pins.json)")
    p.add_argument("--state", required=False, default="/data/mal/structure-monitor/a3-redeploy-state.jsonl", help="append-only verdict memory")
    p.add_argument("--verdict-out", help="also write the verdict JSON here")
    p.add_argument("--rpc-url", default=mon.DEFAULT_RPC)
    p.add_argument("--job-id", default=os.environ.get("MISCUSI_JOB_ID", ""))
    p.add_argument("--observe", action="store_true", help="print the values the pins file should hold from the newest record's redeploy; decide nothing")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None, *, client: Any = None, http_get: Callable | None = None, now_ms: Callable[[], int] | None = None) -> int:
    a = parse_args(argv)
    clock = now_ms or (lambda: int(time.time() * 1000))
    if "helius" in a.rpc_url.lower():
        print("refused: public RPC only", file=sys.stderr)
        return 64
    try:
        rec, prev = last_two_records(Path(a.daily))
        age_s = clock() / 1000 - utc_ms(rec["run_utc"]) / 1000
        mon_pins = mon.load_pins(Path(a.monitor_pins))
        pins = load_json(Path(a.pins))
    except (CheckError, OSError, ValueError, KeyError) as exc:
        print(f"A3_CHECK_OPERATIONAL_ERROR {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
        return 11
    if age_s > MAX_RECORD_AGE_S and not a.observe:
        print(f"A3_CHECK_OPERATIONAL_ERROR newest daily record {rec['run_utc']} is {int(age_s)} s old (> {MAX_RECORD_AGE_S}): the monitor did not write a fresh line", file=sys.stderr)
        return 11
    cl = client or mon.RpcClient(a.rpc_url, min_interval=0.5, max_calls=450)
    get = http_get or mon.urllib_get
    if a.observe:
        tri = triage(rec, mon_pins, prev)
        if not tri["redeploys"]:
            print("no program redeploy in the newest record", file=sys.stderr)
            return 11
        print(json.dumps(observe_report(rec, cl, get, max(d["new"] for d in tri["redeploys"]) + SAMPLE_START_OFFSET_SLOTS), sort_keys=True))
        return 0
    v = decide(rec, prev, mon_pins, pins, Path(a.state), client=cl, http_get=get, now_ms=clock, job_id=a.job_id)
    text = json.dumps(v, sort_keys=True)
    print(text)
    if a.verdict_out:
        Path(a.verdict_out).write_text(text + "\n", encoding="utf-8")
    return 10 if v["halt_at_ms"] is not None else 0


if __name__ == "__main__":
    sys.exit(main())
