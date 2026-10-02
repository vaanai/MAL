# DEC-017 (c) looser-threshold screen, pre-committed: t_loose = p80 of pooled OOF (same non-interpolating rule as the frozen p90).
# Exploration only: EXP-012 OOF scores + latency run rows at k=1 (no new tape pass). One try, logged before results are printed.
import json, collections
from pathlib import Path
from tools.exp012_latency_sensitivity import _side, load_oof
from tools import mal_result
scores, thr, _doc, _days = load_oof(Path("ARTIFACTS/exp012"))
vals = sorted(scores.values()); n = len(vals)
t80 = vals[round(0.80 * (n - 1))]
t90 = vals[round(0.90 * (n - 1))]
assert abs(t90 - thr) < 1e-12, (t90, thr)
rows = [json.loads(l) for l in open("/data/mal/exp012-latency/rows_by_k.jsonl")]
rows = [r for r in rows if r.get("entry_land_k") == 1]
assert len(rows) == n == 8801 and len({r["mint"] for r in rows}) == n
band = [r for r in rows if t80 <= scores[r["mint"]] < thr]
full = [r for r in rows if scores[r["mint"]] >= t80]
ref = [r for r in rows if scores[r["mint"]] >= thr]
blocks = [{"start_hour": "2026-09-19T01", "end_hour_exclusive": "2026-09-22T00", "host": "mal-research-0", "ledger_owner": "exploration pool"},
          {"start_hour": "2026-09-22T00", "end_hour_exclusive": "2026-09-25T07", "host": "mal-research-0", "ledger_owner": "exploration pool"},
          {"start_hour": "2026-09-25T07", "end_hour_exclusive": "2026-09-28T00", "host": "mal-research-0", "ledger_owner": "exploration pool"}]
mal_result.append_try("/data/mal/ops/tries/tries.jsonl", tool="DEC-017 (c) band screen (manager script)",
    config={"experiment": "EXP-012 looser threshold", "t_loose_rule": "p80 pooled OOF, non-interpolating", "t_loose": t80, "t_frozen": thr, "selection": "OOF", "entry": "k=1 start"},
    data_blocks=blocks, result_path="/data/mal/exp012-band/band_screen.json", role="exploration")
def day_signs(rs):
    d = collections.defaultdict(list)
    for r in rs: d[r["day"]].append(r["press"])
    return {k: round(sum(v) / len(v) / 1e9, 5) for k, v in sorted(d.items())}, len(d)
out = {"t_loose": t80, "t_frozen": thr, "n_oof": n,
       "band": {"n": len(band), **_side(band), "press_per_day": day_signs(band)[0]},
       "full_p80": {"n": len(full), **_side(full)},
       "ref_p90": {"n": len(ref), **_side(ref)}}
json.dump(out, open("/data/mal/exp012-band/band_screen.json", "w"), indent=1, sort_keys=True, default=str)
print(json.dumps(out, indent=1, sort_keys=True, default=str)[:4000])
