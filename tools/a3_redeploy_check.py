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
  c. decoder parity on TWO pinned samples, both at or after (newest changed deploy slot + SAMPLE_START_OFFSET_SLOTS), both through the frozen decoders (imported, never copied):
       LOG SAMPLE: the first SAMPLE_MIN_TXS successful txs that invoke the pump or PumpSwap program (public getBlock), events = `Program data:` lines emitted by those programs.
       MIGRATE (inner-instruction) SAMPLE: the first MIGRATE_K settled migrate txs (fetched whole), events = mon.tx_event_blobs (log lines AND emit_cpi inner instructions), which is
       how the book gets Complete, CompletePumpAmmMigration, CreatePool, InitBoost and the V-carrying fields. Pass bar for each: 0 undecodable, 0 unknown discriminators
       (not in KNOWN_DISCS and not a pinned ignored one), field-by-field equality of every decoded event with an INDEPENDENT decode driven by the IDL's own field list;
  d. every event with an IDL entry is consumed EXACTLY by the IDL's full field list plus its pinned trailing bytes; the pinned required events were seen in the migrate sample;
     Trade / Buy / Sell were seen in the log sample; each discriminator's length lies in its pinned range (or the IDL consumes it exactly);
  e. BOOST evidence ONLY from the migrate sample's graduations (all at or after the changed deploy slot, settled): at least MIN_EVAL_N non-mayhem WSOL graduations and MIN_BOOST_POOLS
     boost pools, else ERROR; InitBoost share, median budget and median slice count within +/-BOOST_TOL (20%) of the pins; at least the pinned (> 0) InitBoost events in the sample;
  f. IDL unchanged: sha256 of each pinned IDL URL (the MAIN branch of github.com/pump-fun/pump-public-docs, unauthenticated, fetched at run time) equals the pin.
     This is the credential-free route. The alternative (programdata-derived discriminator table) would need the program's Anchor IDL account or a decompile; the samples'
     discriminator sets in (c)/(d) are the on-chain half of the same question. A fetch failure is ERROR, never a pass;
  h. FROZEN GUARD: git blob of each decoder file the frozen book imports (FROZEN_FILES) equals the pinned blob from the book's frozen_sha, else ERROR.
  g. the whole check finishes within CHECK_WINDOW_S (24 h) of the earliest changed redeploy instant; later is a FAIL (timeout).

ALSO IN THE SAMPLE CHECK: every event with an IDL entry must be consumed EXACTLY by the IDL's full field list (a field added, removed or resized breaks it); a Buy/Sell the shadow
refuses only for a quote amount above its own 100,000 SOL sanity cap is `capped` (explained, bounded by the pinned max_capped_share); `ignored_discs` pins known events the rule
does not read. LIMIT: the sample is the tx LOGS, as the shadow reads them; an event emitted only through emit_cpi inner instructions is not in it (InitBoost was never seen in logs
in 14,755 txs on 2026-10-21), so the BOOST evidence is the monitor record half in (e), and min_init_boost_in_sample is pinned 0.

PINS (null in tools/a3_redeploy_pins.json => ERROR => hard halt; fail closed): IDL sha256 x3, per-discriminator length ranges, InitBoost share, min InitBoost events, max_capped_share. `--observe` prints the observed values for the program-upgrade review (C2/C3) to pin; it writes no pin and decides nothing.

Exit codes: 0 NONE or PASS; 10 HALT (verdict.halt_at_ms is set); 11 operational error that is NOT an A3 flag (stale or unreadable daily record, a state file that cannot be written): no halt
from this tool, the job is loud and tools.a3_halt's dead-man rule is the backstop.
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
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from observe import trade_decode as td
from tools import pump_structure_monitor as mon

SCHEMA = "a3_redeploy_check.v1"
DEFAULT_PINS = Path(__file__).with_name("a3_redeploy_pins.json")
DEFAULT_DAILY = os.path.expanduser("~/data/structure-monitor/daily.jsonl")
DEFAULT_STATE = os.path.expanduser("~/data/structure-monitor/a3-redeploy-state.jsonl")

# ----- pinned rule constants (changing any of them changes the pinned tool blob) -----
CHECK_WINDOW_S = 24 * 3600  # the check must finish within this long of the earliest changed redeploy instant
SAMPLE_START_OFFSET_SLOTS = 150  # sample starts this many slots after the newest changed deploy slot
SAMPLE_MIN_TXS = 500  # first N successful txs that invoke the pump or PumpSwap program
SAMPLE_MAX_SLOTS = 300  # slots scanned at most (skipped slots included); short of SAMPLE_MIN_TXS by then => FAIL
SAMPLE_PROGRAMS = (td.PUMP_BONDING_PROGRAM, td.PUMPSWAP_PROGRAM)
REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATE_K = 40  # the inner-instruction sample: the first K settled migrate txs at or after (newest changed deploy slot + SAMPLE_START_OFFSET_SLOTS)
MIGRATE_K_MIN = 20  # fewer than this many => ERROR (not enough post-deploy evidence)
MIGRATE_MAX_PAGES = 8  # getSignaturesForAddress pages of 1000 back from now; the start slot not reached => ERROR
BOOST_TOL = mon.BOOST_CHANGE_MAX  # 0.20
MAX_RECORD_AGE_S = 1800  # a newer daily line than this is "stale" (exit 11, no halt)
IDL_URL = "https://raw.githubusercontent.com/pump-fun/pump-public-docs/main/idl/{name}.json"
IDL_NAMES = ("pump", "pump_amm", "pump_fees")

