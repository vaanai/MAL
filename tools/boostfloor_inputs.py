#!/usr/bin/env python3
"""EXP-024 Look 1 input producers: P5 (Am.1 sources 2 and 3, the cross-source check), P7 (section 10, with Am.1 line B), E1 (EXP-022
section 8) and the section 13 BOOST PDA fetch. Outcome-blind: they compute no fill, exit or P&L and print counts and hashes only.

    python -m tools.boostfloor_inputs p5 --look 1          # gettx_v.jsonl, cross_source.json (line A), line_a_sample.jsonl, p7_sample.jsonl
    python -m tools.boostfloor_inputs account --look 1     # account_v0.json from p5/account/map.json (exp012_forward_vmap fetch --new)
    python -m tools.boostfloor_inputs p7 --look 1          # p7/pricing_check.json: the two tier lines and line B (buy side: Am.6)
    python -m tools.boostfloor_inputs boost-pda --look 1   # p5/boost_pda.jsonl: per traded pool, the BOOST vault authority's slices
    python -m tools.boostfloor_inputs e1 --source FILE     # e1/calibration.json: a probe_sim_calibration output, copied byte for byte

Order after the EXP-012 FINAL (A): forward_v_join join (into p5/vjoin); h5_forward_extract forward (into look1/extract); boostfloor_read
classify; p5; account; p7; boost-pda; boostfloor_read precount; e1; boostfloor_read look. Every mode refuses before the FINAL marker
is in the external FINAL ledger, refuses Look 2, runs boostfloor_read.integrity() (prereg pins, monitor blob, frozen files) and records
the head in its output, writes each file once (O_EXCL), and takes no flag that changes a number. `e1` takes one path, the E1 record to
copy; it refuses a file written before 2026-10-16T06:13Z (section 8.1(c), the cron run).

What P5 fetches (getTransaction, maxSupportedTransactionVersion 1, finalized; decoded with observe/trade_decode.records_from_logs
event_v=True, rows keyed by (slot, signature, event_index)):
  - for every Look 1 universe pool, its s0 print, and for every trigger pool every landing- and exit-state print any leg of the read
    prices (found with boostfloor_read.price_cell(touch=...), which collects indices and prices nothing), whose joined ev V is absent;
  - the cross-source sample: for the first 100 triggers by ascending sha256("signature:event_index") of the trigger print (found with the
    joined V), the s0, landing-state and exit-state prints that price D;
  - P7's 1,000 prints, sampled evenly (index floor(j * N / 1000)) from every print of the window's V-range pools ordered by (s0 time,
    mint, path order);
  - when line A fails, every other print the read prices (Am.1: then getTransaction is used for every print section 4 fetches).
Ids stay in the files. stdout gets counts and sha256s.
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import base64
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from tools import boostfloor_read as br
from tools import boostfloor_score as bf

LINE_A_TRIGGERS = 100
LINE_A_MAX_NOT_COMPARABLE = 0.01  # at most 1% of the sample (3 of 300) not comparable
P7_N = 1_000
P7_SELL_MIN, P7_BUY_MIN = 0.75, 0.90  # tier lines: share of sells / buys within 1 bp
LINE_B_MIN = 0.99  # line B: share of each side's comparable events within 1 bp or 2 units of the integer law
BP = 1e-4
LINE_B_UNITS = 2
TX_VERSION = 1  # Am.1: maxSupportedTransactionVersion 1
MAX_CALLS = 30_000
PDA_SIG_LIMIT = 100
PDA_VERIFY_FETCHES = 8
ACCOUNT_NOT_BEFORE = "2026-10-16T00:00:00Z"  # section 4: the account map is fetched at or after this instant
E1_NOT_BEFORE = datetime(2026, 10, 16, 6, 13, tzinfo=timezone.utc)  # section 8.1(c): the E1 record is the cron run's, written at or after
DECODED_FIELDS = ("side", "ix_name", "sol_lamports", "token_raw", "quote_reserve", "base_reserve", "virtual_quote_reserves",
                  "pool_quote_amount", "zero_sol", "pool", "mint")


def _jsonl(recs: Iterable[Mapping[str, Any]]) -> bytes:
    return "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in recs).encode()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise br.Refused(f"{path} is missing")
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ---- the P5 plan (outcome-blind) ------------------------------------------------------------------------------------------
def leg_configs(sps: float) -> list[dict[str, Any]]:
    """Every price_cell configuration the look runs on a trigger pool (deciding, report-only, START, the sell retries)."""
    out = [dict(cell=c) for c in br.CELLS.values()] + [dict(kw) for kw in br.REPORT_ONLY.values()] + [dict(cell=br.CELLS["D"], bound="start")]
    step = math.ceil(br.SELL_RETRY_S / sps - 1e-9)
    out += [dict(cell=br.CELLS["D"], sell_delay_slots=k * step) for k in range(br.SELL_RETRY_MAX)]
    return out


def touched(p: br.FwdPool, s: br.Struct, configs: Sequence[Mapping[str, Any]]) -> set[int]:
    idx: set[int] = set()
    for kw0 in configs:
        kw = {k: v for k, v in kw0.items() if k not in ("corr", "stake")}
        br.price_cell(p, s, kw.pop("cell"), rbar=0.0, touch=idx, **kw)
    return idx


def plan_p5(pools: Sequence[br.FwdPool], index: Mapping[br.Key, br.PrintRef], good: frozenset[str]) -> dict[str, Any]:
    """Which prints P5 fetches, and the line A and P7 samples. Structure only: no price_cell call without `touch`."""
    needed: dict[Any, set[str]] = {}
    trig: list[tuple[br.FwdPool, br.Struct]] = []
    universe: list[br.FwdPool] = []
    for p in pools:
        s = br.structure(p, good)
        if s.status in ("outside", "v_range") or not p.keys:
            continue
        universe.append(p)
        needed.setdefault(p.keys[0], set()).add("s0")
        if s.status == "trigger":
            trig.append((p, s))
            for i in touched(p, s, leg_configs(s.sps)):
                needed.setdefault(p.keys[i], set()).add("price")
        ctrl = br.control_struct(p, s, good)
        if ctrl is not None:
            for i in touched(p, ctrl, [dict(cell=br.CELLS["D"], exit_s=br.CONTROL_EXIT_S)]):
                needed.setdefault(p.keys[i], set()).add("control")
    ranked, unranked = [], 0
    for p, s in trig:
        ref = index.get(p.keys[s.trigs[0].i])
        if ref is None or ref.signature is None:
            unranked += 1
            continue
        ranked.append((hashlib.sha256(f"{ref.signature}:{ref.event_index}".encode("ascii")).hexdigest(), p, s))
    ranked.sort(key=lambda x: x[0])
    line_a: list[Any] = []
    line_a_isbuy: list[bool] = []  # each line A print's tape side, so line B can count an unresolved one as a miss on its side (Am.6)
    for _, p, s in ranked[:LINE_A_TRIGGERS]:
        idx: set[int] = set()
        br.price_cell(p, s, br.CELLS["D"], rbar=0.0, touch=idx)
        for i in [0] + sorted(idx):
            if p.keys[i] not in line_a:
                line_a.append(p.keys[i])
                line_a_isbuy.append(bool(p.path.isb[i]))
    allp = sorted(((p.s0_bt, p.mint, i, p) for p in universe for i in range(len(p.keys))), key=lambda x: x[:3])
    n = len(allp)
    m = min(P7_N, n)  # all prints when there are fewer than 1,000
    p7 = [(p.keys[i], bool(p.path.isb[i])) for _, _, i, p in (allp[(j * n) // m] for j in range(m))]
    return {"needed": needed, "line_a": line_a, "line_a_isbuy": line_a_isbuy, "p7": p7, "triggers": len(trig), "unranked_triggers": unranked,
            "universe": len(universe)}


# ---- getTransaction ---------------------------------------------------------------------------------------------------------
def decode_tx(rpc: Any, sig: str) -> list[dict[str, Any]] | None:
    """The transaction's trade rows in event_index order (the walker's decoder and convention), or None when the node has no such tx."""
    from observe.trade_decode import records_from_logs

    tx = rpc.call("getTransaction", [sig, {"encoding": "json", "maxSupportedTransactionVersion": TX_VERSION, "commitment": "finalized"}])
    if not tx:
        return None
    logs = (tx.get("meta") or {}).get("logMessages") or []
    return records_from_logs(logs, slot=int(tx.get("slot") or 0), signature=sig, t_recv_ms=0, commitment="finalized", feed="gettx", event_v=True)


def fetch_prints(rpc: Any, keys: Iterable[Any], index: Mapping[br.Key, br.PrintRef], purposes: Mapping[Any, Iterable[str]],
                 decode: Callable[[Any, str], list[dict[str, Any]] | None] = decode_tx) -> list[dict[str, Any]]:
    """One record per key; one call per distinct signature. A key with no unique raw row cannot be fetched (status no_raw_ref)."""
    by_sig: dict[str, list[Any]] = {}
    out: list[dict[str, Any]] = []
    for k in sorted(set(keys)):
        ref = index.get(k)
        if ref is None or ref.signature is None:
            out.append({"key": list(k), "status": "no_raw_ref", "purposes": sorted(purposes.get(k, ()))})
        else:
            by_sig.setdefault(ref.signature, []).append(k)
    for sig in sorted(by_sig):
        try:
            rows, err = decode(rpc, sig), None
        except Exception as e:  # noqa: BLE001 - a failed fetch after the client's retries leaves the print without source 2
            rows, err = None, type(e).__name__
        for k in by_sig[sig]:
            ref = index[k]
            rec: dict[str, Any] = {"key": list(k), "slot": ref.slot, "signature": sig, "event_index": ref.event_index,
                                   "purposes": sorted(purposes.get(k, ()))}
            row = None if rows is None else next((r for r in rows if r.get("event_index") == ref.event_index), None)
            if err:
                rec["status"] = f"fetch_failed:{err}"
            elif row is None:
                rec["status"] = "absent"
            else:
                d = {f: row.get(f) for f in DECODED_FIELDS}
                rec.update(status="ok", decoded=d,
                           fields_equal=[d["sol_lamports"], d["token_raw"], d["quote_reserve"], d["base_reserve"]] == list(k[2:6]))
            out.append(rec)
    return sorted(out, key=lambda r: r["key"])


def line_a(sample: Sequence[Any], index: Mapping[br.Key, br.PrintRef], recs: Mapping[Any, Mapping[str, Any]]) -> dict[str, Any]:
    """Am.1 line A: joined ev V, q and b equal the decode's to the lamport on every compared print; at most 1% not comparable; a print whose
    V already came from the fallback is counted apart. No compared print at all fails the line (nothing confirmed the ev V)."""
    c = {"sampled": len(sample), "compared": 0, "equal": 0, "from_fallback": 0, "not_comparable": 0, "differing_prints": 0}
    per_field = {"virtual_quote_reserves": 0, "quote_reserve": 0, "base_reserve": 0}
    for k in sample:
        ref = index.get(k)
        rec = recs.get(k)
        if ref is None or ref.signature is None:
            c["not_comparable"] += 1
        elif ref.ev_v is None:
            c["from_fallback"] += 1
        elif rec is None or rec.get("status") != "ok":
            c["not_comparable"] += 1
        else:
            d = rec["decoded"]
            c["compared"] += 1
            diffs = [f for f, mine in (("virtual_quote_reserves", ref.ev_v), ("quote_reserve", k[4]), ("base_reserve", k[5])) if d.get(f) != mine]
            for f in diffs:
                per_field[f] += 1
            c["differing_prints" if diffs else "equal"] += 1
    allowed = int(math.floor(LINE_A_MAX_NOT_COMPARABLE * len(sample) + 1e-9))
    return {**c, "differing_per_field": per_field, "not_comparable_allowed": allowed,
            "line_a_pass": c["compared"] > 0 and c["differing_prints"] == 0 and c["not_comparable"] <= allowed}


# ---- P7 ---------------------------------------------------------------------------------------------------------------------
def tier_lines(sample: Sequence[Mapping[str, Any]], v_of: Callable[[Any, Mapping[str, Any]], float | None]) -> dict[str, Any]:
    """Section 10 P7 on tape prints, V by the source order. Sell: tape sol within 1 bp of (q+V) tok / (b+tok) * (1 - tier(q+V, b)). Buy: the
    implied fee 1 - net / sol, net = tok (q+V) / (b - tok), within 1 bp of tier(q+V, b). A print without V is a miss; a zero buy is skipped."""
    out = {}
    for side, need in (("sell", P7_SELL_MIN), ("buy", P7_BUY_MIN)):
        n = match = no_v = skipped = 0
        for rec in sample:
            if rec["isbuy"] != (side == "buy"):
                continue
            _slot, _mint, sol, tok, q, b = rec["key"]
            if side == "buy" and (sol <= 0 or tok <= 0 or tok >= b):
                skipped += 1
                continue
            n += 1
            v = v_of(br.content_key(*rec["key"]), rec)
            if v is None:
                no_v += 1
                continue
            Q = q + v
            if side == "sell":
                exp = Q * tok / (b + tok) * (1 - bf.tier_fee(Q, b))
                ok = exp > 0 and abs(sol - exp) <= BP * exp
            else:
                ok = abs((1 - tok * Q / (b - tok) / sol) - bf.tier_fee(Q, b)) <= BP
            match += bool(ok)
        share = match / n if n else None
        out[side] = {"n": n, "match": match, "no_v": no_v, "skipped": skipped, "share": share, "need": need,
                     "pass": share is not None and share >= need}
    return out


LINE_B_BUY_NAMES = frozenset({"buy", "buy_v2"})  # Am.6 (QP-P7-1010 item 2.1): the only comparable buys, by exact ix_name
LINE_B_EXACT_IN_PREFIX = "buy_exact_quote_in"  # v1 and v2: excluded by prefix
LINE_B_BUY_EXCLUSIONS = ("zero_sol", "buy_exact_quote_in", "no_ix_name", "ix_not_listed")  # the causes, in the order they are tested


def buy_exclusion(d: Mapping[str, Any]) -> str | None:
    """Why a raw buy event is in neither line B denominator (one of LINE_B_BUY_EXCLUSIONS), or None when it is comparable. Decided from the
    raw getTransaction decode's `zero_sol` and `ix_name` (forward-1002 rows carry no ix_name; it is the instruction name and carries no
    outcome): any `zero_sol`; a name starting with buy_exact_quote_in; a missing, null or empty name; any name other than buy / buy_v2."""
    if d.get("zero_sol"):
        return "zero_sol"
    name = d.get("ix_name")
    if name is None or name == "":
        return "no_ix_name"
    if str(name).startswith(LINE_B_EXACT_IN_PREFIX):
        return "buy_exact_quote_in"
    return None if name in LINE_B_BUY_NAMES else "ix_not_listed"


def buy_law(Q: int, base_reserve: int, token_raw: int) -> int | None:
    """Am.6 inverse law: the quote a buy of `token_raw` base units pays into the pool, ceil(Q * token_raw / (base_reserve - token_raw)) in
    integers, Q = quote_reserve + V. None when base_reserve <= token_raw, which is a miss. It replaces the forward law for buys."""
    if base_reserve <= token_raw:
        return None
    return -((-Q * token_raw) // (base_reserve - token_raw))


def within_b(got: int, law: int) -> bool:
    """Line B's tolerance: within 2 units (lamports on both sides) or within 1 bp of the law."""
    return abs(got - law) <= LINE_B_UNITS or (law > 0 and abs(got - law) <= BP * law)


