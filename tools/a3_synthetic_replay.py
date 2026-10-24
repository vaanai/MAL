"""Counts-only replay of the pooled synthetic rule over a daily.jsonl. Prints per run: run_utc, n/x, old raw flag, new z, new decision. No other record content.
usage: PYTHONPATH=<checkout with tools/> python -m tools.a3_synthetic_replay <daily.jsonl>"""
import json, sys
from tools import a3_redeploy_check as c

recs = []
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    try:
        r = json.loads(line)
    except ValueError:
        continue
    if isinstance(r, dict) and r.get("run_utc"):
        recs.append(r)
recs.sort(key=lambda r: r["run_utc"])
print(f"records={len(recs)} constants K={c.SYN_POOL_K} p0={c.SYN_P0:.5f} phi={c.SYN_PHI} z>{c.SYN_Z_HALT} minrun={c.SYN_MIN_RUN_CHECKED}")
old_h = new_h = other_diff = 0
for i, r in enumerate(recs):
    raw = (((r.get("halt") or {}).get("flags") or {}).get("synthetic_share_high") or {})
    syn = (r.get("graduations") or {}).get("synthetic") or {}
    f = c.pooled_synthetic_flag(r, recs[:i])
    try:
        raw_n = c._norm_flags(r)
        t = c.triage(r, {}, None, recs[:i])
        same_other = all(raw_n[k] == c._norm_flags(r)[k] for k in c.ALL_HALT_FLAGS if k != "synthetic_share_high")
    except c.CheckError:
        same_other = True
    other_diff += 0 if same_other else 1
    old_h += bool(raw.get("halt")); new_h += bool(f["halt"])
    print(f"{r['run_utc']} x/n={syn.get('n_synthetic')}/{syn.get('n_checked')} slots={(r.get('graduations') or {}).get('slot_min')}-{(r.get('graduations') or {}).get('slot_max')} "
          f"old_halt={raw.get('halt')} new_evaluated={f['evaluated']} z={f.get('z')} pooled={f.get('pooled_x')}/{f.get('pooled_n')} new_halt={f['halt']}")
print(f"old_halts={old_h} new_halts={new_h} other_flag_diffs={other_diff}")