LOADER_PROGRAM = "BPFLoaderUpgradeab1e11111111111111111111111"
LOADER_UPGRADE_TAG = 3  # UpgradeableLoaderInstruction::Upgrade (bincode u32, little endian)
MAX_WRITE_PAGES = 8  # getSignaturesForAddress(programdata) pages of 1000 back to the pinned deploy slot; not reached => fail closed
TRAILING_EVENTS = ("TradeEvent", "BuyEvent", "SellEvent", "WithdrawEvent")  # the events that carry the added 8-byte field (QP repin ruling C3'b)
SOL_QUOTES = (mon.NATIVE_SOL, mon.WSOL_MINT)
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
    "CompleteEvent": {"mint": "mint", "bonding_curve": "curve"},
    "CompletePumpAmmMigrationEvent": {"mint": "mint", "bonding_curve": "curve", "pool": "pool", "quote_mint": "quote_mint"},
    "PostCompleteBuyEvent": {"user": "trader", "mint": "mint", "bonding_curve": "bonding_curve", "quote_mint": "quote_mint", "timestamp": "event_ts", "base_out": "base_out",
                             "quote_in": "quote_in", "fee": "fee", "creator_fee": "creator_fee", "buyback_fee": "buyback_fee",
                             "pool_base_reserves_before": "pool_base_reserves_before", "pool_quote_reserves_before": "pool_quote_reserves_before",
                             "pool_base_reserves_after": "pool_base_reserves_after", "pool_quote_reserves_after": "pool_quote_reserves_after"},
}
SHADOW_DECODED_DISCS = {  # discriminators the shadow decodes: the pinned `read` set
    mon.DISC_MIGRATE, mon.DISC_COMPLETE, td._TRADE_DISC, td._BUY_DISC, td._SELL_DISC, td._CREATE_POOL_DISC, td._POST_COMPLETE_BUY_DISC, td._BOOST_BUY_AND_BURN_DISC,
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
    ev = td.decode_extra_event(raw)
    if ev is not None:
        return ev
    return mon.decode_migration_event(raw) or mon.decode_complete_event(raw)  # the monitor's decoders: the shadow imports them for the class inputs


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
        else:
            want_v = ref[fld]
            if name == "CompletePumpAmmMigrationEvent" and fld == "quote_mint" and want_v == mon.NATIVE_SOL:
                want_v = mon.WSOL_MINT  # the monitor's decoder normalises native SOL to WSOL
            if row[key] != want_v:
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


SWAP_AMOUNT_FIELD = {td._BUY_DISC: "user_quote_amount_in", td._SELL_DISC: "user_quote_amount_out"}


def idl_full_len(raw: bytes, entry: tuple[str, list[Json], dict[str, Json]]) -> int:
    """Offset at which the IDL's COMPLETE field list ends. An event whose bytes are not exactly consumed has a different layout than the IDL (field added, removed or resized)."""
    off = 8
    for f in entry[1]:
        _v, off = _read(raw, off, f["type"], entry[2])
    return off


def evaluate_blobs(blobs: Iterable[bytes], idls: Mapping[str, Json] | None, ignored: Mapping[str, str] | None = None,
                   trailing: Mapping[str, Sequence[int]] | None = None) -> dict[str, Any]:
    """Counts only. disc_len: hex -> [min, max, n]. `ignored` = pinned {disc hex: IDL event name} of events the rule does not read (known, not decoded by the shadow).
    capped = a Buy/Sell the shadow refuses ONLY because its quote amount is above the shadow's own sanity cap (td._MAX_SWAP_SOL_LAMPORTS, 100,000 SOL), with a sane timestamp:
    explained, counted apart, bounded by a pinned share. A blob shorter than 8 bytes has no discriminator: counted as undecodable ("short"). Any other non-decode is `undecodable`.
    layout_mismatch = the bytes after the IDL's full field list (`trailing`, per event name, pinned list of allowed counts; default [0]) are not an allowed count."""
    table = idl_event_table(idls) if idls else {}
    ignored = ignored or {}
    trailing = trailing or {}
    trailing_seen: dict[str, set[int]] = {}
    capped = n_swap = n_events = n_init_boost = 0
    layout_bad: dict[str, int] = {}
    nonzero: list[dict[str, Any]] = []
    disc_len: dict[str, list[int]] = {}
    undecodable: dict[str, int] = {}
    unknown: dict[str, int] = {}
    mismatch: dict[str, int] = {}
    seen_names: set[str] = set()
    for raw in blobs:
        n_events += 1
        if len(raw) < 8:
            undecodable["short"] = undecodable.get("short", 0) + 1
            continue
        disc = raw[:8]
        h = disc.hex()
        r = disc_len.setdefault(h, [len(raw), len(raw), 0])
        r[0], r[1], r[2] = min(r[0], len(raw)), max(r[1], len(raw)), r[2] + 1
        entry = table.get(disc)
        if h not in td.KNOWN_DISCS and not (h in ignored and (entry is None or entry[0] == ignored[h])):
            unknown[h] = unknown.get(h, 0) + 1
            continue
        if entry:
            seen_names.add(entry[0])
            try:
                end = idl_full_len(raw, entry)
                extra = len(raw) - end
                trailing_seen.setdefault(entry[0], set()).add(extra)
                if extra == 8 and entry[0] in TRAILING_EVENTS and int.from_bytes(raw[end:end + 8], "little") != 0:
                    # C3'c: the added field is non-zero. Where is it? A SOL/WSOL-quoted trade (bonding: the event's quote_mint; PumpSwap: the pool's quote mint, looked up later)
                    probe = idl_decode(raw, entry, ["quote_mint"]) if entry[0] == "TradeEvent" else idl_decode(raw, entry, ["pool"]) if "pool" in {f["name"] for f in entry[1]} else {}
                    nonzero.append({"name": entry[0], "quote_mint": probe.get("quote_mint"), "pool": probe.get("pool")})
                if extra not in (trailing.get(entry[0]) or [0]):
                    layout_bad[entry[0]] = layout_bad.get(entry[0], 0) + 1
            except CheckError:
                layout_bad[entry[0]] = layout_bad.get(entry[0], 0) + 1
        if disc not in SHADOW_DECODED_DISCS:
            continue
        if disc in SWAP_AMOUNT_FIELD:
            n_swap += 1
        row = shadow_decode(raw)
        if row is None:
            if entry and disc in SWAP_AMOUNT_FIELD:
                try:
                    ref = idl_decode(raw, entry, ["timestamp", SWAP_AMOUNT_FIELD[disc]])
                    if td._sane_ts(ref["timestamp"]) and ref[SWAP_AMOUNT_FIELD[disc]] > td._MAX_SWAP_SOL_LAMPORTS:
                        capped += 1
                        continue
                except CheckError:
                    pass
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
    return {"n_events": n_events, "disc_len": disc_len, "undecodable": undecodable, "unknown": unknown, "idl_mismatch": mismatch, "seen_events": sorted(seen_names),
            "n_init_boost": n_init_boost, "capped": capped, "n_swap_events": n_swap, "layout_mismatch": layout_bad, "nonzero_trailing": nonzero,
            "trailing_seen": {k: sorted(v) for k, v in sorted(trailing_seen.items())}}


def evaluate_sample(sample: Sequence[Sequence[str]], idls: Mapping[str, Json] | None, ignored: Mapping[str, str] | None = None,
                    trailing: Mapping[str, Sequence[int]] | None = None) -> dict[str, Any]:
    """The log sample: `Program data:` blobs emitted by the pump / PumpSwap programs (log-stack top), as the shadow reads logs."""
    ev = evaluate_blobs((raw for logs in sample for raw in program_data_events(logs)), idls, ignored, trailing)
    ev["n_txs"] = len(sample)
    return ev


# ------------------------------------------------------------------------------------------------------------------------------ the inner-instruction (migrate) sample
def migrate_sample(client: Any, min_slot: int, now_s: float) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """The first MIGRATE_K settled migrate txs with slot >= min_slot (oldest first), each as (whole tx, the monitor's parse_migrate_tx dict + sig).
    Candidates come from the migration fee account's signature list read back from now until a signature older than min_slot; the start slot not reached in
    MIGRATE_MAX_PAGES pages of 1000 is ERROR. Only txs older than the settle time (mon.SETTLE_S_DEFAULT) are taken, so BOOST has finished."""
    cutoff_bt = int(now_s) - mon.SETTLE_S_DEFAULT
    cands: list[dict[str, Any]] = []
    before = None
    reached = False
    for _ in range(MIGRATE_MAX_PAGES):
        opts: dict[str, Any] = {"limit": 1000, "commitment": "finalized"}
        if before:
            opts["before"] = before
        sigs = client.call("getSignaturesForAddress", [mon.MIGRATION_FEE_ACCOUNT, opts]) or []
        for s in sigs:
            before = s["signature"]
            if s["slot"] < min_slot:
                reached = True
            elif s.get("err") is None and s.get("blockTime") is not None and s["blockTime"] <= cutoff_bt:
                cands.append(s)
        if reached or len(sigs) < 1000:
            reached = True
            break
    if not reached:
        raise CheckError(f"migrate sample: start slot {min_slot} not reached in {MIGRATE_MAX_PAGES} pages")
    cands.sort(key=lambda s: s["slot"])
    out: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for s in cands:
        tx = mon.fetch_tx(client, s["signature"])
        if tx is None:
            continue
        g = mon.parse_migrate_tx(tx)
        if g is None:
            continue
        g["sig"] = s["signature"]
        out.append((tx, g))
        if len(out) >= MIGRATE_K:
            break
    return out


def median(vals: Sequence[float]) -> float:
    return statistics.median(vals)


def migrate_evidence(client: Any, idls: Mapping[str, Json], min_slot: int, now_s: float, ignored: Mapping[str, str] | None, trailing: Mapping[str, Sequence[int]] | None) -> dict[str, Any]:
    """C-1 and C-2: the post-deploy migrate sample decoded through the FROZEN path (mon.tx_event_blobs: log lines AND emit_cpi inner instructions, then the shadow's decoders),
    compared with the independent IDL decode, plus the BOOST evidence from the SAME graduations (all at or after min_slot). Not enough graduations => ERROR."""
    txs = migrate_sample(client, min_slot, now_s)
    if len(txs) < MIGRATE_K_MIN:
        raise CheckError(f"migrate sample: {len(txs)} settled post-deploy migrate txs, need {MIGRATE_K_MIN}")
    grads = [g for _tx, g in txs]
    mon.annotate_mayhem(client, grads)
    ev = evaluate_blobs((b for tx, _g in txs for b in mon.tx_event_blobs(tx)), idls, ignored, trailing)
    ev["n_migrate_txs"] = len(txs)
    ev["slot_min"], ev["slot_max"] = min(g["slot"] for g in grads), max(g["slot"] for g in grads)
    den = [g for g in grads if g.get("mayhem") is False and g["quote_wsol"]]
    if len(den) < mon.MIN_EVAL_N_DEFAULT:
        raise CheckError(f"BOOST evidence: {len(den)} non-mayhem WSOL post-deploy graduations, need {mon.MIN_EVAL_N_DEFAULT}")
    boost_pools = [g for g in den if g["init_boost"]]
    ev["n_nonmayhem_wsol"], ev["n_init_boost_graduations"] = len(den), len(boost_pools)
    ev["init_boost_share"] = len(boost_pools) / len(den)
    budgets = [g["budget_lamports"] / 1e9 for g in boost_pools if g.get("budget_lamports") is not None]
    prof = mon.profile_boost(client, boost_pools, None) if boost_pools else {}
    slices = [g["boost"]["n_slices"] for g in boost_pools if g.get("boost")]
    if len(budgets) < mon.MIN_BOOST_POOLS or len(slices) < mon.MIN_BOOST_POOLS:
        raise CheckError(f"BOOST evidence: {len(budgets)} budgets and {len(slices)} slice counts from post-deploy pools, need {mon.MIN_BOOST_POOLS}")
    ev["budget_sol_median"], ev["slices_median"] = median(budgets), median(slices)
    ev["n_pools_skipped"] = prof.get("n_pools_skipped", 0)
    return ev


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


def git_blob_sha(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def frozen_guard(pins: Json, root: Path = REPO_ROOT) -> dict[str, str]:
    """C-3: every decoder file the frozen book imports must be byte-identical (git blob) to the one at the covered book's frozen_sha, or ERROR."""
    blobs = need(pins.get("frozen_blobs"), "frozen_blobs")
    need(pins.get("frozen_sha"), "frozen_sha")
    if not isinstance(blobs, dict) or not blobs:
        raise CheckError("pin missing: frozen_blobs is empty")
    bad = []
    for rel, want in sorted(blobs.items()):
        try:
            got = git_blob_sha(root / rel)
        except OSError as exc:
            raise CheckError(f"frozen file {rel} unreadable: {exc}") from exc
        if got != want:
            bad.append(f"{rel} blob {got[:10]} != frozen {str(want)[:10]}")
    if bad:
        raise CheckError("decoder files differ from the frozen book's: " + "; ".join(bad))
    return dict(blobs)


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


def validate_trailing_pins(trailing: Any) -> dict[str, list[int]]:
    """C3'b: each pinned event has EXACTLY one allowed trailing count (a one-element list), TRAILING_EVENTS pinned [8], every other pinned event [0]. Anything else is ERROR."""
    if not isinstance(trailing, dict) or not trailing:
        raise CheckError("pin missing: trailing_bytes")
    for name, v in trailing.items():
        if not (isinstance(v, list) and len(v) == 1 and isinstance(v[0], int) and not isinstance(v[0], bool)):
            raise CheckError(f"pin invalid: trailing_bytes.{name} must be a one-element list")
        if (v[0] == 8) != (name in TRAILING_EVENTS) or v[0] not in (0, 8):
            raise CheckError(f"pin invalid: trailing_bytes.{name} = {v}: only {list(TRAILING_EVENTS)} carry 8, every other event 0")
    missing = [n for n in TRAILING_EVENTS if n not in trailing]
    if missing:
        raise CheckError(f"pin missing: trailing_bytes for {missing}")
    return trailing


def universe_nonzero(client: Any, ev: Mapping[str, Any]) -> int:
    """C3'c: how many events with a NON-ZERO added field sit in the H5 universe (SOL-quoted bonding trade, WSOL-quoted PumpSwap trade). A PumpSwap pool whose quote mint cannot be
    read counts as in the universe (fail closed). Any count > 0 is a hard fail: the field's meaning would then be unknown inside the universe."""
    n = 0
    pools = sorted({x["pool"] for x in ev.get("nonzero_trailing", ()) if x["name"] != "TradeEvent" and x.get("pool")})
    quote: dict[str, str | None] = {}
    for i in range(0, len(pools), 100):
        chunk = pools[i:i + 100]
        res = client.call("getMultipleAccounts", [chunk, {"encoding": "base64", "commitment": "finalized"}])
        for pool, entry in zip(chunk, (res or {}).get("value") or []):
            info = td.decode_pool_account(mon._account_bytes(entry)) if entry else None
            quote[pool] = info["quote_mint"] if info else None
    for x in ev.get("nonzero_trailing", ()):
        if x["name"] == "TradeEvent":
            n += x.get("quote_mint") in SOL_QUOTES or x.get("quote_mint") is None
        else:
            q = quote.get(x.get("pool"))
            n += q is None or q in SOL_QUOTES
    return n


def sample_failures(ev: Mapping[str, Any], label: str, pins: Json, fails: list[str], client: Any = None) -> None:
    """The FAIL reasons common to both samples."""
    if client is not None and ev.get("nonzero_trailing"):
        n_univ = universe_nonzero(client, ev)
        ev["n_nonzero_trailing"], ev["n_nonzero_in_universe"] = len(ev["nonzero_trailing"]), n_univ
        if n_univ:
            fails.append(f"c: {label}: HARD: {n_univ} SOL/WSOL-quoted trade events carry a NON-ZERO added 8-byte field (meaning unknown inside the H5 universe)")
    if ev["undecodable"]:
        fails.append(f"c: {label}: undecodable events of a decoded discriminator: {ev['undecodable']}")
    if ev["unknown"]:
        fails.append(f"c: {label}: unknown discriminators: {ev['unknown']}")
    if ev["idl_mismatch"]:
        fails.append(f"c: {label}: shadow decode differs from the IDL decode: {ev['idl_mismatch']}")
    if ev["layout_mismatch"]:
        fails.append(f"d: {label}: event bytes not consumed exactly by the IDL layout: {ev['layout_mismatch']}")
    max_cap = need(pins.get("max_capped_share"), "max_capped_share")
    if ev["n_swap_events"] and ev["capped"] / ev["n_swap_events"] > max_cap:
        fails.append(f"c: {label}: {ev['capped']}/{ev['n_swap_events']} Buy/Sell events above the shadow's amount cap (pinned max share {max_cap})")
    ranges = pins.get("disc_len") or {}
    idl_known = set(ev.get("_idl_discs", ()))
    for h, (lo, hi, _n) in ev["disc_len"].items():
        if h in ev["unknown"]:
            continue  # already a FAIL reason above; an unknown discriminator has no pin by definition
        pr = ranges.get(h)
        if pr is None and bytes.fromhex(h) in idl_known:
            continue  # no length pin, but the IDL consumes this event exactly (the stronger test); the range is only the guard for events without an IDL entry
        if pr is None or pr.get("min_len") is None or pr.get("max_len") is None:
            raise CheckError(f"pin missing: disc_len.{h}")
        if lo < pr["min_len"] or hi > pr["max_len"]:
            fails.append(f"d: {label}: discriminator {h} length {lo}-{hi} outside pinned {pr['min_len']}-{pr['max_len']}")


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
    # C-3. the decoder files are the frozen book's
    frozen_guard(pins)
    # the pins every later step needs: ERROR before any RPC when one is missing
    share_pin = need((pins.get("boost") or {}).get("init_boost_share"), "boost.init_boost_share")
    min_ib = need((pins.get("boost") or {}).get("min_init_boost_in_migrate_sample"), "boost.min_init_boost_in_migrate_sample")
    if not min_ib > 0:
        raise CheckError("pin invalid: boost.min_init_boost_in_migrate_sample must be > 0")
    required = need((pins.get("migrate") or {}).get("required_events"), "migrate.required_events")
    # f. IDL unchanged
    idls, idl_fails = fetch_idls(http_get, pins)
    fails += [f"f: {x}" for x in idl_fails]
    ignored, trailing = pins.get("ignored_discs") or {}, validate_trailing_pins(pins.get("trailing_bytes"))
    idl_discs = set(idl_event_table(idls))
    start_slot = max(d["new"] for d in tri["redeploys"]) + SAMPLE_START_OFFSET_SLOTS
    detail: dict[str, Any] = {"sample_start_slot": start_slot}
    # c, d. the log sample
    sample, scanned = fetch_sample(client, start_slot)
    detail["slots_scanned"] = scanned
    if len(sample) < SAMPLE_MIN_TXS:
        fails.append(f"c: sample has {len(sample)} txs after {scanned} slots, need {SAMPLE_MIN_TXS}")
    ev = evaluate_sample(sample, idls, ignored, trailing)
    ev["_idl_discs"] = idl_discs
    detail["log_sample"] = {k: ev[k] for k in ("n_txs", "n_events", "seen_events", "capped", "n_swap_events", "trailing_seen")}
    sample_failures(ev, "log sample", pins, fails, client)
    for name in REQUIRED_EVENTS:
        if name not in ev["seen_events"]:
            fails.append(f"d: {name} not seen in the log sample")
    # C-1, C-2. the inner-instruction (migrate) sample and the BOOST evidence from the same post-deploy graduations
    mv = migrate_evidence(client, idls, start_slot, now_ms() / 1000, ignored, trailing)
    mv["_idl_discs"] = idl_discs
    detail["migrate_sample"] = {k: mv[k] for k in ("n_migrate_txs", "slot_min", "slot_max", "n_events", "seen_events", "n_init_boost", "trailing_seen", "n_nonmayhem_wsol",
                                                    "n_init_boost_graduations", "init_boost_share", "budget_sol_median", "slices_median")}
    sample_failures(mv, "migrate sample", pins, fails, client)
    for name in required:
        if name not in mv["seen_events"]:
            fails.append(f"d: {name} not seen in the migrate sample")
    if mv["n_init_boost"] < min_ib:
        fails.append(f"e: InitBoost events in the migrate sample {mv['n_init_boost']} below pinned minimum {min_ib}")
    if abs(mv["init_boost_share"] / share_pin - 1.0) > BOOST_TOL:
        fails.append(f"e: InitBoost share {mv['init_boost_share']:.3f} vs pin {share_pin} beyond +/-{BOOST_TOL:.0%}")
    pb = mon_pins.get("boost") or {}
    for cur, want, label in ((mv["budget_sol_median"], pb.get("budget_sol"), "budget"), (mv["slices_median"], pb.get("slices"), "slices")):
        if not want:
            raise CheckError(f"monitor pin missing: boost.{label}")
        if abs(cur / want - 1.0) > BOOST_TOL:
            fails.append(f"e: BOOST {label} median {cur} vs pin {want} beyond +/-{BOOST_TOL:.0%}")
    return ("FAIL" if fails else "PASS"), fails, detail


def append_state(path: Path, entry: Json) -> None:
    """Checked append: any failure raises (the caller turns it into an operational error; a verdict that cannot be remembered is not a quiet pass)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def read_state(path: Path) -> dict[str, dict[str, Any]]:
    """Per redeploy key, the FIRST verdict ever written (a verdict is never replaced)."""
    out: dict[str, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                e = json.loads(line)
                out.setdefault(e["key"], e)
    return out


def list_upgrade_writes(client: Any, name: str, programdata: str, pinned_slot: int, newest_slot: int) -> list[dict[str, Any]]:
    """Q4: EVERY successful upgrade write to a program's programdata account after the pinned deploy slot, oldest first. Pages getSignaturesForAddress(programdata)
    back until a signature at or before the pinned slot is seen; not reached in MAX_WRITE_PAGES pages => CheckError (fail closed). A write is a successful tx with a
    BPF-loader `Upgrade` instruction, top-level or inner (the real upgrades are Squads multisig CPIs). The newest write must be the record's deploy slot, else CheckError (the enumeration and the monitor disagree)."""
    cands: list[dict[str, Any]] = []
    before = None
    reached = False
    for _ in range(MAX_WRITE_PAGES):
        opts: dict[str, Any] = {"limit": 1000, "commitment": "finalized"}
        if before:
            opts["before"] = before
        sigs = client.call("getSignaturesForAddress", [programdata, opts]) or []
        for sg in sigs:
            before = sg["signature"]
            if sg["slot"] <= pinned_slot:
                reached = True
            elif sg.get("err") is None:
                cands.append(sg)
        if reached or len(sigs) < 1000:
            break
    if not reached:
        raise CheckError(f"{name}: the pinned deploy slot {pinned_slot} was not reached in the programdata signature list (fail closed)")
    out: list[dict[str, Any]] = []
    for sg in sorted(cands, key=lambda x: x["slot"]):
        tx = mon.fetch_tx(client, sg["signature"])
        if tx is None:
            raise CheckError(f"{name}: write candidate {sg['signature'][:12]} unreadable")
        keys = mon.tx_keys(tx)
        ixs = list(((tx.get("transaction") or {}).get("message") or {}).get("instructions") or [])
        for grp in (tx.get("meta") or {}).get("innerInstructions") or []:  # upgrades go through the Squads multisig: the loader Upgrade is an INNER instruction
            ixs += list(grp.get("instructions") or [])
        is_upgrade = False
        for ix in ixs:
            idx = ix.get("programIdIndex")
            if idx is not None and idx < len(keys) and keys[idx] == LOADER_PROGRAM:
                try:
                    data = mon.b58decode(ix.get("data") or "")
                except ValueError:
                    continue
                if len(data) >= 4 and int.from_bytes(data[:4], "little") == LOADER_UPGRADE_TAG:
                    is_upgrade = True
        if not is_upgrade:
            continue
        bt = sg.get("blockTime") or tx.get("blockTime")
        if bt is None:
            raise CheckError(f"{name}: write at slot {sg['slot']} has no block time")
        out.append({"kind": "program", "name": name, "old": pinned_slot, "new": int(tx.get("slot", sg["slot"])), "deploy_utc": mon.iso(bt), "sig": sg["signature"]})
    if not out or out[-1]["new"] != newest_slot:
        raise CheckError(f"{name}: newest enumerated write {out[-1]['new'] if out else None} != the record's deploy slot {newest_slot}")
    return out


def expand_writes(client: Any, rec: Json, redeploys: Sequence[Json]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for d in redeploys:
        pdata = ((rec.get("programs") or {}).get(d["name"]) or {}).get("programdata")
        if not pdata:
            raise CheckError(f"record has no programdata address for {d['name']}")
        out += list_upgrade_writes(client, d["name"], pdata, d["old"], d["new"])
    return sorted(out, key=lambda w: (w["new"], w["name"]))


def decide(rec: Json, prev: Json | None, mon_pins: Json, pins: Json, state_path: Path, *, client: Any, http_get: Callable, now_ms: Callable[[], int], job_id: str = "") -> dict[str, Any]:
    tri = triage(rec, mon_pins, prev)
    v: dict[str, Any] = {"schema": SCHEMA, "run_utc": rec["run_utc"], "action": tri["action"], "verdict": "NONE", "reasons": [], "redeploys": [],
                         "flag_halt_ms": tri["flag_halt_ms"], "halt_at_ms": None, "record_only": False, "job_id": job_id}
    if tri["action"] == "none":
        return v
    # Q4: every upgrade write since the pinned slot is its own redeploy (own instant, own state, own check). If the enumeration fails the record's own
    # (newest-write) redeploys stand in, and in the redeploy-only path the failure itself is an ERROR.
    redeploys, expand_err = list(tri["redeploys"]), None
    if redeploys:
        try:
            redeploys = expand_writes(client, rec, redeploys)
        except Exception as exc:  # noqa: BLE001
            redeploys, expand_err = list(tri["redeploys"]), exc
    v["redeploys"] = [{"name": d["name"], "slot": d["new"], "deploy_utc": d.get("deploy_utc")} for d in redeploys]
    state = read_state(state_path)
    unresolved = [d for d in redeploys if state.get(redeploy_key(d)) is None or state[redeploy_key(d)]["verdict"] != "PASS"]
    if tri["action"] == "hard":
        # B1: a hard flag in the same run as a not-yet-PASSed program write cannot be a PASS (R3a), so R4 applies: the window ends at the EARLIEST unreviewed write,
        # or at the flag's instant when that is earlier.
        halt_at = tri["run_ms"]
        if unresolved:
            try:
                halt_at = min(halt_at, redeploy_instant_ms(unresolved))
            except CheckError:
                pass
        v.update(verdict="HALT", reasons=tri["hard_reasons"] + ([f"write enumeration failed: {expand_err}"] if expand_err else []), halt_at_ms=halt_at)
        return v
    # redeploy-only: a write that already PASSed is covered and no longer sets the instant, the deadline or the sample
    failed = [state[redeploy_key(d)] for d in redeploys if state.get(redeploy_key(d)) and state[redeploy_key(d)]["verdict"] != "PASS"]
    pending = sorted((d for d in redeploys if state.get(redeploy_key(d)) is None), key=lambda w: (redeploy_instant_ms([w]), w["new"], w["name"]))
    try:
        deploy_ms = redeploy_instant_ms(unresolved or redeploys)
    except CheckError as exc:  # no redeploy instant exists: the flag's instant is all there is
        v.update(verdict="ERROR", reasons=[str(exc)], halt_at_ms=tri["run_ms"])
        return v
    if failed and not expand_err:
        bad = failed[0]
        v.update(verdict=bad["verdict"], reasons=[f"state: {bad['key']} was {bad['verdict']} ({'; '.join(bad.get('reasons', []))[:300]})"], halt_at_ms=deploy_ms,
                 redeploy_utc=iso_ms(deploy_ms))
        return v
    if not pending and not expand_err:
        v.update(verdict="PASS", record_only=True, reasons=["covered: every changed slot already PASSed"], covered=True)
        return v
    deadline = deploy_ms + CHECK_WINDOW_S * 1000
    v["redeploy_utc"], v["deadline_utc"] = iso_ms(deploy_ms), iso_ms(deadline)

    def remember(d: Json, verdict: str, reasons: list[str]) -> None:
        if state.get(redeploy_key(d)) is None:
            append_state(state_path, {"key": redeploy_key(d), "program": d["name"], "slot": d["new"], "deploy_utc": d.get("deploy_utc"), "verdict": verdict,
                                      "reasons": reasons[:20], "checked_ms": now_ms(), "run_utc": rec["run_utc"], "job_id": job_id})

    def end(verdict: str, reasons: list[str], halt_ms: int | None, items: Sequence[Json], detail: dict[str, Any] | None = None) -> dict[str, Any]:
        v.update(verdict=verdict, reasons=reasons, detail=detail or {})
        if verdict == "PASS":
            v["record_only"] = True
        else:
            v["halt_at_ms"] = halt_ms
        for d in items:
            remember(d, verdict, reasons)
        return v

    if expand_err:  # the enumeration could not be completed (or the pinned slot was not reached): ERROR, at the earliest record-known redeploy instant
        return end("ERROR", [f"{type(expand_err).__name__}: {str(expand_err)[:200]}"], deploy_ms, [d for d in tri["redeploys"] if state.get(redeploy_key(d)) is None])
    if now_ms() > deadline:
        return end("FAIL", [f"timeout: check started {iso_ms(now_ms())}, after the deadline {iso_ms(deadline)}"], deploy_ms, pending)
    detail: dict[str, Any] = {}
    per_write: dict[str, str] = {}
    all_reasons: list[str] = []
    for i, w in enumerate(pending):
        w_ms = redeploy_instant_ms([w])
        try:
            verdict, reasons, detail = check_redeploy(rec, mon_pins, pins, dict(tri, redeploys=[w]), client=client, http_get=http_get, now_ms=now_ms, deploy_ms=w_ms)
        except Exception as exc:  # noqa: BLE001 - ANY failure of the check is ERROR (hard halt), recorded in the state; a traceback with no state would be a silent no-halt
            verdict, reasons, detail = "ERROR", [f"{type(exc).__name__}: {str(exc)[:200]}"], {}
        if verdict == "PASS" and now_ms() > deadline:
            verdict, reasons = "FAIL", [f"timeout: finished {iso_ms(now_ms())}, after the deadline {iso_ms(deadline)}"]
        per_write[redeploy_key(w)] = verdict
        remember(w, verdict, reasons)
        if verdict != "PASS":
            # the window ends at the EARLIEST write that is not reviewed; the writes after it are not evaluated (the window is already over)
            for later in pending[i + 1:]:
                remember(later, verdict, [f"not evaluated: earlier write {redeploy_key(w)} {verdict}"])
                per_write[redeploy_key(later)] = f"not evaluated ({verdict})"
            v["per_write"] = per_write
            return end(verdict, [f"{redeploy_key(w)}: {r}" for r in reasons], w_ms, [], detail)
        all_reasons += reasons
    v["per_write"] = per_write
    return end("PASS", all_reasons, None, [], detail)


# ------------------------------------------------------------------------------------------------------------------------------ CLI
def previous_daily(recs: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """The newest record of an EARLIER UTC date than the last record: the previous daily run. A manual run in between (same date) does not reset 'two consecutive runs'."""
    last = recs[-1]["run_utc"][:10]
    for r in reversed(recs[:-1]):
        if str(r.get("run_utc", ""))[:10] < last:
            return r
    return None


def last_two_records(path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    recs = mon.read_tail_records(path)
    if not recs:
        raise CheckError(f"no daily record in {path}")
    return recs[-1], previous_daily(recs)


def observe_report(rec: Json, client: Any, http_get: Callable, start_slot: int, ignored: Mapping[str, str] | None = None, trailing: Mapping[str, Sequence[int]] | None = None,
                   now_s: float | None = None) -> dict[str, Any]:
    """For the program-upgrade review: what the pins file should hold. Writes nothing, decides nothing. Both samples: the log sample and the migrate (inner-instruction) sample."""
    idl_sha, idls = {}, {}
    for name in IDL_NAMES:
        status, _h, body = http_get(IDL_URL.format(name=name), 30.0)
        if status != 200:
            raise CheckError(f"IDL {name}: http {status}")
        idl_sha[name] = hashlib.sha256(body).hexdigest()
        idls[name] = json.loads(body)
    sample, scanned = fetch_sample(client, start_slot)
    ev = evaluate_sample(sample, idls, ignored, trailing)
    keep = ("disc_len", "n_init_boost", "undecodable", "unknown", "idl_mismatch", "capped", "n_swap_events", "layout_mismatch", "seen_events", "trailing_seen")
    out: dict[str, Any] = {"idl_sha256": idl_sha, "sample_start_slot": start_slot, "frozen_blobs": {rel: git_blob_sha(REPO_ROOT / rel) for rel in FROZEN_FILES},
                           "log_sample": {"slots_scanned": scanned, "n_txs": ev["n_txs"], **{k: ev[k] for k in keep}}}
    mv = migrate_evidence(client, idls, start_slot, now_s if now_s is not None else time.time(), ignored, trailing)
    out["migrate_sample"] = {k: mv[k] for k in (*keep, "n_migrate_txs", "slot_min", "slot_max", "n_events", "n_nonmayhem_wsol", "n_init_boost_graduations", "init_boost_share",
                                                 "budget_sol_median", "slices_median", "n_pools_skipped")}
    return out


FROZEN_FILES = ("observe/trade_decode.py", "tools/h5_shadow.py", "tools/pump_structure_monitor.py", "observe/link_state.py", "tools/cap_pick_oracle.py", "tools/paper_curve_math.py")


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="A3 automatic redeploy check (no override path).")
    p.add_argument("--daily", default=DEFAULT_DAILY, help="A3 daily record JSONL (default %(default)s)")
    p.add_argument("--monitor-pins", default=str(mon.DEFAULT_PINS))
    p.add_argument("--pins", default=str(DEFAULT_PINS), help="a3_redeploy pins (default tools/a3_redeploy_pins.json)")
    p.add_argument("--state", default=DEFAULT_STATE, help="append-only verdict memory (default %(default)s)")
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
    except Exception as exc:  # noqa: BLE001 - unreadable inputs are an operational error, loud, never a pass
        print(f"A3_CHECK_OPERATIONAL_ERROR {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
        return 11
    if age_s > MAX_RECORD_AGE_S and not a.observe:
        print(f"A3_CHECK_OPERATIONAL_ERROR newest daily record {rec['run_utc']} is {int(age_s)} s old (> {MAX_RECORD_AGE_S}): the monitor did not write a fresh line", file=sys.stderr)
        return 11
    cl = client or mon.RpcClient(a.rpc_url, min_interval=0.5, max_calls=450)
    get = http_get or mon.urllib_get
    try:
        if a.observe:
            tri = triage(rec, mon_pins, prev)
            if not tri["redeploys"]:
                print("no program redeploy in the newest record", file=sys.stderr)
                return 11
            writes = expand_writes(cl, rec, tri["redeploys"])  # Q4: every upgrade write since the pinned slot, printed with the report
            out = observe_report(rec, cl, get, max(d["new"] for d in tri["redeploys"]) + SAMPLE_START_OFFSET_SLOTS, pins.get("ignored_discs"), pins.get("trailing_bytes"), clock() / 1000)
            out["upgrade_writes"] = [{k: w[k] for k in ("name", "new", "deploy_utc")} for w in writes]
            print(json.dumps(out, sort_keys=True))
            return 0
        v = decide(rec, prev, mon_pins, pins, Path(a.state), client=cl, http_get=get, now_ms=clock, job_id=a.job_id)
        text = json.dumps(v, sort_keys=True)
        print(text)
        if a.verdict_out:
            Path(a.verdict_out).write_text(text + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - the state could not be written, the triage failed, ...: tool failure, loud
        print(f"A3_CHECK_OPERATIONAL_ERROR {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
        return 11
    return 10 if v["halt_at_ms"] is not None else 0


if __name__ == "__main__":
    sys.exit(main())