def line_b(recs: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Am.1 line B on raw getTransaction events, buy side as amended by Am.6. Match: within 1 bp or within 2 lamports of the integer law.
      sells: pool_quote_amount against (quote_reserve + V) * token_raw // (base_reserve + token_raw) (unchanged);
      buys:  comparable only with no zero_sol and ix_name exactly buy or buy_v2 (else excluded, counted per cause and, within ix_not_listed,
             per name); pool_quote_amount against buy_law(quote_reserve + V, base_reserve, token_raw); base_reserve <= token_raw is a miss.
    An unresolved print (no record, a status other than ok, a decode with no buy/sell side, or a law field that is not an integer) is a
    comparable MISS on its side: the decoded side, else the tape side `isbuy` the caller put on the record, else (side unknown) both sides.
    Each side needs >= 99% of its comparable events; a side with none fails."""
    c: dict[str, dict[str, Any]] = {s: {"comparable": 0, "match": 0, "unresolved": 0} for s in ("sell", "buy")}
    c["buy"].update(excluded=0, excluded_by={x: 0 for x in LINE_B_BUY_EXCLUSIONS}, ix_not_listed_by_name={},
                    by_ix_name={n: {"n": 0, "match": 0} for n in sorted(LINE_B_BUY_NAMES)})
    side_unknown = 0
    for rec in recs:
        d = rec.get("decoded") or {}
        side = d.get("side") if rec.get("status") == "ok" else None
        if side not in ("buy", "sell"):
            tape = rec.get("isbuy")
            sides = ("buy",) if tape is True else ("sell",) if tape is False else ("sell", "buy")
            side_unknown += tape not in (True, False)
            for s in sides:
                c[s]["comparable"] += 1
                c[s]["unresolved"] += 1
            continue
        if side == "buy":
            why = buy_exclusion(d)
            if why is not None:
                c["buy"]["excluded"] += 1
                c["buy"]["excluded_by"][why] += 1
                if why == "ix_not_listed":
                    nm = str(d.get("ix_name"))
                    c["buy"]["ix_not_listed_by_name"][nm] = c["buy"]["ix_not_listed_by_name"].get(nm, 0) + 1
                continue
        c[side]["comparable"] += 1
        q, b, v, pqa, tok = (d.get(f) for f in ("quote_reserve", "base_reserve", "virtual_quote_reserves", "pool_quote_amount", "token_raw"))
        if any(type(x) is not int for x in (q, b, v, pqa, tok)):
            c[side]["unresolved"] += 1
            ok = False
        elif side == "sell":
            ok = b + tok > 0 and within_b(pqa, (q + v) * tok // (b + tok))
        else:
            law = buy_law(q + v, b, tok)
            ok = law is not None and within_b(pqa, law)
        c[side]["match"] += bool(ok)
        if side == "buy":
            c["buy"]["by_ix_name"][d["ix_name"]]["n"] += 1
            c["buy"]["by_ix_name"][d["ix_name"]]["match"] += bool(ok)
    for s in c.values():
        s["share"] = s["match"] / s["comparable"] if s["comparable"] else None
        s["pass"] = s["share"] is not None and s["share"] >= LINE_B_MIN
    c["buy"]["ix_not_listed_by_name"] = dict(sorted(c["buy"]["ix_not_listed_by_name"].items()))
    c["side_unknown"] = side_unknown
    return c


def p7_check(lay: br.Layout) -> dict[str, Any]:
    cs = json.loads(lay.cross_source.read_text(encoding="utf-8"))
    use_ev = cs.get("line_a_pass") is True
    recs = {br.content_key(*r["key"]): r for r in _read_jsonl(lay.gettx_v)}
    gettx = br.load_gettx(lay.gettx_v)
    sample = _read_jsonl(lay.p7_sample)
    if len(sample) != cs.get("p7_sampled"):
        raise br.Refused("P7: p7_sample.jsonl does not hold the sample P5 recorded")

    def v_of(k: Any, rec: Mapping[str, Any]) -> float | None:
        if use_ev and type(rec.get("ev_v")) is int:
            return float(rec["ev_v"])
        return float(gettx[k]) if k in gettx else None

    tiers = tier_lines(sample, v_of)
    # every line B key is scored: one with no gettx record is unresolved, a miss on its tape side (Am.6); the P7 sample's side first
    side: dict[Any, Any] = {}
    for r in sample + _read_jsonl(lay.line_a_sample):
        side.setdefault(br.content_key(*r["key"]), r.get("isbuy") if type(r.get("isbuy")) is bool else None)
    b_keys = set(side)
    lb = line_b({**(recs.get(k) or {"key": list(k), "status": "no_record"}), "isbuy": side[k]} for k in sorted(b_keys))
    return {"pass": all(x["pass"] for x in (tiers["sell"], tiers["buy"], lb["sell"], lb["buy"])), "tier": tiers, "line_b": lb,
            "line_b_prints": len(b_keys), "v_source": "ev_then_gettx" if use_ev else "gettx",
            "inputs_sha256": {p.name: br.sha256_file(p) for p in (lay.cross_source, lay.gettx_v, lay.p7_sample, lay.line_a_sample)}}


# ---- BOOST PDA (section 13) -------------------------------------------------------------------------------------------------
def boost_authority(rpc: Any) -> str | None:
    from tools import pump_structure_monitor as M

    res = rpc.call("getAccountInfo", [M.PUMPSWAP_GLOBAL_CONFIG, {"encoding": "base64", "commitment": "finalized"}]) or {}
    val = res.get("value") if isinstance(res, dict) else None
    if not val:
        return None
    return M.decode_global_config(base64.b64decode(val["data"][0])).get("boost_authority")


def pda_record(rpc: Any, pool: str, mint: str, authority_key: str | None) -> dict[str, Any]:
    """The vault authority PDA's signature list (newest 100): slice slots and times; the last slice is the newest signature signed by
    GlobalConfig.boost_authority that runs BoostBuyAndBurn (the A3 monitor's rule). No signature at all: BOOST ended at 0 s."""
    from tools import pump_structure_monitor as M

    sigs = rpc.call("getSignaturesForAddress", [M.boost_vault_authority(pool), {"limit": PDA_SIG_LIMIT, "commitment": "finalized"}]) or []
    ok = sorted((s for s in sigs if s.get("err") is None and s.get("blockTime") is not None), key=lambda s: (s["slot"], s["blockTime"]))
    last_bt = None
    if ok and authority_key:
        for s in reversed(ok[-PDA_VERIFY_FETCHES:]):
            tx = M.fetch_tx(rpc, s["signature"])
            if tx and authority_key in M.tx_signers(tx) and "BoostBuyAndBurn" in M.program_instructions(M.tx_logs(tx), M.PUMPSWAP_PROGRAM):
                last_bt = int(s["blockTime"])
                break
    return {"pool": pool, "mint": mint, "n_slices": len(ok), "slice_slots": [int(s["slot"]) for s in ok],
            "first_bt": int(ok[0]["blockTime"]) if ok else None, "last_bt": last_bt, "last_verified": last_bt is not None,
            "no_slices": not ok, "truncated": len(sigs) >= PDA_SIG_LIMIT}


# ---- E1 ---------------------------------------------------------------------------------------------------------------------
def e1_copy(src: Path, dest: Path) -> dict[str, Any]:
    br.refuse_name(src)
    if not src.is_file():
        raise br.Refused(f"{src} is not a file")
    if src.stat().st_mtime < E1_NOT_BEFORE.timestamp():
        raise br.Refused(f"{src} was written before {E1_NOT_BEFORE.isoformat()} (section 8.1(c): the cron run's E1 record only)")
    if dest.exists():
        raise br.Refused(f"{dest} exists: E1 is recorded once")
    raw = src.read_bytes()
    agg = json.loads(raw)["aggregate"]["faa3192"]["pnl_gap_lamports_live_minus_sim"]
    if type(agg.get("n")) is not int or not isinstance(agg.get("mean"), (int, float)):
        raise br.Refused(f"{src}: aggregate.faa3192.pnl_gap_lamports_live_minus_sim has no integer n and numeric mean")
    br.write_new(dest, raw)
    return {"n": agg["n"], "n_ge_20": agg["n"] >= br.E1_MIN_N, "source": str(src), "source_sha256": hashlib.sha256(raw).hexdigest(),
            "sha256": br.sha256_file(dest)}


# ---- CLI --------------------------------------------------------------------------------------------------------------------
def _rpc(max_calls: int = MAX_CALLS) -> Any:
    from tools import pump_structure_monitor as M

    return M.RpcClient(M.DEFAULT_RPC, max_calls=max_calls)


def run_p5(lay: br.Layout, rpc: Any, good: frozenset[str], loaded: tuple[list[br.FwdPool], dict[br.Key, br.PrintRef]],
           decode: Callable[[Any, str], list[dict[str, Any]] | None] = decode_tx) -> dict[str, Any]:
    for f in (lay.gettx_v, lay.cross_source, lay.line_a_sample, lay.p7_sample):
        if f.exists():
            raise br.Refused(f"{f} exists: P5 runs once")
    pools, index = loaded
    plan = plan_p5(pools, index, good)
    purposes: dict[Any, set[str]] = {k: set(v) for k, v in plan["needed"].items()}
    for k in plan["line_a"]:
        purposes.setdefault(k, set()).add("line_a")
    for k, _ in plan["p7"]:
        purposes.setdefault(k, set()).add("p7")
    first = [k for k, why in purposes.items() if (why & {"line_a", "p7"}) or index.get(k) is None or index[k].ev_v is None]
    recs = {br.content_key(*r["key"]): r for r in fetch_prints(rpc, first, index, purposes, decode)}
    la = line_a(plan["line_a"], index, recs)
    if not la["line_a_pass"]:  # Am.1: getTransaction for every print section 4 fetches
        rest = [k for k in plan["needed"] if k not in recs]
        recs.update({br.content_key(*r["key"]): r for r in fetch_prints(rpc, rest, index, purposes, decode)})
    br.write_new(lay.gettx_v, _jsonl(recs[k] for k in sorted(recs)))
    br.write_new(lay.line_a_sample, _jsonl({"key": list(k), "isbuy": isb} for k, isb in zip(plan["line_a"], plan["line_a_isbuy"])))
    br.write_new(lay.p7_sample, _jsonl({"key": list(k), "isbuy": isb, "ev_v": index[k].ev_v if k in index else None} for k, isb in plan["p7"]))
    status: dict[str, int] = {}
    for r in recs.values():
        status[r["status"].split(":")[0]] = status.get(r["status"].split(":")[0], 0) + 1
    out = {**la, "universe_pools": plan["universe"], "triggers": plan["triggers"], "unranked_triggers": plan["unranked_triggers"],
           "needed_prints": len(plan["needed"]), "needed_without_ev": sum(1 for k in plan["needed"] if index.get(k) is None or index[k].ev_v is None),
           "fetched_prints": len(recs), "fetch_status": dict(sorted(status.items())), "p7_sampled": len(plan["p7"]),
           "rpc_calls": int(getattr(rpc, "total_calls", 0)), "gettx_sha256": br.sha256_file(lay.gettx_v),
           "line_a_sample_sha256": br.sha256_file(lay.line_a_sample), "p7_sample_sha256": br.sha256_file(lay.p7_sample),
           "written_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    br.write_new(lay.cross_source, (json.dumps(out, indent=1, sort_keys=True) + "\n").encode())
    return out


def run_account(lay: br.Layout) -> dict[str, Any]:
    from tools import exp012_forward_vmap as VM

    mp = lay.account_map
    br.refuse_name(mp)
    if not mp.is_file():
        raise br.Refused(f"{mp} is missing: run exp012_forward_vmap pools/fetch --new into it at or after {ACCOUNT_NOT_BEFORE}")
    if mp.stat().st_mtime < br._ts(ACCOUNT_NOT_BEFORE[:13]):
        raise br.Refused(f"{mp} was written before {ACCOUNT_NOT_BEFORE} (section 4: a new file at or after it)")
    detail = VM.load_detail(mp)
    vmap = json.loads(mp.read_text(encoding="utf-8"))["v"]
    # the vmap tool's own resolver: v_base, or the stored V when the account has no pending counters (DEC-016 Am.5 clarification)
    acct = {p: v0 for p in sorted(set(detail) | set(vmap)) if (v0 := VM._v0(detail, p, vmap.get(p))) is not None}
    br.write_new(lay.account_v0, (json.dumps(acct, sort_keys=True) + "\n").encode())
    return {"pools_in_map": len(detail), "pools_with_v0": len(acct), "map_sha256": br.sha256_file(mp), "sha256": br.sha256_file(lay.account_v0)}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("p5", "account", "p7", "boost-pda"):
        sub.add_parser(c).add_argument("--look", type=int, required=True)
    sub.add_parser("e1").add_argument("--source", type=Path, required=True)
    a = ap.parse_args(argv)
    log = lambda s: print(s, file=sys.stderr, flush=True)  # noqa: E731
    try:
        br.refuse_look(getattr(a, "look", 1))
        from tools.forward_v_join import final_marker

        final_marker(br.FINAL_LEDGER)
        ident = br.integrity()  # the prereg's pins, the monitor blob and the frozen files: every output carries the head it ran at
        lay = br.Layout.for_look(br.ROOT, 1)
        if a.cmd == "e1":
            res = e1_copy(a.source, lay.e1)
        elif a.cmd == "account":
            res = run_account(lay)
        elif a.cmd == "p7":
            if lay.p7.exists():
                raise br.Refused(f"{lay.p7} exists: P7 runs once")
            res = p7_check(lay)
            br.write_new(lay.p7, (json.dumps(res, indent=1, sort_keys=True) + "\n").encode())
            lbc = {s: {f: res["line_b"][s][f] for f in ("comparable", "match", "unresolved", "excluded", "excluded_by", "ix_not_listed_by_name",
                                                           "by_ix_name") if f in res["line_b"][s]} for s in ("sell", "buy")}
            res = {"pass": res["pass"], "line_b_counts": {**lbc, "side_unknown": res["line_b"]["side_unknown"]}, "sha256": br.sha256_file(lay.p7)}
        else:
            good, _ = br._good_bad(br.FORWARD_1002)
            classes = br.load_classes(lay.classes)
            if a.cmd == "p5":
                res = run_p5(lay, _rpc(), good, br.forward_pools(lay, classes, p5_stage=True))
            else:
                if lay.boost_pda.exists():
                    raise br.Refused(f"{lay.boost_pda} exists: written once")
                pools, _ = br.forward_pools(lay, classes)
                rpc = _rpc()
                key = boost_authority(rpc)
                recs = []
                for p in pools:
                    if br.structure(p, good).status != "trigger":
                        continue
                    try:
                        recs.append(pda_record(rpc, p.pool, p.mint, key))
                    except Exception as e:  # noqa: BLE001 - a failed read leaves the pool unknown (counted as ended for no-live)
                        recs.append({"pool": p.pool, "mint": p.mint, "error": type(e).__name__})
                br.write_new(lay.boost_pda, _jsonl(recs))
                res = {"pools": len(recs), "errors": sum("error" in r for r in recs), "authority_found": key is not None,
                       "sha256": br.sha256_file(lay.boost_pda)}
        res["head"] = ident["head"]
        print(json.dumps(res, indent=1, sort_keys=True, default=str))
        return 0
    except br.Refused as e:
        log(f"REFUSED: {e}")
        return 2
    except Exception as e:  # noqa: BLE001 - forward_v_join's own Refused
        if type(e).__name__ == "Refused":
            log(f"REFUSED: {e}")
            return 2
        raise


if __name__ == "__main__":
    sys.exit(main())
