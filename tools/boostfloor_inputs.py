#!/usr/bin/env python3
"""EXP-024 Look 1 input producers: P5 (Am.1 sources 2 and 3, the cross-source check), P7 (section 10, with Am.1 line B), E1 (EXP-022
section 8) and the section 13 BOOST PDA fetch. Outcome-blind: they compute no fill, exit or P&L and print counts and hashes only.

    python -m tools.boostfloor_inputs p5 --look 1          # gettx_v.jsonl, cross_source.json (line A), line_a_sample.jsonl, p7_sample.jsonl
    python -m tools.boostfloor_inputs account --look 1     # account_v0.json from p5/account/map.json (exp012_forward_vmap fetch --new)
    python -m tools.boostfloor_inputs p7 --look 1          # p7/pricing_check.json: the two tier lines and line B
    python -m tools.boostfloor_inputs boost-pda --look 1   # p5/boost_pda.jsonl: per traded pool, the BOOST vault authority's slices
    python -m tools.boostfloor_inputs e1 --source FILE     # e1/calibration.json: a probe_sim_calibration output, copied byte for byte

Order after the EXP-012 FINAL (A): forward_v_join join (into p5/vjoin); h5_forward_extract forward (into look1/extract); boostfloor_read
classify; p5; account; p7; boost-pda; boostfloor_read precount; e1; boostfloor_read look. Every mode refuses before the FINAL marker
is in the external FINAL ledger, refuses Look 2, writes each file once (O_EXCL), and takes no flag that changes a number. `e1` takes one
path, the E1 record to copy.

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
    for _, p, s in ranked[:LINE_A_TRIGGERS]:
        idx: set[int] = set()
        br.price_cell(p, s, br.CELLS["D"], rbar=0.0, touch=idx)
        for k in [p.keys[0]] + [p.keys[i] for i in sorted(idx)]:
            if k not in line_a:
                line_a.append(k)
    allp = sorted(((p.s0_bt, p.mint, i, p) for p in universe for i in range(len(p.keys))), key=lambda x: x[:3])
    n = len(allp)
    m = min(P7_N, n)  # all prints when there are fewer than 1,000
    p7 = [(p.keys[i], bool(p.path.isb[i])) for _, _, i, p in (allp[(j * n) // m] for j in range(m))]
    return {"needed": needed, "line_a": line_a, "p7": p7, "triggers": len(trig), "unranked_triggers": unranked, "universe": len(universe)}


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


def line_b(recs: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Am.1 line B on raw getTransaction events: sells' pool_quote_amount against (quote_reserve + V) base_in // (base_reserve + base_in);
    buys' token_raw against base_reserve qin // (quote_reserve + V + qin), excluding buy_exact_quote_in* and zero_sol; a buy with no
    ix_name is not comparable. Match: within 1 bp or within 2 units. Each side needs >= 99% of its comparable events; none fails it."""
    c = {s: {"comparable": 0, "match": 0, "not_comparable": 0, "excluded": 0} for s in ("sell", "buy")}
    for rec in recs:
        d = rec.get("decoded") or {}
        side = d.get("side")
        if rec.get("status") != "ok" or side not in ("buy", "sell"):
            continue
        q, b, v, pqa, tok = (d.get(f) for f in ("quote_reserve", "base_reserve", "virtual_quote_reserves", "pool_quote_amount", "token_raw"))
        if side == "buy" and (d.get("zero_sol") or str(d.get("ix_name") or "").startswith("buy_exact_quote_in")):
            c[side]["excluded"] += 1
            continue
        if any(type(x) is not int for x in (q, b, v, pqa, tok)) or (side == "buy" and not d.get("ix_name")):
            c[side]["not_comparable"] += 1
            continue
        if side == "sell":
            got, law = pqa, (q + v) * tok // (b + tok)
        else:
            got, law = tok, b * pqa // (q + v + pqa)
        c[side]["comparable"] += 1
        c[side]["match"] += abs(got - law) <= LINE_B_UNITS or (law > 0 and abs(got - law) <= BP * law)
    for s in c.values():
        s["share"] = s["match"] / s["comparable"] if s["comparable"] else None
        s["pass"] = s["share"] is not None and s["share"] >= LINE_B_MIN
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
    b_keys = {br.content_key(*r["key"]) for r in _read_jsonl(lay.line_a_sample)} | {br.content_key(*r["key"]) for r in sample}
    lb = line_b(recs[k] for k in sorted(b_keys) if k in recs)
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
    if dest.exists():
        raise br.Refused(f"{dest} exists: E1 is recorded once")
    raw = src.read_bytes()
    agg = json.loads(raw)["aggregate"]["faa3192"]["pnl_gap_lamports_live_minus_sim"]
    if type(agg.get("n")) is not int or not isinstance(agg.get("mean"), (int, float)):
        raise br.Refused(f"{src}: aggregate.faa3192.pnl_gap_lamports_live_minus_sim has no integer n and numeric mean")
    br.write_new(dest, raw)
    return {"n": agg["n"], "n_ge_20": agg["n"] >= br.E1_MIN_N, "source_sha256": hashlib.sha256(raw).hexdigest(), "sha256": br.sha256_file(dest)}


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
    br.write_new(lay.line_a_sample, _jsonl({"key": list(k)} for k in plan["line_a"]))
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
            res = {"pass": res["pass"], "sha256": br.sha256_file(lay.p7)}
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
