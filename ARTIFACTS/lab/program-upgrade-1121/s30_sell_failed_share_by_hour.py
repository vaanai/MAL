"""QP-RULING-1121a section 3 item 3: reproduce QP's per-block-hour failed-share table for WSOL PumpSwap sell and buy_exact_quote_in instructions, with the slippage split
(sell 6004 ExceededSlippage, buy 6040 BuySlippageBelowMinBaseAmountOut), over: this review's A (raw) and P (raw_pre), and the 1115 review's A1 (raw), A2 (raw_a2) and pre (raw_pre).
Same counting rule as s20 (per matching instruction, top-level or inner, account 4 == WSOL; failed tx counts every matching instruction it carries). Offline over cached blocks.
Run: /data/mal/venv/bin/python s30_sell_failed_share_by_hour.py   (writes out/s30_sell_failed_share_by_hour.json). Structure only."""
import collections, gzip, json, sys, os, time
from pathlib import Path
sys.argv = [sys.argv[0]]
src = open("s18_instruction_shapes.py").read()
exec(src.split("res = {}; own_inputs")[0])
B15 = Path("/data2/mal/scratch/a3-repin-1115")
GROUPS = {"A (1121 post)": BASE / "raw", "P (1121 pre)": BASE / "raw_pre", "1115 A1": B15 / "raw", "1115 A2": B15 / "raw_a2", "1115 pre": B15 / "raw_pre"}
SLIP = {"sell": "InstructionError:Custom=6004", "buy_exact_quote_in": "InstructionError:Custom=6040"}
out = {}
for label, root in GROUPS.items():
    c = collections.defaultdict(lambda: collections.Counter())   # (cls, hour) -> ok, bad, slip
    for p in sorted((root / "blocks").glob("*.json.gz")):
        blk = json.loads(gzip.open(p).read())["r"]
        if not blk: continue
        hr = time.strftime("%Y-%m-%dT%HZ", time.gmtime(blk["blockTime"]))
        for tx in blk.get("transactions", []):
            meta = tx.get("meta") or {}
            for where, d, acc in amm_ixs(tx):
                if len(acc) < 5 or acc[4] != WSOL: continue
                cls = DISC[d[:8].hex()]
                if cls == "buy": continue
                k = (cls, hr)
                if meta.get("err") is None: c[k]["ok"] += 1
                else:
                    c[k]["bad"] += 1
                    if errword(meta["err"]) == SLIP[cls]: c[k]["slip"] += 1
    g = {}
    for cls in ("sell", "buy_exact_quote_in"):
        rows = {hr: c[(cls, hr)] for (cl, hr) in sorted(c) if cl == cls}
        tot = collections.Counter()
        for r in rows.values(): tot.update(r)
        def row(r):
            n = r["ok"] + r["bad"]; ex_n = n - r["slip"]; ex_bad = r["bad"] - r["slip"]
            return {"n": n, "failed": r["bad"], "share": r["bad"] / n if n else None, "slip": r["slip"], "ex_slip_failed": ex_bad, "ex_slip_n": ex_n, "ex_slip_share": ex_bad / ex_n if ex_n else None}
        g[cls] = {"ALL": row(tot), "hours": {hr: row(r) for hr, r in rows.items()}}
    out[label] = g
Path(os.environ.get("UPGRADE_OUT", "out") + "/s30_sell_failed_share_by_hour.json").write_text(json.dumps(out, indent=1))
for label, g in out.items():
    for cls in ("sell", "buy_exact_quote_in"):
        a = g[cls]["ALL"]; print(f"{label} {cls} ALL: failed {a['failed']} of {a['n']} share {a['share']:.4f}; slip {a['slip']}; ex-slip {a['ex_slip_failed']} of {a['ex_slip_n']} share {a['ex_slip_share']:.4f}")
    for hr, r in g["sell"]["hours"].items(): print(f"   {label} sell {hr}: {r['failed']}/{r['n']} share {r['share']:.4f} slip {r['slip']} ex-slip {r['ex_slip_share']:.4f}")
a = out["A (1121 post)"]["sell"]["hours"]["2026-11-21T02Z"]; t = out["A (1121 post)"]["sell"]["ALL"]
n, f = t["n"] - a["n"], t["failed"] - a["failed"]; print(f"A sell without 2026-11-21T02Z: {f} of {n} share {f/n:.4f}")
