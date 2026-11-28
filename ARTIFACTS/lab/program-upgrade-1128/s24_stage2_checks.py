"""STAGE 2 additions (QP requirement on PR #1405 and QP-RULING-1115-a3-repin-dual-ADD1), offline over the cached chain samples. Structure only.
Inputs: A3_BASE (a combined cache dir: raw/ = A1 + A2 + M + M2 + the s22 CompleteEvent txs, raw_pre/ = P; see run_stage2.sh), UPGRADE_OUT.
Writes $UPGRADE_OUT/s24_stage2_checks.json. Parts:
 (1) FIELD-BY-FIELD equality (not value containment) for CompleteEvent, CompletePumpAmmMigrationEvent and CreatePoolEvent: every field the frozen decoder
     emits is compared with the field of the same meaning in the independent IDL walker (explicit mapping below), counts per type and per field. IDL fields the
     frozen decoder does not emit are listed (named, not compared). The frozen zero-pubkey -> WSOL mapping of quote_mint is applied to the walker value
     explicitly (it is by design in bf._quote_or_wsol) and counted.
 (2) UNDECODABLE `Program data` lines: every Program data line whose invoke-stack top is pump, PumpSwap or fees, in the successful sampled txs (primary) and in
     the failed txs of the same cached blocks (secondary), strict base64 (padding added, validate=True): categories bad_base64, shorter_than_8, unknown_disc,
     layout_short (walker runs out of bytes), trailing_other (remainder not 0 and not 8). Each hit is listed with its signature, program, disc and length.
 (3) DepositEvent: count, and the trailing-byte count of every DepositEvent seen, post and pre.
 (4) Fee-triple rule R (ADD1 B): R = declared pre set (P + M_pre, from s17 pre) U the stage-1 s19 extended pre set U {20,0,5}; every post triple in R, or
     (all three components occur in R and share <= 0.5% of post WSOL-only Buy+Sell events).
"""
import collections, gzip, json, os, sys, base64
from pathlib import Path
sys.argv = [sys.argv[0]]
exec(open("s06_parity.py").read().split("acc = dict(")[0])
BASE = Path(os.environ.get("A3_BASE", "/data2/mal/scratch/a3-repin-1128"))
OUTD = Path(os.environ.get("UPGRADE_OUT", "out"))
WSOL = td.WSOL_MINT; ZERO = "11111111111111111111111111111111"
MIG = json.load(open(BASE / "raw" / "migrate_index.json"))
PROGS = {"pump", "pump_amm", "pump_fees"}

# frozen key -> IDL field (frozen decoder output on the left)
MAP = {
    "CompleteEvent": {"trader": "user", "mint": "mint", "bonding_curve": "bonding_curve", "event_ts": "timestamp", "quote_mint": "quote_mint"},
    "CompletePumpAmmMigrationEvent": {"trader": "user", "mint": "mint", "token_raw": "mint_amount", "sol_lamports": "sol_amount", "migration_fee": "pool_migration_fee",
                                      "bonding_curve": "bonding_curve", "event_ts": "timestamp", "pool": "pool", "quote_mint": "quote_mint"},
    "CreatePoolEvent": {"pool": "pool", "base_mint": "base_mint", "quote_mint": "quote_mint"},
}
IDL_OF = {"CompleteEvent": "pump", "CompletePumpAmmMigrationEvent": "pump", "CreatePoolEvent": "pump_amm"}
FROZEN_OF = {"CompleteEvent": bf.decode_complete_event, "CompletePumpAmmMigrationEvent": bf.decode_migration_event, "CreatePoolEvent": lambda b: td.decode_program_data(b, event_v=True)}


def strict_b64(line):
    s = line[len("Program data: "):].strip()
    s = s + "=" * (-len(s) % 4)
    try:
        return base64.b64decode(s, validate=True), None
    except Exception as e:  # noqa: BLE001
        return None, type(e).__name__



def classify(pid, ln):
    """None if the Program data line decodes to a whole event of pump / PumpSwap / fees, else (category, details)."""
    blob, err = strict_b64(ln)
    if blob is None: return ("bad_base64", {"err": err})
    if len(blob) < 8: return ("shorter_than_8", {"len": len(blob)})
    nm = EV_BY_PROG[pid].get(blob[:8])
    if nm is None: return ("unknown_disc", {"disc": blob[:8].hex(), "len": len(blob)})
    try:
        ev, end = parse_event(PNAME[pid], nm, blob)
    except Short: return ("layout_short", {"event": nm, "len": len(blob)})
    if len(blob) - end not in (0, 8): return ("trailing_other", {"event": nm, "len": len(blob), "consumed": end})
    return None


