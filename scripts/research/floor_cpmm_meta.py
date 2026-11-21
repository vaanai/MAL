#!/usr/bin/env python3
"""floor_cpmm_meta: the v2 universe metadata for the FLOOR-CPMM replay (DECLARE s2, s5). PAPER ONLY, read-only public chain data, one Helius flock slot, rps <= 8.

    python scripts/research/floor_cpmm_meta.py --migrations LIST.json --out META.json [--env-file ...] [--call-cap 2000] [--rps 6]

For each pool of the migrations list (the same list the fetch ran with): getMultipleAccounts on the PoolState, then on the token mint, the LP mint and the distinct AmmConfig accounts, and
getTokenAccountsByOwner(Raydium LP-lock authority, LP mint) for the locked LP. Output (`floor-cpmm-meta-v2`): per pool token_mint, token_program (token | token-2022), transfer_fee (bool),
extensions (all Token-2022 extension type ids; PermanentDelegate / TransferHook / NonTransferable / DefaultAccountState are listed report-only), freeze_authority, mint_authority, supply (S),
amm_config, trade / protocol / fund / creator fee rates (ppm of 1e6), lp_supply_ps (PoolState.lp_supply), lp_supply_mint, lp_locked_frac = (burned + held by the lock authority) / lp_supply_ps.
An unreadable or undecodable account makes the pool `unreadable: <reason>` (excluded and counted by the pricer), never assumed. The key is read from the env file and never printed; stdout is
counts, the output file's sha256 and the run time only.

CURRENT-STATE LOOK-AHEAD (DECLARE preamble item 2), direction stated: with a null mint authority S can only fall (burns), so today's S <= S at entry and k / S is overstated, which makes E1
and E2 easier to pass; a freeze or mint authority can go from set to null but never back, so today's null can include a pool that would have been vetoed at entry; the token program and the
transfer-fee extension cannot change after mint init. Layouts below are pinned in code and tested on synthetic bytes; they are NOT verified against the chain here (QP question on the PR).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import launchlab_cpmm_fetch as C  # noqa: E402
from tools import venue_backfill as vb  # noqa: E402
from tools import venue_tape as vt  # noqa: E402

SCHEMA = "floor-cpmm-meta-v2"
WSOL = C.WSOL
CPMM = C.CPMM
LOCK_AUTH = "3f7GcQFG397GAaEnv51zR6tsTVihYRydnydDD1cXekxH"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
EXT_TRANSFER_FEE_CONFIG = 1
EXT_REPORT_ONLY = {"permanent_delegate": 12, "transfer_hook": 14, "non_transferable": 9, "default_account_state": 6}
NOTE = ("S, mint authority, freeze authority and the LP lock are CURRENT-STATE reads (look-ahead): S can only fall after entry (k / S overstated, E1 and E2 easier to pass); a null authority "
        "today may have been set at entry; the token program and transfer-fee extension cannot change. Not a history.")
GMA_BATCH = 100
# PoolState (Anchor, packed): disc 8, amm_config 8, pool_creator 40, vault0 72, vault1 104, lp_mint 136, mint0 168, mint1 200, prog0 232, prog1 264, observation 296, auth_bump 328, status 329,
# lp_mint_decimals 330, mint_0_decimals 331, mint_1_decimals 332, lp_supply u64 333
PS = {"amm_config": 8, "lp_mint": 136, "mint0": 168, "mint1": 200, "lp_supply": 333, "min_len": 341}
# AmmConfig: disc 8, bump 8, disable_create_pool 9, index u16 10, trade_fee_rate u64 12, protocol 20, fund 28, create_pool_fee 36, protocol_owner 44, fund_owner 76, creator_fee_rate u64 108
AC = {"trade": 12, "protocol": 20, "fund": 28, "creator": 108, "min_len": 116}


class Unreadable(Exception):
    pass


def pk(b: bytes, o: int) -> str:
    return vt.b58(b[o:o + 32])


def u64(b: bytes, o: int) -> int:
    return struct.unpack_from("<Q", b, o)[0]


def decode_pool_state(b: bytes) -> dict[str, Any]:
    if len(b) < PS["min_len"]:
        raise Unreadable("pool_state_short")
    return {"amm_config": pk(b, PS["amm_config"]), "lp_mint": pk(b, PS["lp_mint"]), "mint0": pk(b, PS["mint0"]), "mint1": pk(b, PS["mint1"]), "lp_supply": u64(b, PS["lp_supply"])}


def decode_amm_config(b: bytes) -> dict[str, int]:
    if len(b) < AC["min_len"]:
        raise Unreadable("amm_config_short")
    return {"trade_fee_rate": u64(b, AC["trade"]), "protocol_fee_rate": u64(b, AC["protocol"]), "fund_fee_rate": u64(b, AC["fund"]), "creator_fee_rate": u64(b, AC["creator"])}


def decode_mint(b: bytes, owner: str) -> dict[str, Any]:
    """SPL mint (82 bytes: COption<mint_authority> tag u32 @0 + key @4, supply u64 @36, decimals @44, init @45, COption<freeze> tag @46 + key @50). Token-2022: padded to 165, account type byte
    (1 = mint) @165, TLV from 166 (type u16, length u16, data)."""
    if owner not in (TOKEN_PROGRAM, TOKEN_2022):
        raise Unreadable("mint_owner_unknown")
    if len(b) < 82 or b[45] != 1:
        raise Unreadable("mint_undecodable")
    ma_tag, fz_tag = struct.unpack_from("<I", b, 0)[0], struct.unpack_from("<I", b, 46)[0]
    if ma_tag not in (0, 1) or fz_tag not in (0, 1):
        raise Unreadable("mint_bad_option_tag")
    out: dict[str, Any] = {"token_program": "token" if owner == TOKEN_PROGRAM else "token-2022", "supply": u64(b, 36), "mint_authority": pk(b, 4) if ma_tag else None,
                           "freeze_authority": pk(b, 50) if fz_tag else None, "extensions": [], "transfer_fee": False}
    if owner == TOKEN_2022 and len(b) > 82:
        if len(b) < 166 or b[165] != 1:
            raise Unreadable("token2022_account_type")
        o = 166
        while o + 4 <= len(b):
            t, ln = struct.unpack_from("<HH", b, o)
            if t == 0 and ln == 0:
                break
            if o + 4 + ln > len(b):
                raise Unreadable("token2022_tlv_truncated")
            out["extensions"].append(t)
            o += 4 + ln
    out["transfer_fee"] = EXT_TRANSFER_FEE_CONFIG in out["extensions"]
    out["extensions_report_only"] = {k: out["extensions"].count(v) for k, v in EXT_REPORT_ONLY.items()}
    return out


def _gma(call: Callable, credits: vb.Credits, addrs: list[str]) -> list[tuple[bytes, str] | None]:
    res: list[tuple[bytes, str] | None] = []
    for i in range(0, len(addrs), GMA_BATCH):
        credits.charge(1)
        r = call("getMultipleAccounts", [addrs[i:i + GMA_BATCH], {"encoding": "base64"}])
        for v in (r or {}).get("value") or []:
            if not v:
                res.append(None)
                continue
            try:
                res.append((base64.b64decode(v["data"][0], validate=True), v["owner"]))
            except Exception:  # noqa: BLE001
                res.append(None)
    if len(res) != len(addrs):
        raise RuntimeError("getMultipleAccounts length mismatch")
    return res


def locked_lp(call: Callable, credits: vb.Credits, lp_mint: str) -> int:
    credits.charge(1)
    r = call("getTokenAccountsByOwner", [LOCK_AUTH, {"mint": lp_mint}, {"encoding": "jsonParsed"}])
    tot = 0
    for a in (r or {}).get("value") or []:
        info = (((a.get("account") or {}).get("data") or {}).get("parsed") or {}).get("info") or {}
        if info.get("mint") != lp_mint or info.get("owner") != LOCK_AUTH:
            raise Unreadable("lp_lock_account_mismatch")
        tot += int((info.get("tokenAmount") or {}).get("amount"))
    return tot


def build_meta(call: Callable, credits: vb.Credits, pools: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {p: {} for p in pools}
    ps = _gma(call, credits, pools)
    st: dict[str, dict[str, Any]] = {}
    for p, v in zip(pools, ps):
        try:
            if v is None or v[1] != CPMM:
                raise Unreadable("pool_state_missing")
            st[p] = decode_pool_state(v[0])
        except Unreadable as e:
            out[p] = {"unreadable": str(e)}
        except Exception:  # noqa: BLE001
            out[p] = {"unreadable": "pool_state_undecodable"}
    tok: dict[str, str] = {}
    for p, s in st.items():
        if WSOL not in (s["mint0"], s["mint1"]):
            out[p] = {"unreadable": "non_wsol_quote"}
        else:
            tok[p] = s["mint1"] if s["mint0"] == WSOL else s["mint0"]
    live = [p for p in pools if p in tok]
    mints = _gma(call, credits, [tok[p] for p in live])
    lps = _gma(call, credits, [st[p]["lp_mint"] for p in live])
    cfgs = sorted({st[p]["amm_config"] for p in live})
    cfg_raw = dict(zip(cfgs, _gma(call, credits, cfgs)))
    cfg_dec: dict[str, dict[str, int] | str] = {}
    for c, v in cfg_raw.items():
        try:
            if v is None or v[1] != CPMM:
                raise Unreadable("amm_config_missing")
            cfg_dec[c] = decode_amm_config(v[0])
        except Unreadable as e:
            cfg_dec[c] = str(e)
        except Exception:  # noqa: BLE001
            cfg_dec[c] = "amm_config_undecodable"
    for p, m, lp in zip(live, mints, lps):
        s = st[p]
        try:
            if m is None or lp is None:
                raise Unreadable("mint_missing")
            md = decode_mint(*m)
            lpd = decode_mint(*lp)
            cd = cfg_dec[s["amm_config"]]
            if isinstance(cd, str):
                raise Unreadable(cd)
            if s["lp_supply"] <= 0:
                raise Unreadable("lp_supply_zero")
            if lpd["supply"] > s["lp_supply"]:
                raise Unreadable("lp_mint_supply_above_pool_state")
            burned = s["lp_supply"] - lpd["supply"]
            lock = locked_lp(call, credits, s["lp_mint"])
            out[p] = {"token_mint": tok[p], "token_program": md["token_program"], "transfer_fee": md["transfer_fee"], "extensions": md["extensions"],
                      "extensions_report_only": md["extensions_report_only"], "freeze_authority": md["freeze_authority"], "mint_authority": md["mint_authority"], "supply": md["supply"],
                      "amm_config": s["amm_config"], **cd, "lp_supply_ps": s["lp_supply"], "lp_supply_mint": lpd["supply"], "lp_burned": burned, "lp_locked_lock_authority": lock,
                      "lp_locked_frac": (burned + lock) / s["lp_supply"]}
        except Unreadable as e:
            out[p] = {"unreadable": str(e)}
        except (ValueError, TypeError, KeyError):
            out[p] = {"unreadable": "undecodable"}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--migrations", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--env-file", default=vb.HELIUS_ENV)
    ap.add_argument("--lockdir", default=vb.LOCKDIR)
    ap.add_argument("--rps", type=float, default=6.0)
    ap.add_argument("--call-cap", type=int, default=2000)
    a = ap.parse_args(argv)
    if a.rps > 8:
        print("refused: rps > 8", file=sys.stderr)
        return 2
    pools = [m["pool"] for m in C.load_window_migrations(a.migrations)]
    outp = Path(a.out)
    if outp.exists():
        print("refused: --out exists", file=sys.stderr)
        return 2
    try:
        url = vb.make_url(vb.read_key(a.env_file))
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return vb.EXIT_ENV
    credits = vb.Credits(a.call_cap, 1)
    slot = C.acquire_slot(a.lockdir)
    try:
        call = vb.http_rpc(url, vb.Pacer(a.rps))
        meta = build_meta(call, credits, pools)
    except vb.CapReached:
        print(f"call cap reached at {credits.used}: no output written", file=sys.stderr)
        return vb.EXIT_CAP
    finally:
        slot.close()
    doc = {"schema": SCHEMA, "run_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "note": NOTE, "calls": credits.used, "pools": meta}
    txt = json.dumps(doc, indent=1, sort_keys=True) + "\n"
    outp.write_text(txt)
    unread = sum(1 for v in meta.values() if "unreadable" in v)
    print(json.dumps({"pools": len(pools), "unreadable": unread, "calls": credits.used, "sha256": hashlib.sha256(txt.encode()).hexdigest(), "run_utc": doc["run_utc"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
