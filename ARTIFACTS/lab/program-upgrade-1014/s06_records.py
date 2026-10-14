"""Step 6 (offline, no RPC): tabulate the A3 daily records in /data/mal/structure-monitor/daily.jsonl, before and after the pump
redeploy (slot 456494698). Reads the structure-monitor record only (counts of graduations and BOOST structure); no trade data."""
import json
import sys
from pathlib import Path

path = sys.argv[1] if len(sys.argv) > 1 else "/data/mal/structure-monitor/daily.jsonl"
rows = []
for line in open(path):
    r = json.loads(line)
    g, b, qm = r.get("graduations") or {}, r.get("boost") or {}, r.get("quote_mix") or {}
    rows.append(dict(
        run_utc=r["run_utc"], slot_min=g.get("slot_min"), slot_max=g.get("slot_max"), after_pump_deploy=(g.get("slot_min") or 0) > 456494698,
        pump_deploy_slot=(r.get("programs") or {}).get("pump", {}).get("deploy_slot"), global_len=(r.get("accounts") or {}).get("pump_global", {}).get("len"),
        global_sha8=((r.get("accounts") or {}).get("pump_global", {}).get("sha256") or "")[:8],
        n=g.get("n"), migrate_ix=g.get("migrate_ix"), quote=g.get("quote"), mayhem=g.get("mayhem"),
        n_init_boost=(g.get("boost_denominator") or {}).get("n_init_boost"), n_nonmayhem_wsol=(g.get("boost_denominator") or {}).get("n_nonmayhem_wsol"),
        synthetic=(g.get("synthetic") or {}).get("n_synthetic"), synthetic_checked=(g.get("synthetic") or {}).get("n_checked"),
        boost_n_profiled=b.get("n_profiled"), slices=b.get("slices"), budget_sol=(b.get("budget_sol") or {}).get("median"), last_slice_s=b.get("last_slice_after_migrate_s"),
        first_slice_s=b.get("first_slice_after_migrate_s"), quote_mix_n=qm.get("n"), quote_mix_by_quote=qm.get("by_quote"), halt_any=(r.get("halt") or {}).get("any"),
        halt_flags=[k for k, v in ((r.get("halt") or {}).get("flags") or {}).items() if v.get("halt")]))
out = dict(source=path, n_records=len(rows), rows=rows)
(Path(__file__).resolve().parent / "out" / "s06_records.json").write_text(json.dumps(out, indent=1) + "\n")
for x in rows:
    print(x["run_utc"], "post" if x["after_pump_deploy"] else "pre ", "n", x["n"], x["migrate_ix"], x["quote"], "ib", x["n_init_boost"], "/", x["n_nonmayhem_wsol"], "syn", x["synthetic"], "/", x["synthetic_checked"],
          "slices", (x["slices"] or {}).get("median"), "budget", x["budget_sol"], "last", (x["last_slice_s"] or {}).get("median"), (x["last_slice_s"] or {}).get("min"), (x["last_slice_s"] or {}).get("max"), "halt", x["halt_flags"])
