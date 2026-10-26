#!/usr/bin/env python3
"""dbc_frontend_attrib (lane P52, P36 proposal R1): Meteora DBC front-end attribution and early-slot competition counts. COUNTS ONLY. PAPER/RESEARCH ONLY.

No pricing, no P&L, no wallet, no key. Public unkeyed RPC only (api.mainnet-beta), 0 Helius.

DATA GUARD: tape rows only through tools.dbc_price (exploration hours [2026-10-14T00, 2026-10-17T00) under its BACKFILL_ROOT; every other hour, the spent
[10-17, 10-24) block, the DBC forward window and fwdv1/ are refused by that guard). This tool never enables the spent, holdout or forward switches.
Fetched transactions are create transactions whose block time is inside the exploration window (checked before and after the fetch).

Pipeline
  fetch  : (1) exploration creates from the tape; (2) config accounts missing from the cached P17 fetch -> getMultipleAccounts; (3) on-chain PartnerMetadata
           (PDA ["partner_metadata", fee_claimer]) of every partner -> getMultipleAccounts; (4) a seeded sample of create transactions -> getTransaction
           (payer, wrapper program, name/symbol/uri from the create instruction). Writes <out>/attrib_cache.json and a call ledger.
  table  : offline. Per pool: early-window metrics from the tape. Per partner: a front-end label. Per (front end, quote): the table and the P36 R1 screen verdict.

Metrics (per create; slot 0 = the create slot; window = trades with block_time - t0 <= WIN_S and slot >= s0):
  * buy volume = amount_in of buy rows (quote atoms, fee included), pooled only within one quote mint;
  * ex-launcher drops buys whose signer is the pool creator or the create tx payer (when the tx was sampled), and every buy inside the create tx itself;
  * share01 = pooled ex-launcher buy volume in slots {s0, s0+1} / pooled ex-launcher buy volume in the window;
  * others01 = distinct ex-launcher buy signers in slots {s0, s0+1} (median per front end);
  * grad24 = curve_complete within 24 h, over creates with t0 <= window end - 24 h; grad_in_create = complete inside the create tx.
R1 screen (P36 CENSUS.md §5 R1, quoted): "a front end goes to the 'bots are here' bin if its pooled slot-0-1 ex-launcher share is >= 30% or its median
other-signers in slots 0-1 is >= 2. Only front ends with >= 200 creates/day and share < 10% go forward as a sub-universe proposal."
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import base64
import json
import os
import random
import statistics
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from tools import dbc_config as C
from tools import dbc_decode as dd
from tools import dbc_price as L

PUBLIC_RPC = "https://api.mainnet-beta.solana.com"
CFG_RAW_CACHE = "/data/mal/hunt-1008/push-1016/hunt28/p17/cfg_raw_all.json"
OUT_DEFAULT = "/data2/mal/p52-dbc-frontend"
WIN_S = 30.0
GRAD_S = 24 * 3600.0
FEE_DEN = 1_000_000_000
BPS_MAX = 10_000
DROP_SHARE = 0.30
DROP_OTHERS = 2.0
CAND_PER_DAY = 200.0
CAND_SHARE = 0.10
PARTNER_META_DISC = bytes([68, 68, 130, 19, 16, 209, 98, 156])
CREATE_IX = {  # discriminator -> (name, index of payer in the instruction accounts)
    bytes([140, 85, 215, 176, 102, 54, 104, 79]): ("initialize_virtual_pool_with_spl_token", 10),
    bytes([169, 118, 51, 78, 145, 110, 220, 155]): ("initialize_virtual_pool_with_token2022", 8),
    bytes([182, 13, 233, 177, 42, 145, 135, 2]): ("initialize_virtual_pool_with_token2022_transfer_hook", 9),
}
QUOTES = {"So11111111111111111111111111111111111111112": "SOL", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
          "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN": "JUP"}
# Vanity mint suffixes (label hints only; a suffix is a grind, not proof of the front end). Applied only when >= SUFFIX_MAJ of a partner's creates share it.
SUFFIX_HINT = {"BAGS": "Bags (mint suffix BAGS)", "jups": "Jupiter Studio (mint suffix jups)", "jupx": "Jupiter Studio (mint suffix jupx)",
               "moon": "mint suffix moon", "bonk": "mint suffix bonk", "pump": "mint suffix pump"}
SUFFIX_MAJ = 0.5


class Refused(Exception):
    pass


# ----------------------------------------------------------------------------------------------------------------------- public RPC (budgeted, ledgered)
class PublicRPC:
    """Unkeyed public RPC with a hard getTransaction cap, a total-call cap and a JSONL ledger (one line per HTTP attempt)."""

    def __init__(self, ledger: str, max_gettx: int = 1500, max_total: int = 1700, url: str = PUBLIC_RPC, spacing_s: float = 0.45, opener=None):
        if "helius" in url.lower() or "api-key" in url.lower() or "?" in url:
            raise Refused("public unkeyed RPC only")
        self.url, self.ledger, self.max_gettx, self.max_total, self.spacing_s = url, ledger, max_gettx, max_total, spacing_s
        self.opener = opener or self._http
        self._last = 0.0
        self.n_total, self.n_gettx = self._count()

    def _count(self) -> tuple[int, int]:
        tot = gtx = 0
        if os.path.exists(self.ledger):
            for line in open(self.ledger):
                tot += 1
                gtx += json.loads(line).get("m") == "getTransaction"
        return tot, gtx

    def _http(self, body: bytes) -> dict[str, Any]:
        req = urllib.request.Request(self.url, data=body, headers={"content-type": "application/json", "user-agent": "mal-p52"})
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.load(r)

    def call(self, method: str, params: list, tag: str = "") -> Any:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        err: Any = None
        for attempt in range(5):
            if self.n_total >= self.max_total or (method == "getTransaction" and self.n_gettx >= self.max_gettx):
                raise Refused(f"budget reached: total {self.n_total}/{self.max_total}, getTransaction {self.n_gettx}/{self.max_gettx}")
            wait = self.spacing_s - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            self.n_total += 1
            self.n_gettx += method == "getTransaction"
            with open(self.ledger, "a") as f:
                f.write(json.dumps({"t": int(time.time()), "m": method, "tag": tag, "attempt": attempt}) + "\n")
            try:
                d = self.opener(body)
                if "error" in d:
                    err = d["error"]
                    if d["error"].get("code") in (429, -32429) or "rate" in str(d["error"]).lower():
                        time.sleep(5 * (attempt + 1))
                        continue
                    return {"error": d["error"]}
                return d.get("result")
            except Exception as e:  # noqa: BLE001
                err = str(e)
                time.sleep(5 * (attempt + 1))
        return {"error": err}


# ----------------------------------------------------------------------------------------------------------------------- decoders
def partner_metadata_pda(fee_claimer: str) -> str:
    from solders.pubkey import Pubkey  # venv only

    pda, _ = Pubkey.find_program_address([b"partner_metadata", bytes(Pubkey.from_string(fee_claimer))], Pubkey.from_string(dd.PROGRAM))
    return str(pda)


def _borsh_str(b: bytes, off: int) -> tuple[str, int]:
    n = int.from_bytes(b[off:off + 4], "little")
    if off + 4 + n > len(b):
        raise ValueError("short string")
    return b[off + 4:off + 4 + n].decode("utf-8", "replace"), off + 4 + n


def decode_partner_metadata(data: bytes) -> dict[str, str]:
    """Anchor PartnerMetadata: disc(8) fee_claimer(32) padding(6 x u128) name website logo (borsh strings)."""
    if data[:8] != PARTNER_META_DISC:
        raise ValueError("not PartnerMetadata")
    off = 8
    fc = dd.b58(data[off:off + 32])
    off += 32 + 96
    name, off = _borsh_str(data, off)
    web, off = _borsh_str(data, off)
    logo, off = _borsh_str(data, off)
    return {"fee_claimer": fc, "name": name.strip(), "website": web.strip(), "logo": logo.strip()}


def fee_schedule(cfg: dict[str, Any]) -> dict[str, Any]:
    """Base-fee schedule of a decoded PoolConfig: start/end bps, mode (0 linear, 1 exponential scheduler, 2 rate limiter), duration in points
    (slots if activation_type 0, seconds if 1), dynamic fee flag. Same formulas as tools/dbc_p21.base_fee_at."""
    bf = cfg["pool_fees"]["base_fee"]
    mode, cliff = int(bf["base_fee_mode"]), int(bf["cliff_fee_numerator"])
    n, freq, red = int(bf["first_factor"]), int(bf["second_factor"]), int(bf["third_factor"])
    if mode == 0:
        end, dur = max(cliff - n * red, 0), n * freq
    elif mode == 1:
        end, dur = cliff * (1.0 - red / BPS_MAX) ** n, n * freq
    else:
        end, dur = cliff, freq
    return {"mode": mode, "start_bps": cliff * BPS_MAX / FEE_DEN, "end_bps": float(end) * BPS_MAX / FEE_DEN, "dur": dur,
            "activation_type": int(cfg["activation_type"]), "dyn": bool(cfg["pool_fees"]["dynamic_fee"]["initialized"])}


def config_info(raw_b64: str) -> dict[str, Any]:
    a = C.decode_b64(raw_b64)
    c = a["config"]
    return {"fee_claimer": c["fee_claimer"], "quote_mint": c["quote_mint"], "token_type": int(c["token_type"]), "kind": a["kind"],
            "thr": int(c["migration_quote_threshold"]), "creator_fee_pct": int(c["creator_trading_fee_percentage"]),
            "pool_creation_fee": int(c["pool_creation_fee"]), "fee": fee_schedule(c)}


def uri_host(uri: str) -> str:
    u = (uri or "").strip()
    if not u:
        return "(empty)"
    if u.startswith("ipfs://"):
        return "ipfs://"
    if u.startswith("ar://"):
        return "ar://"
    if u.startswith("data:"):
        return "data:"
    h = urlparse(u).netloc.lower()
    return h or "(unparsed)"


def parse_create_tx(tx: dict[str, Any], create_sig: str | None = None) -> dict[str, Any]:
    """From a getTransaction(json) result: fee payer, signers, top-level programs, the DBC create instruction (depth, wrapper program, payer, name/symbol/uri)."""
    keys = dd.account_keys(tx)
    msg = (tx.get("transaction") or {}).get("message") or {}
    nsig = int(((msg.get("header") or {}).get("numRequiredSignatures")) or 1)
    tops = [keys[ix["programIdIndex"]] for ix in msg.get("instructions") or [] if ix.get("programIdIndex", 10**9) < len(keys)]
    out: dict[str, Any] = {"fee_payer": keys[0] if keys else None, "signers": keys[:nsig], "top_programs": tops, "create": None,
                           "err": (tx.get("meta") or {}).get("err"), "block_time": tx.get("blockTime")}
    for ix in dd._dbc_instructions(tx, keys):
        hit = CREATE_IX.get(bytes(ix["data"][:8]))
        if not hit:
            continue
        name, pi = hit
        try:
            nm, off = _borsh_str(ix["data"], 8)
            sym, off = _borsh_str(ix["data"], off)
            uri, off = _borsh_str(ix["data"], off)
        except ValueError:
            nm = sym = uri = ""
        acc = ix["accounts"]
        out["create"] = {"ix": name, "depth": ix["depth"], "wrapper": tops[ix["top"]] if ix["top"] < len(tops) else None,
                         "config": acc[0] if acc else None, "creator": acc[2] if len(acc) > 2 else None, "pool": acc[5] if len(acc) > 5 else None,
                         "payer": acc[pi] if len(acc) > pi else None, "name": nm[:64], "symbol": sym[:32], "uri": uri[:300], "uri_host": uri_host(uri)}
        break
    if create_sig is not None:
        sigs = (tx.get("transaction") or {}).get("signatures") or []
        out["sig_ok"] = bool(sigs) and sigs[0] == create_sig
    return out


# ----------------------------------------------------------------------------------------------------------------------- tape (guarded)
def _ts(dt: datetime) -> float:
    return dt.timestamp()


def scan_hours(hours: Iterable[dict[str, Any]], win_end_ts: float, win_s: float = WIN_S, grad_s: float = GRAD_S) -> dict[str, Any]:
    """Pure function over read_hour-shaped dicts in time order. Returns per-pool records {create, early buys (window), complete} and bad-hour info."""
    pools: dict[str, dict[str, Any]] = {}
    bad_hours: list[int] = []
    for i, h in enumerate(hours):
        if h.get("incomplete"):
            bad_hours.append(i)
        for c in h["creates"]:
            if c["pool"] in pools:
                continue
            pools[c["pool"]] = {"create": c, "t0": L.block_time_s(c), "buys": [], "n_trades_win": 0, "complete": None, "hour_idx": i}
        for r in h["trades"]:
            p = pools.get(r.get("pool"))
            if p is None:
                continue
            c = p["create"]
            dt = L.block_time_s(r) - p["t0"]
            if r["slot"] < c["slot"] or dt > win_s:
                continue
            p["n_trades_win"] += 1
            if r.get("side") == "buy":
                p["buys"].append({"slot": int(r["slot"]), "signer": r.get("signer"), "amt": int(r.get("amount_in") or 0), "sig": r.get("signature"),
                                  "quote": r.get("quote_mint")})
        for r in h["completes"]:
            p = pools.get(r.get("pool"))
            if p is None or p["complete"] is not None:
                continue
            p["complete"] = {"dt": L.block_time_s(r) - p["t0"], "sig": r.get("signature")}
    return {"pools": pools, "bad_hours": bad_hours, "win_end_ts": win_end_ts, "win_s": win_s, "grad_s": grad_s}


def load_tape(log=print) -> dict[str, Any]:
    """All exploration hours through the dbc_price guard (read in order, never more than one hour decoded at a time beyond the pool registry)."""
    w0, w1, root = L.window()
    if (w0, w1, os.path.realpath(root)) != (L.EXPLORE_START, L.EXPLORE_END, os.path.realpath(L.BACKFILL_ROOT)):
        raise Refused("dbc_price guard is not in the default exploration mode")
    hours = L.exploration_hours()

    def gen():
        for h in hours:
            d = L.read_hour(L.hour_file(h))
            log(f"{h:%Y-%m-%dT%H} trades {len(d['trades'])} creates {len(d['creates'])} completes {len(d['completes'])} incomplete {d['incomplete']}")
            # keep only fields we use (memory)
            yield {"creates": d["creates"], "completes": d["completes"], "incomplete": d["incomplete"],
                   "trades": [{k: t.get(k) for k in ("pool", "slot", "block_time", "t_recv_ms", "side", "signer", "amount_in", "signature", "quote_mint")}
                              for t in d["trades"]]}

    res = scan_hours(gen(), _ts(L.EXPLORE_END))
    res["hours"] = [f"{h:%Y-%m-%dT%H}" for h in hours]
    res["bad_hours"] = [res["hours"][i] for i in res["bad_hours"]]
    return res


def pool_metrics(p: dict[str, Any], win_end_ts: float, payer: str | None = None, win_s: float = WIN_S, grad_s: float = GRAD_S,
                 bad_hour_idx: set[int] | None = None) -> dict[str, Any]:
    c = p["create"]
    s0, t0, csig = int(c["slot"]), p["t0"], c.get("signature")
    launch = {c.get("creator")} | ({payer} if payer else set())
    win_ok = t0 + win_s <= win_end_ts and not (bad_hour_idx and (p["hour_idx"] in bad_hour_idx or (p["hour_idx"] + 1) in bad_hour_idx))
    in_create = [b for b in p["buys"] if b["sig"] == csig]
    ext = [b for b in p["buys"] if b["sig"] != csig and b["signer"] not in launch]
    early = [b for b in ext if b["slot"] <= s0 + 1]
    comp = p["complete"]
    return {"pool": c["pool"], "config": c.get("config"), "t0": t0, "day": datetime.fromtimestamp(t0, timezone.utc).strftime("%Y-%m-%d"),
            "base_mint": c.get("base_mint"), "win_ok": win_ok, "vol_win": sum(b["amt"] for b in ext), "vol01": sum(b["amt"] for b in early),
            "others01": len({b["signer"] for b in early}), "buyers_win": len({b["signer"] for b in ext}), "n_buys_in_create": len(in_create),
            "launcher_buys_win": sum(1 for b in p["buys"] if b["sig"] != csig and b["signer"] in launch),
            "grad_eligible": t0 + grad_s <= win_end_ts, "grad24": bool(comp and comp["dt"] <= grad_s),
            "grad_in_create": bool(comp and comp["sig"] == csig), "complete_dt": comp["dt"] if comp else None, "n_trades_win": p["n_trades_win"],
            "creator": c.get("creator"), "quote_trade": Counter(b["quote"] for b in p["buys"]).most_common(1)[0][0] if p["buys"] else None}


# ----------------------------------------------------------------------------------------------------------------------- labels
def label_partner(fee_claimer: str, meta: dict[str, str] | None, hosts: Counter, suffixes: Counter, n_creates: int) -> tuple[str, str]:
    """-> (front-end label, evidence). Precedence: on-chain PartnerMetadata name > metadata-uri host majority of sampled create txs > vanity mint suffix majority
    > 'unattributed partner <prefix>'. Hosts that are generic (ipfs gateways, arweave, empty) are not a front end by themselves."""
    if meta and meta.get("name"):
        return meta["name"], f"partner_metadata name (website {meta.get('website') or '-'})"
    generic = {"ipfs://", "ar://", "data:", "(empty)", "(unparsed)", "ipfs.io", "gateway.pinata.cloud", "arweave.net", "gateway.irys.xyz", "cf-ipfs.com",
               "dweb.link", "nftstorage.link", "ipfs.filebase.io", "w3s.link"}
    tot = sum(hosts.values())
    if tot:
        h, k = hosts.most_common(1)[0]
        if h not in generic and k / tot >= 0.5:
            return f"uri:{h}", f"uri host {k}/{tot} sampled create txs"
    if n_creates:
        s, k = suffixes.most_common(1)[0] if suffixes else ("", 0)
        if s in SUFFIX_HINT and k / n_creates >= SUFFIX_MAJ:
            return SUFFIX_HINT[s], f"mint suffix {s} {k}/{n_creates}"
    return f"unattributed partner {fee_claimer[:6]}", (f"generic uri host {hosts.most_common(1)[0][0]} {hosts.most_common(1)[0][1]}/{tot}" if tot else "no sample")


def frontend_map(cache: dict[str, Any], creates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """partner (fee_claimer) -> {label, evidence, n_creates, n_configs}. Configs with no decodable account are partner 'UNKNOWN-CONFIG'."""
    cfg = cache["configs"]
    per = defaultdict(lambda: {"n": 0, "configs": set(), "suf": Counter(), "hosts": Counter(), "wrappers": Counter(), "payers": Counter()})
    for c in creates:
        fc = (cfg.get(c["config"]) or {}).get("fee_claimer") or "UNKNOWN-CONFIG"
        d = per[fc]
        d["n"] += 1
        d["configs"].add(c["config"])
        d["suf"][(c.get("base_mint") or "")[-4:]] += 1
        tx = cache["txs"].get(c["signature"])
        if tx and tx.get("create"):
            d["hosts"][tx["create"]["uri_host"]] += 1
            d["wrappers"][tx["create"]["wrapper"] if tx["create"]["depth"] > 1 else "(top-level DBC)"] += 1
            d["payers"][tx["create"]["payer"] == c.get("creator")] += 1
    out = {}
    for fc, d in per.items():
        lab, ev = label_partner(fc, cache["partner_meta"].get(fc), d["hosts"], d["suf"], d["n"])
        if fc == "UNKNOWN-CONFIG":
            lab, ev = "unknown config (account not decodable)", "config account missing"
        out[fc] = {"label": lab, "evidence": ev, "n_creates": d["n"], "n_configs": len(d["configs"]), "top_suffix": d["suf"].most_common(2),
                   "hosts": d["hosts"].most_common(3), "wrappers": d["wrappers"].most_common(2), "payer_is_creator": dict(d["payers"])}
    return out


# ----------------------------------------------------------------------------------------------------------------------- partner classes (descriptive, POST HOC)
ONE_TRADE_S = 5.0
CLASS_MIN = 0.8
FAST_GRAD_S = 600.0


def partner_class(ms: list[dict[str, Any]]) -> str:
    """Descriptive class of one partner's pools, defined AFTER the first table was seen (post hoc, disclosed):
    'one-trade completer'  : >= 80% of its pools complete <= 5 s after the create;
    'all-graduate fast'    : >= 80% complete within 24 h (eligible pools) and the median completion is <= 600 s;
    'launchpad-like'       : everything else."""
    n = len(ms)
    if not n:
        return "launchpad-like"
    if sum(1 for m in ms if m.get("complete_dt") is not None and m["complete_dt"] <= ONE_TRADE_S) / n >= CLASS_MIN:
        return "one-trade completer"
    ge = [m for m in ms if m["grad_eligible"]]
    dts = [m["complete_dt"] for m in ms if m.get("complete_dt") is not None]
    if ge and sum(m["grad24"] for m in ge) / len(ge) >= CLASS_MIN and dts and statistics.median(dts) <= FAST_GRAD_S:
        return "all-graduate fast"
    return "launchpad-like"


# ----------------------------------------------------------------------------------------------------------------------- table + verdict
def r1_verdict(per_day: float, share: float | None, med_others: float | None) -> str:
    """P36 R1 screen, exactly as declared. 'drop' = bots-are-here bin; 'candidate' = >= 200 creates/day and share < 10%; else 'neither'."""
    if share is None or med_others is None:
        return "no data"
    if share >= DROP_SHARE or med_others >= DROP_OTHERS:
        return "drop (bots are here)"
    if per_day >= CAND_PER_DAY and share < CAND_SHARE:
        return "candidate (needs QP; narrows m10)"
    return "neither (keep watching)"


def build_table(metrics: list[dict[str, Any]], label_of: dict[str, str], quote_of: dict[str, str], n_days: float) -> list[dict[str, Any]]:
    """metrics: pool_metrics rows; label_of: config -> front-end label; quote_of: config -> quote mint. Groups by (label, quote)."""
    g: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for m in metrics:
        q = quote_of.get(m["config"]) or m.get("quote_trade") or "?"
        g[(label_of.get(m["config"], "unknown config (account not decodable)"), QUOTES.get(q, q[:6]))].append(m)
    rows = []
    for (lab, q), ms in g.items():
        w = [m for m in ms if m["win_ok"]]
        v_all, v01 = sum(m["vol_win"] for m in w), sum(m["vol01"] for m in w)
        share = (v01 / v_all) if v_all > 0 else None
        med = statistics.median([m["others01"] for m in w]) if w else None
        ge = [m for m in ms if m["grad_eligible"]]
        days = Counter(m["day"] for m in ms)
        per_day = len(ms) / n_days
        rows.append({"front_end": lab, "quote": q, "creates": len(ms), "per_day": round(per_day, 1), "by_day": dict(sorted(days.items())),
                     "n_window": len(w), "n_with_buys": sum(1 for m in w if m["vol_win"] > 0), "share01": share, "median_others01": med,
                     "mean_others01": (sum(m["others01"] for m in w) / len(w)) if w else None,
                     "frac_any_other01": (sum(1 for m in w if m["others01"] > 0) / len(w)) if w else None,
                     "grad24": (sum(m["grad24"] for m in ge) / len(ge)) if ge else None, "n_grad_eligible": len(ge),
                     "grad_in_create": (sum(m["grad_in_create"] for m in ms) / len(ms)), "buys_in_create": sum(m["n_buys_in_create"] > 0 for m in ms) / len(ms),
                     "complete_le5s": sum(1 for m in ms if m.get("complete_dt") is not None and m["complete_dt"] <= 5) / len(ms),
                     "median_complete_s": statistics.median(dts) if (dts := [m["complete_dt"] for m in ms if m.get("complete_dt") is not None]) else None,
                     "median_buyers_win": statistics.median([m.get("buyers_win", 0) for m in w]) if w else None,
                     "median_trades_win": statistics.median([m.get("n_trades_win", 0) for m in w]) if w else None,
                     "n_creators": len({m.get("creator") for m in ms}),
                     "verdict": r1_verdict(per_day, share, med)})
    tot = Counter()
    for r in rows:
        tot[r["front_end"]] += r["creates"]
    for r in rows:
        r["per_day_all_quotes"] = round(tot[r["front_end"]] / n_days, 1)
    rows.sort(key=lambda r: -r["creates"])
    return rows


# ----------------------------------------------------------------------------------------------------------------------- fetch
def choose_sample(creates: list[dict[str, Any]], partner_of: dict[str, str], k_big: int, big_min: int, budget: int, seed: int = 1) -> list[str]:
    """Seeded: up to k_big create sigs from every partner with >= big_min creates, then a uniform random fill from the rest, up to `budget` sigs."""
    rng = random.Random(seed)
    byp = defaultdict(list)
    for c in creates:
        byp[partner_of.get(c["config"], "UNKNOWN-CONFIG")].append(c["signature"])
    chosen: list[str] = []
    for p in sorted(byp):
        sigs = sorted(byp[p])
        if len(sigs) >= big_min:
            chosen += rng.sample(sigs, min(k_big, len(sigs)))
    rest = sorted(set(c["signature"] for c in creates) - set(chosen))
    rng.shuffle(rest)
    chosen += rest[: max(0, budget - len(chosen))]
    return chosen[:budget]


def fetch(out: Path, n_tx: int, k_big: int, big_min: int, max_gettx: int, log=print) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    cache_p = out / "attrib_cache.json"
    cache = json.loads(cache_p.read_text()) if cache_p.exists() else {"configs": {}, "partner_meta": {}, "partner_meta_missing": [], "txs": {}}
    rpc = PublicRPC(str(out / "p52_calls.jsonl"), max_gettx=max_gettx, max_total=max_gettx + 200)
    tape = load_tape(log=log)
    creates = [p["create"] for p in tape["pools"].values()]
    raw = json.loads(Path(CFG_RAW_CACHE).read_text())
    need = sorted({c["config"] for c in creates})
    for k in need:
        if k in raw and k not in cache["configs"]:
            try:
                cache["configs"][k] = config_info(raw[k]["data_b64"])
            except (ValueError, KeyError):
                pass
    miss = [k for k in need if k not in cache["configs"]]
    log(f"configs needed {len(need)}, from cache {len(need) - len(miss)}, to fetch {len(miss)}")
    for i in range(0, len(miss), 100):
        res = rpc.call("getMultipleAccounts", [miss[i:i + 100], {"encoding": "base64", "commitment": "finalized"}], "cfg")
        for k, v in zip(miss[i:i + 100], (res or {}).get("value") or []):
            if v:
                try:
                    cache["configs"][k] = config_info(v["data"][0])
                except (ValueError, KeyError):
                    pass
    partners = sorted({v["fee_claimer"] for v in cache["configs"].values()} - set(cache["partner_meta"]) - set(cache["partner_meta_missing"]))
    pdas = {partner_metadata_pda(p): p for p in partners}
    keys = sorted(pdas)
    for i in range(0, len(keys), 100):
        res = rpc.call("getMultipleAccounts", [keys[i:i + 100], {"encoding": "base64", "commitment": "finalized"}], "pmeta")
        for k, v in zip(keys[i:i + 100], (res or {}).get("value") or []):
            if v and v.get("owner") == dd.PROGRAM:
                try:
                    cache["partner_meta"][pdas[k]] = decode_partner_metadata(base64.b64decode(v["data"][0]))
                    continue
                except ValueError:
                    pass
            cache["partner_meta_missing"].append(pdas[k])
    log(f"partners {len(partners)} with on-chain metadata {len(cache['partner_meta'])}")
    cache_p.write_text(json.dumps(cache, sort_keys=True))
    partner_of = {k: v["fee_claimer"] for k, v in cache["configs"].items()}
    sample = [s for s in choose_sample(creates, partner_of, k_big, big_min, n_tx) if s not in cache["txs"]]
    lo, hi = _ts(L.EXPLORE_START), _ts(L.EXPLORE_END)
    by_sig = {c["signature"]: c for c in creates}
    log(f"create txs to fetch {len(sample)}")
    for j, s in enumerate(sample):
        if not (lo <= L.block_time_s(by_sig[s]) < hi):
            raise Refused(f"create outside exploration window: {s}")
        try:
            tx = rpc.call("getTransaction", [s, {"encoding": "json", "maxSupportedTransactionVersion": 1, "commitment": "confirmed"}], "create_tx")
        except Refused as e:
            log(f"stop: {e}")
            break
        if not isinstance(tx, dict) or "error" in tx or not tx.get("transaction"):
            cache["txs"][s] = {"fetch_error": str((tx or {}).get("error") if isinstance(tx, dict) else tx)[:200]}
            continue
        if tx.get("blockTime") is not None and not (lo <= tx["blockTime"] < hi):
            raise Refused(f"fetched tx outside exploration window: {s}")
        cache["txs"][s] = parse_create_tx(tx, s)
        if j % 50 == 49:
            cache_p.write_text(json.dumps(cache, sort_keys=True))
            log(f"  {j + 1}/{len(sample)} calls total {rpc.n_total} getTransaction {rpc.n_gettx}")
    cache_p.write_text(json.dumps(cache, sort_keys=True))
    return {"rpc_total": rpc.n_total, "rpc_gettx": rpc.n_gettx, "txs": len(cache["txs"]), "configs": len(cache["configs"]), "partner_meta": len(cache["partner_meta"])}


def table(out: Path, log=print) -> dict[str, Any]:
    cache = json.loads((out / "attrib_cache.json").read_text())
    tape = load_tape(log=log)
    hours = tape["hours"]
    bad = {hours.index(h) for h in tape["bad_hours"]}
    creates = [p["create"] for p in tape["pools"].values()]
    fmap = frontend_map(cache, creates)
    label_of = {c["config"]: fmap[(cache["configs"].get(c["config"]) or {}).get("fee_claimer") or "UNKNOWN-CONFIG"]["label"] for c in creates}
    quote_of = {k: v["quote_mint"] for k, v in cache["configs"].items()}
    payer_of = {s: (t.get("create") or {}).get("payer") for s, t in cache["txs"].items() if t.get("create")}
    metrics = [pool_metrics(p, tape["win_end_ts"], payer_of.get(p["create"]["signature"]), bad_hour_idx=bad) for p in tape["pools"].values()]
    n_days = (L.EXPLORE_END - L.EXPLORE_START).total_seconds() / 86400.0
    rows = build_table(metrics, label_of, quote_of, n_days)
    by_partner = defaultdict(list)
    for m in metrics:
        by_partner[(cache["configs"].get(m["config"]) or {}).get("fee_claimer") or "UNKNOWN-CONFIG"].append(m)
    pclass = {fc: partner_class(ms) for fc, ms in by_partner.items()}
    for fc, v in fmap.items():
        v["class"] = pclass.get(fc)
    lab_class = defaultdict(Counter)
    for fc, ms in by_partner.items():
        lab_class[fmap[fc]["label"]][pclass[fc]] += len(ms)
    for r in rows:
        r["partner_classes"] = lab_class[r["front_end"]].most_common()
    class_of = {m["config"]: "CLASS " + pclass[(cache["configs"].get(m["config"]) or {}).get("fee_claimer") or "UNKNOWN-CONFIG"] for m in metrics}
    class_rows = build_table(metrics, class_of, quote_of, n_days)
    fee = defaultdict(list)
    for c in creates:
        ci = cache["configs"].get(c["config"])
        if ci:
            fee[label_of[c["config"]]].append(ci["fee"])
    fees = {}
    for lab, fs in fee.items():
        fees[lab] = {"median_start_bps": statistics.median(f["start_bps"] for f in fs), "median_end_bps": statistics.median(f["end_bps"] for f in fs),
                     "modes": Counter(f["mode"] for f in fs).most_common(), "median_dur": statistics.median(f["dur"] for f in fs),
                     "activation_types": Counter(f["activation_type"] for f in fs).most_common(), "dyn_share": sum(f["dyn"] for f in fs) / len(fs),
                     "share_start_ge_5000bps": sum(f["start_bps"] >= 5000 for f in fs) / len(fs)}
    for r in rows:
        r["fee"] = fees.get(r["front_end"])
    res = {"generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "window": [L.EXPLORE_START.isoformat(), L.EXPLORE_END.isoformat()],
           "n_creates": len(creates), "bad_hours": tape["bad_hours"], "n_txs_sampled": sum(1 for t in cache["txs"].values() if t.get("create")),
           "rows": rows, "class_rows": class_rows, "partners": sorted(({"fee_claimer": k, **v} for k, v in fmap.items()), key=lambda d: -d["n_creates"])}
    (out / "table.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=list))
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--out", default=OUT_DEFAULT)
    f.add_argument("--n-tx", type=int, default=900)
    f.add_argument("--k-big", type=int, default=4)
    f.add_argument("--big-min", type=int, default=30)
    f.add_argument("--max-gettx", type=int, default=1500)
    t = sub.add_parser("table")
    t.add_argument("--out", default=OUT_DEFAULT)
    a = ap.parse_args(argv)
    if a.cmd == "fetch":
        print(json.dumps(fetch(Path(a.out), a.n_tx, a.k_big, a.big_min, a.max_gettx)), flush=True)
    else:
        res = table(Path(a.out))
        print(json.dumps({k: res[k] for k in ("n_creates", "bad_hours", "n_txs_sampled")}), flush=True)
        for r in res["rows"][:40]:
            print(json.dumps({k: r[k] for k in ("front_end", "quote", "creates", "per_day", "n_window", "share01", "median_others01", "grad24", "verdict")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