def selftest():
    """The classifier must flag each category on a synthetic line (and pass a real event) before its zero counts mean anything."""
    pid = PID["pump"]; good = next(d for d, n in EV_BY_PROG[pid].items() if n == "CompleteEvent") + b"\0" * 136   # CompleteEvent disc + 136 zero bytes = 144
    mk = lambda b: "Program data: " + base64.b64encode(b).decode()
    cases = {"bad_base64": "Program data: !!!notbase64", "shorter_than_8": mk(b"abc"), "unknown_disc": mk(b"\xff" * 8 + b"\0" * 20), "layout_short": mk(good[:100]),
             "trailing_other": mk(good + b"\0" * 3), "ok_complete_event": mk(good)}
    return {k: (None if (r := classify(pid, v)) is None else r[0]) for k, v in cases.items()}


def tx_sets(root, label):
    """yield (src, tx, in_index) for blocks of `root` (all txs) and the migrate / complete-event whole txs."""
    idx = json.load(open(root / "sample_index.json"))["a"]
    for p in sorted((root / "blocks").glob("*.json.gz")):
        blk = json.loads(gzip.open(p).read())["r"]
        for tx in (blk or {}).get("transactions", []):
            sg = tx["transaction"]["signatures"][0]
            meta = tx.get("meta") or {}
            if sg in idx: yield "A", tx
            elif meta.get("err") is not None:
                keys = keys_of(tx)
                if any(k in PID.values() for k in keys): yield "FAILED", tx


def whole_txs(label):
    seen = set(); out = []
    rows = MIG["post" if label == "post" else "pre"]
    extra = []
    s22 = json.load(open(OUTD / f"s22_complete_events_{label}.json")) if (OUTD / f"s22_complete_events_{label}.json").exists() else json.load(open(Path("out") / f"s22_complete_events_{label}.json"))
    for k in s22["kept"]: extra.append(k["sig"])
    for sg in [r["sig"] for r in rows] + extra:
        if sg in seen: continue
        seen.add(sg)
        pth = BASE / "raw" / "txs" / f"{sg}.json.gz"
        if pth.exists():
            r_ = json.loads(gzip.open(pth).read())["r"]
            if r_: out.append(("M" if sg in {r["sig"] for r in rows} else "C", r_))
    return out


def all_txs(label):
    root = BASE / ("raw" if label == "post" else "raw_pre")
    seen = set()
    for src, tx in tx_sets(root, label):
        seen.add(tx["transaction"]["signatures"][0]); yield src, tx
    for src, tx in whole_txs(label):
        sg = tx["transaction"]["signatures"][0]
        if sg in seen: continue
        seen.add(sg); yield src, tx


