"""P36 venue census: first-30 s bot-competition sample per venue from public RPC (getSignaturesForAddress + getTransaction). Counts only.

For each sampled launch (create or migration tx at slot s0, block time t0):
  * follow address A (pump.fun: the mint; PumpSwap: the pool; LaunchLab: the pool),
  * list A's signatures in [s0, s0 + n_slots] with `until` = the launch tx and `before` = a signature of a block just past the window,
  * keep successful txs with blockTime - t0 <= 30 s; fetch them with getTransaction (a seeded uniform sample of at most SAMPLE_EARLY txs in slots s0..s0+1
    and at most SAMPLE_LATE of the later ones, each weighted n / n_sampled within its group),
  * decode buys of the launch's token from the logs (pump/PumpSwap: observe.trade_decode via tools.arena_feed.rows_from_notice;
    LaunchLab: tools.venue_tape.decode_ll_event), buyer = the event's trader (pump/PumpSwap) or the fee payer (LaunchLab).
Reported per launch: buy volume in slots 0-1 / buy volume in the first 30 s (all buyers and excluding the launch signer), distinct
buyer wallets in slots 0-1, failed txs in the window. Never prices anything; no P&L.

Seal notes (manager17 COMMON-BRIEF + SEALS-BRIEF 823cc062): no Meteora DBC rows from [2026-10-17T00, 2026-11-01T00:10Z); PumpSwap is
sampled only from 2026-09 (EXP-024 Look-2 seal covers October V-range pools' first 360 s); LaunchLab holdout [10-17, 10-24) not touched.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))  # repo root

import p36_rpc as R  # noqa: E402

PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMPSWAP = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
LAUNCHLAB = "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj"
GENERIC_MINT: dict = {}  # pool -> base mint, filled from --launch-list for venue "generic"
PUMP_MINT_AUTH = "TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM"  # pump.fun mint authority PDA: appears in create txs only
PUMP_MIGRATOR = "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg"  # signs pump.fun MigrateV2 (checked 2026-10-26)
WINDOW_S = 30
SAMPLE_LATE = 12
SAMPLE_EARLY = 30
MAX_PAGES = 4
FORBIDDEN = [  # (venue, start_unix, end_unix): never sample a launch inside these
    ("meteora_dbc", 1792195200, 1793491800),  # [2026-10-17T00, 2026-11-01T00:10Z)
    ("pumpswap", 1790812800, 1793404800),  # October 2026 (Look-2 seal: V-range pools' first 360 s, any source)
    ("launchlab", 1792195200, 1792800000),  # LaunchLab holdout [10-17T00, 10-24T00)
]


def forbidden(venue: str, t: int) -> bool:
    return any(v == venue and a <= t < b for v, a, b in FORBIDDEN)


def notice(tx: dict, sig: str):
    from observe.trade_source import RawNotice
    return RawNotice(slot=tx["slot"], signature=sig, failed=tx["meta"]["err"] is not None, logs=tuple(tx["meta"]["logMessages"] or ()),
                     t_recv_ms=(tx.get("blockTime") or 0) * 1000, commitment="confirmed", feed="p36_getTransaction")


def fee_payer(tx: dict) -> str:
    return tx["transaction"]["message"]["accountKeys"][0]


def all_keys(tx: dict) -> list[str]:
    k = list(tx["transaction"]["message"]["accountKeys"])
    la = tx["meta"].get("loadedAddresses") or {}
    return k + list(la.get("writable", [])) + list(la.get("readonly", []))


def block_sig_after(slot: int, tag: str) -> tuple[str | None, int | None, int]:
    """First signature of the first non-empty block at or after `slot`."""
    for s in range(slot, slot + 8):
        b = R.rpc("getBlock", [s, {"transactionDetails": "signatures", "rewards": False, "maxSupportedTransactionVersion": 1,
                                  "commitment": "confirmed"}], tag)
        if isinstance(b, dict) and b.get("signatures"):
            return b["signatures"][0], b.get("blockTime"), s
    return None, None, slot


def buys_in_tx(venue: str, tx: dict, sig: str, key: str) -> list[dict]:
    """Buys of the launch token in one tx: [{trader, lamports_or_quote}]."""
    out = []
    if venue in ("pump", "pumpswap"):
        from tools.arena_feed import rows_from_notice
        pool_mints: dict = {}
        for r in rows_from_notice(notice(tx, sig), pool_mints):
            if r.get("type") != "trade" or r.get("side") != "buy":
                continue
            if venue == "pump" and r.get("prog") == "pumpfun" and r.get("mint") == key:
                amt = int(r.get("sol_lamports") or 0)
                if amt == 0 and r.get("protocol_fee") and r.get("protocol_fee_bps"):
                    # custom-pair coin: the decoder leaves sol_lamports 0; quote amount recovered from fee / fee_bps (quote units)
                    amt = int(r["protocol_fee"]) * 10_000 // int(r["protocol_fee_bps"])
                out.append({"trader": r.get("trader"), "amt": amt})
            if venue == "pumpswap" and r.get("prog") == "pumpswap" and r.get("pool") == key:
                out.append({"trader": r.get("trader"), "amt": int(r.get("sol_lamports") or 0)})
    elif venue == "generic":
        # venue without a decoder (meme.fun): buyer = fee payer; a buy = the payer's balance of the base mint rises;
        # amount = the payer's lamport decrease minus the tx fee (includes any ATA rent, ~0.002 SOL: disclosed bias)
        mint = GENERIC_MINT.get(key)
        payer = fee_payer(tx)
        pre = sum(float(b["uiTokenAmount"]["amount"]) for b in tx["meta"].get("preTokenBalances") or [] if b.get("owner") == payer and b["mint"] == mint)
        post = sum(float(b["uiTokenAmount"]["amount"]) for b in tx["meta"].get("postTokenBalances") or [] if b.get("owner") == payer and b["mint"] == mint)
        if post > pre and tx["meta"]["err"] is None:
            lam = tx["meta"]["preBalances"][0] - tx["meta"]["postBalances"][0] - tx["meta"]["fee"]
            out.append({"trader": payer, "amt": max(int(lam), 0)})
    elif venue == "launchlab":
        from tools.venue_tape import decode_ll_event, program_events
        pe = program_events(tx["meta"]["logMessages"] or [], LAUNCHLAB)
        for b in pe["data"]:
            ev = decode_ll_event(b)
            if ev.get("type") == "trade" and ev.get("side") == "buy" and ev.get("pool") == key:
                out.append({"trader": fee_payer(tx), "amt": int(ev["amount_in"])})
    return out


def sample_launch(venue: str, launch_sig: str, key: str | None, slots_per_30s: int, rng: random.Random, tag: str) -> dict:
    tx0 = R.get_tx(launch_sig, f"{tag} launch tx")
    if not isinstance(tx0, dict) or "meta" not in tx0:
        return {"launch_sig": launch_sig, "error": "launch tx fetch failed", "raw": str(tx0)[:200]}
    s0, t0, signer = tx0["slot"], tx0.get("blockTime"), fee_payer(tx0)
    info: dict = {"venue": venue, "launch_sig": launch_sig, "slot0": s0, "t0": t0, "launch_signer": signer}
    if forbidden(venue, t0 or 0):
        return {**info, "error": "inside a sealed window; skipped"}
    creator = signer
    if venue == "pump":
        from tools.arena_feed import rows_from_notice
        cr = [r for r in rows_from_notice(notice(tx0, launch_sig), {}) if r.get("type") == "create"]
        if not cr:
            return {**info, "error": "no create event"}
        key, creator = cr[0]["mint"], cr[0].get("creator") or signer
        info["is_mayhem_mode"] = cr[0].get("is_mayhem_mode")
        info["quote_mint"] = cr[0].get("quote_mint")
        info["router_ix"] = sorted({ln.split("Instruction: ")[1] for ln in tx0["meta"]["logMessages"] if "Instruction: " in ln
                                    and "Create" in ln and "CreateV2" not in ln and "CreateIdempotent" not in ln})[:4]
    elif venue == "pumpswap":
        from tools.arena_feed import rows_from_notice
        pc = [r for r in rows_from_notice(notice(tx0, launch_sig), {}) if r.get("type") == "pool_create"]
        if not pc:
            return {**info, "error": "no pool_create event"}
        key = pc[0]["pool"]
        info["mint"] = pc[0]["mint"]
        info["quote_mint"] = pc[0]["quote_mint"]
    elif venue == "launchlab":
        from tools.venue_tape import decode_ll_event, program_events
        pe = program_events(tx0["meta"]["logMessages"] or [], LAUNCHLAB)
        cr = [decode_ll_event(b) for b in pe["data"]]
        cr = [c for c in cr if c.get("type") == "create"]
        if cr:
            key = key or cr[0]["pool"]
            creator = cr[0]["creator"]
            info["config"] = cr[0]["config"]
        tb = tx0["meta"].get("postTokenBalances") or []
        info["mints_in_create"] = sorted({b["mint"] for b in tb})
    info["key"], info["creator"] = key, creator
    before, bt_after, s_after = block_sig_after(s0 + slots_per_30s + 6, f"{tag} window-end block")
    if before is None:
        return {**info, "error": "no window-end block"}
    if bt_after is not None and t0 is not None and bt_after - t0 < WINDOW_S:
        info["warn_window_short"] = bt_after - t0
    lst: list = []
    cur = before
    for page in range(MAX_PAGES):
        part = R.sigs(key, f"{tag} window sigs p{page}", before=cur, until=launch_sig)
        if not isinstance(part, list):
            return {**info, "error": f"sigs failed {str(part)[:120]}"}
        lst += part
        if len(part) < 1000:
            break
        cur = part[-1]["signature"]
    info["sigs_listed"] = len(lst)
    info["sigs_truncated"] = len(lst) >= 1000 * MAX_PAGES
    win = [x for x in lst if x["slot"] >= s0 and x.get("blockTime") is not None and t0 is not None and x["blockTime"] - t0 <= WINDOW_S]
    ok = [x for x in win if x["err"] is None]
    info["n_ok_window"] = len(ok)
    info["n_failed_window"] = len(win) - len(ok)
    info["n_failed_slot01"] = sum(1 for x in win if x["err"] is not None and x["slot"] <= s0 + 1)
    early = [x for x in ok if x["slot"] <= s0 + 1]
    late = [x for x in ok if x["slot"] > s0 + 1]
    late_s = late if len(late) <= SAMPLE_LATE else rng.sample(late, SAMPLE_LATE)
    w_late = (len(late) / len(late_s)) if late_s else 0.0
    early_s = early if len(early) <= SAMPLE_EARLY else rng.sample(early, SAMPLE_EARLY)
    w_early = (len(early) / len(early_s)) if early_s else 0.0
    info["n_early_fetched"] = len(early_s)
    info["n_early_ok"], info["n_late_ok"], info["n_late_fetched"] = len(early), len(late), len(late_s)
    buys = []
    # the launch tx itself (dev buy / platform opening buy)
    for b in buys_in_tx(venue, tx0, launch_sig, key):
        buys.append({**b, "slot_off": 0, "w": 1.0, "in_launch_tx": True})
    for x, w in [(x, w_early) for x in early_s] + [(x, w_late) for x in late_s]:
        t = R.get_tx(x["signature"], f"{tag} window tx")
        if not isinstance(t, dict) or "meta" not in t:
            info["fetch_fail"] = info.get("fetch_fail", 0) + 1
            continue
        for b in buys_in_tx(venue, t, x["signature"], key):
            buys.append({**b, "slot_off": t["slot"] - s0, "w": w, "in_launch_tx": False})
    tot = sum(b["amt"] * b["w"] for b in buys)
    e01 = sum(b["amt"] * b["w"] for b in buys if b["slot_off"] <= 1)
    tot_x = sum(b["amt"] * b["w"] for b in buys if b["trader"] not in (creator, signer))
    e01_x = sum(b["amt"] * b["w"] for b in buys if b["slot_off"] <= 1 and b["trader"] not in (creator, signer))
    w01 = {b["trader"] for b in buys if b["slot_off"] <= 1}
    w01_x = w01 - {creator, signer}
    info.update({
        "buy_amt_30s": tot, "buy_amt_slot01": e01, "share_slot01": (e01 / tot) if tot else None,
        "buy_amt_30s_ex_launcher": tot_x, "buy_amt_slot01_ex_launcher": e01_x,
        "share_slot01_ex_launcher": (e01_x / tot_x) if tot_x else None,
        "wallets_slot01": len(w01), "wallets_slot01_ex_launcher": len(w01_x),
        "n_buys_fetched": len(buys),
        "buys": [[b["slot_off"], b["amt"], b["trader"], round(b["w"], 3), b["in_launch_tx"]] for b in buys],
        "first_other_buy_slot": min((b["slot_off"] for b in buys if b["trader"] not in (creator, signer)), default=None),
    })
    return info


def pick_pump_today(n: int, min_age_s: int, max_age_s: int, rng: random.Random) -> list[str]:
    lst = R.sigs(PUMP_MINT_AUTH, "pump create list", limit=1000)
    now = int(time.time())
    c = [x["signature"] for x in lst if x["err"] is None and x.get("blockTime") and min_age_s <= now - x["blockTime"] <= max_age_s]
    return rng.sample(c, min(n, len(c)))


def sig_at_time(t_target: int, tag: str) -> tuple[str, int]:
    """A signature of a block near unix time t_target (slot estimated from the current slot and slot rate, refined twice)."""
    cur = R.rpc("getSlot", [{"commitment": "finalized"}], tag)
    bt_cur = R.rpc("getBlockTime", [cur], tag)
    s = cur - int((bt_cur - t_target) / 0.4)
    for _ in range(3):
        bt = R.rpc("getBlockTime", [s], tag)
        if not isinstance(bt, int):
            s += 3
            continue
        bt2 = R.rpc("getBlockTime", [s - 5000], tag)
        rate = (bt - bt2) / 5000 if isinstance(bt2, int) and bt != bt2 else 0.4
        if abs(bt - t_target) < 20:
            break
        s -= int((bt - t_target) / rate)
    sig, _, s2 = block_sig_after(s, tag)
    return sig, s2


def slot_rate_at(slot: int, tag: str) -> float:
    a = R.rpc("getBlockTime", [slot], tag)
    b = R.rpc("getBlockTime", [slot - 3000], tag)
    return (a - b) / 3000 if isinstance(a, int) and isinstance(b, int) else 0.4


def pick_migrations(t_target: int, n: int, rng: random.Random) -> tuple[list[str], float]:
    before, s = sig_at_time(t_target, "pumpswap cursor")
    rate = slot_rate_at(s, "pumpswap slot rate")
    lst = R.sigs(PUMP_MIGRATOR, "pumpswap migration list", before=before, limit=200)
    c = [x["signature"] for x in lst if x["err"] is None]
    return rng.sample(c, min(n, len(c))), rate


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--venue", required=True, choices=["pump", "pumpswap", "launchlab", "generic"])
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--out", required=True)
    ap.add_argument("--t-target", type=int, default=None, help="pumpswap: unix time of the sample (September 2026 only)")
    ap.add_argument("--launch-list", default=None, help="launchlab: JSON list of {sig, pool} from p36_capture")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    res = {"venue": a.venue, "started": int(time.time()), "rows": []}
    if a.venue == "pump":
        perf = R.rpc("getRecentPerformanceSamples", [5], "pump slot rate")
        rate = sum(p["samplePeriodSecs"] for p in perf) / sum(p["numSlots"] for p in perf)
        launches = [(s, None) for s in pick_pump_today(a.n, 600, 3000, rng)]
    elif a.venue == "pumpswap":
        launches_s, rate = pick_migrations(a.t_target, a.n, rng)
        launches = [(s, None) for s in launches_s]
    elif a.venue == "generic":
        # launch list = [{pool, mint}] from GeckoTerminal; the launch tx = the pool's oldest signature
        perf = R.rpc("getRecentPerformanceSamples", [5], "generic slot rate")
        rate = sum(p["samplePeriodSecs"] for p in perf) / sum(p["numSlots"] for p in perf)
        launches = []
        for x in json.load(open(a.launch_list))[: a.n]:
            GENERIC_MINT[x["pool"]] = x["mint"]
            lst = R.sigs(x["pool"], "generic pool oldest sig")
            if isinstance(lst, list) and lst and len(lst) < 1000:
                launches.append((lst[-1]["signature"], x["pool"]))
    else:
        perf = R.rpc("getRecentPerformanceSamples", [5], "launchlab slot rate")
        rate = sum(p["samplePeriodSecs"] for p in perf) / sum(p["numSlots"] for p in perf)
        lst = json.load(open(a.launch_list))
        lst = rng.sample(lst, min(a.n, len(lst)))
        launches = [(x["sig"], x["pool"]) for x in lst]
    spw = int(WINDOW_S / rate) + 1
    res["slot_s"], res["slots_per_30s"] = rate, spw
    for i, (sig, key) in enumerate(launches):
        try:
            row = sample_launch(a.venue, sig, key, spw, rng, f"{a.venue} {i}")
        except RuntimeError as e:  # budget
            res["stopped"] = str(e)
            break
        except Exception as e:  # noqa: BLE001
            row = {"launch_sig": sig, "error": f"{type(e).__name__}: {e}"[:300]}
        res["rows"].append(row)
        json.dump(res, open(a.out, "w"), indent=1)
        print(i, json.dumps({k: row.get(k) for k in ("share_slot01", "share_slot01_ex_launcher", "wallets_slot01_ex_launcher",
                                                        "n_ok_window", "n_failed_window", "error")}), flush=True)
    res["finished"] = int(time.time())
    res["calls_ledger"] = R.LEDGER
    json.dump(res, open(a.out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