res = {"classifier_selftest": selftest()}
for label in ("post", "pre"):
    fbf = {t: {"n": 0, "frozen_none": 0, "walker_short": 0, "fields": {f: {"compared": 0, "equal": 0, "unequal": 0, "mapped_zero_to_wsol": 0} for f in m_}, "unequal_examples": []} for t, m_ in MAP.items()}
    und = {"successful_sampled": collections.Counter(), "failed_same_blocks": collections.Counter(), "other_program_lines": 0, "other_program_ids": collections.Counter(), "no_stack_program_lines": 0, "lines_checked": collections.Counter(), "hits": []}
    deposit = {"count": 0, "trailing": collections.Counter(), "blob_len": collections.Counter()}
    ev_names = collections.Counter(); trail_by_type = collections.defaultdict(collections.Counter)
    trip = collections.Counter(); srcs = collections.Counter()
    for src, tx in all_txs(label):
        meta = tx.get("meta") or {}; failed = meta.get("err") is not None
        sg = tx["transaction"]["signatures"][0]; srcs[src] += 1
        cls = "failed_same_blocks" if failed else "successful_sampled"
        logs = meta.get("logMessages") or []
        evs = []
        for ln, (pid, _b) in zip([l for l in logs if l.startswith("Program data: ")], log_events(logs)):
            if pid is None: und["no_stack_program_lines"] += 1; continue
            if pid not in PNAME: und["other_program_lines"] += 1; und["other_program_ids"][pid] += 1; continue
            hit = classify(pid, ln)
            und["lines_checked"][cls] += 1
            if hit:
                und[cls][hit[0]] += 1; und["hits"].append(dict(cat=hit[0], cls=cls, sig=sg, program=PNAME[pid], src=src, **hit[1]))
        if failed: continue   # field equality and event tests are on successful txs only (as stage 1)
        # Events for the field-by-field and event tests are taken ONLY from the self-CPI instruction copy (inner instruction data after the event tag); `evs` is empty until here and the
        # log lines are not added to it (they are used only by the undecodable-line count above). Counts match s06/s17 because each Anchor event has one such copy.
        ms = collections.Counter(b for _, _, b, _ in evs); keys = keys_of(tx)
        for g in meta.get("innerInstructions") or []:
            for ins in g["instructions"]:
                pid = keys[ins["programIdIndex"]] if ins["programIdIndex"] < len(keys) else None
                if pid in PNAME and ins.get("data"):
                    d = b58d(ins["data"])
                    if d[:8] == TAG and len(d) > 8:
                        b = d[8:]
                        if ms.get(b, 0) > 0: ms[b] -= 1
                        else:
                            nm = EV_BY_PROG[pid].get(b[:8])
                            if nm: evs.append((pid, nm, b, "cpi_only"))
        qm = quote_mints(tx) if any(n in ("BuyEvent", "SellEvent") for _, n, _, _ in evs) else set()
        for pid, nm, blob, how in evs:
            idl = PNAME[pid]
            try: ev, end = parse_event(idl, nm, blob)
            except Short: continue
            ev_names[f"{idl}.{nm}"] += 1; trail_by_type[f"{idl}.{nm}"][len(blob) - end] += 1
            if nm == "DepositEvent":
                deposit["count"] += 1; deposit["trailing"][len(blob) - end] += 1; deposit["blob_len"][len(blob)] += 1
            if nm in ("BuyEvent", "SellEvent") and qm == {WSOL}:
                trip[f"{ev['lp_fee_basis_points']},{ev['protocol_fee_basis_points']},{ev['coin_creator_fee_basis_points']}"] += 1
            if nm in MAP and idl == IDL_OF[nm]:
                R = fbf[nm]; R["n"] += 1
                fro = FROZEN_OF[nm](blob)
                if fro is None: R["frozen_none"] += 1; continue
                for fk, ik in MAP[nm].items():
                    exp = ev[ik]; F = R["fields"][fk]; F["compared"] += 1
                    if fk == "quote_mint" and exp == ZERO:
                        exp = td.WSOL_MINT; F["mapped_zero_to_wsol"] += 1
                    if fro.get(fk) == exp: F["equal"] += 1
                    else:
                        F["unequal"] += 1
                        if len(R["unequal_examples"]) < 5: R["unequal_examples"].append({"sig": sg, "field": fk, "frozen": str(fro.get(fk)), "walker": str(exp)})
    for nm, R in fbf.items():
        idlf = [f["name"] for f in TYPES[IDL_OF[nm]][nm]["type"]["fields"]]
        R["idl_fields_not_emitted_by_frozen"] = [f for f in idlf if f not in MAP[nm].values()]
        R["total_unequal"] = sum(F["unequal"] for F in R["fields"].values())
        R["total_compared"] = sum(F["compared"] for F in R["fields"].values())
    und_out = {k: (dict(v) if isinstance(v, collections.Counter) else v) for k, v in und.items()}
    und_out["other_program_ids"] = dict(und["other_program_ids"].most_common(12)); und_out["total_hits_successful_sampled"] = sum(und["successful_sampled"].values()); und_out["total_hits_failed_same_blocks"] = sum(und["failed_same_blocks"].values())
    res[label] = {"tx_sources": dict(srcs), "field_by_field": fbf, "undecodable_program_data": und_out, "deposit_event": {"count": deposit["count"], "trailing": dict(deposit["trailing"]), "blob_len": dict(deposit["blob_len"])},
                  "event_counts": dict(ev_names), "trailing_by_type": {k: dict(v) for k, v in trail_by_type.items()}, "wsol_only_fee_triples": dict(trip)}

# fee rule R: DROPPED by QP-RULING-1128 (PumpSwap and fees unchanged; F5 report-only). The WSOL-only fee triples per side stay in res[...]["wsol_only_fee_triples"].
rule = {"dropped": "QP-RULING-1128"}; tot = 0
res["fee_triple_rule_R"] = rule
(OUTD / "s24_stage2_checks.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=str))
for lab in ("post", "pre"):
    r = res[lab]
    print("==", lab, r["tx_sources"])
    for t, R_ in r["field_by_field"].items(): print("  ", t, "n", R_["n"], "frozen_none", R_["frozen_none"], "compared", R_["total_compared"], "unequal", R_["total_unequal"])
    u = r["undecodable_program_data"]; print("   undecodable successful", u["successful_sampled"], "failed", u["failed_same_blocks"], "lines", u["lines_checked"], "other_prog", u["other_program_lines"], "nostack", u["no_stack_program_lines"])
    print("   deposit", r["deposit_event"])

