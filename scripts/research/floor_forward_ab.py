#!/usr/bin/env python3
"""floor_forward_ab helpers (used by floor_forward_ab.sh). PAPER, in-sample/forward research; not a gate read.
  link  --end-hour 2026-11-12T20 --out DIR [--copy-dir D] [--live-dir D]   symlink every closed tape hour 2026-11-02T01..end-hour into DIR; prints to-ms
  table --base PNL.json --variant PNL.json --out-json F --out-md F --end-hour H [--forward-from 2026-11-12]
Days >= --forward-from are FORWARD (counted only when COMPLETE: see floor_nov_pnl.complete_bound_ms) (after the vault1.0 variant was chosen on 11-03..11-11); earlier days are IN-SAMPLE.
"""
import argparse, collections, json, os
from datetime import datetime, timedelta, timezone

from floor_tape_paths import require_ok, refusal

START = datetime(2026, 11, 2, 1, tzinfo=timezone.utc)
COPY_DIR = "/data2/mal/scratch/floor-nov-funnel/tapecopy"
LIVE_DIR = "/data2/mal/arena-tip-feed/hv7"


def hours(end):
    h = START
    while h <= end:
        yield h
        h += timedelta(hours=1)


def parse_hour(s):
    return datetime.strptime(s, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)


def link(a):
    end = parse_hour(a.end_hour)
    if end >= datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0):
        raise SystemExit("refusing: end hour %s is the current or a future UTC hour (not closed)" % a.end_hour)
    for d in (a.copy_dir, a.live_dir):
        require_ok(d)
    os.makedirs(a.out, exist_ok=True)
    missing = []
    for h in hours(end):
        name = "tape-%s.jsonl.zst" % h.strftime("%Y-%m-%dT%H")
        src = next((os.path.join(d, name) for d in (a.copy_dir, a.live_dir) if os.path.exists(os.path.join(d, name))), None)
        if src is None:
            missing.append(name); continue
        why = refusal(src)
        if why:
            raise SystemExit("%s (file %s)" % (why, name))
        dst = os.path.join(a.out, name)
        if os.path.lexists(dst):
            os.remove(dst)
        os.symlink(src, dst)
    if missing:
        raise SystemExit("missing closed hours: %s" % missing[:5])
    print(int((end + timedelta(hours=1)).timestamp() * 1000))


def header(forward_from="2026-11-12"):
    return ("paper; days < %s are IN-SAMPLE for the variant choice; FORWARD days count only when COMPLETE; nothing here is a gate read; "
            "the delta is not paired unless the paired table says so" % forward_from)


HEADER = header()


def day_set(forward_from, d):
    return "FORWARD" if d >= forward_from else "IN-SAMPLE"


def _nets(pos, key, days=None):
    return [p["net"][key] for p in pos if p["status"][key.split(":")[0]] == "closed" and (days is None or p["day"] in days)]


def arm_stats(pos, stake, model, days):
    """pooled figures of one arm over the given (complete) days; ex-top-3 is per arm (None when n <= 3)"""
    key = f"{stake}:{model}"
    ps = [p for p in pos if p["day"] in days]
    nets = sorted(_nets(ps, key), reverse=True)
    st = [p["status"][stake] for p in ps]
    return {"n_entries": len(ps), "n_closed": len(nets), "n_engine_excluded": st.count("engine_excluded"), "exclusion_reasons": dict(collections.Counter(p["reason"][stake] for p in ps if stake in p.get("reason", {}))), "n_open_at_tape_end": st.count("open_at_tape_end"),
            "total_sol": sum(nets) / 1e9, "mean_sol": (sum(nets) / len(nets) / 1e9) if nets else None, "ex_top3_sol_per_arm": (sum(nets[3:]) / 1e9) if len(nets) > 3 else None}


def pair(bpos, vpos, stake, model, days):
    """pool-paired grouping over `days` (entry days). Entries are keyed (pool, entry day); within a key, same entry instant first (group `same`), the rest matched in
    time order (group `retimed`), leftovers are `base_only` / `variant_only`. Each group: n, and per arm n_priced and total_sol (closed positions only)."""
    key = f"{stake}:{model}"
    grp = {g: {"n": 0, "base": {"n_priced": 0, "total_sol": 0.0}, "vault1.0": {"n_priced": 0, "total_sol": 0.0}} for g in ("same", "retimed", "base_only", "variant_only")}
    # like-for-like subtotal over the matched pairs (same + retimed): pairs priced in BOTH arms feed the delta; a pair priced in one arm only is its own row, never in the delta
    lfl = {g: {"n_both_priced": 0, "base_sol": 0.0, "vault_sol": 0.0, "n_base_priced_only": 0, "base_sol_one_side": 0.0, "n_vault_priced_only": 0, "vault_sol_one_side": 0.0, "n_neither_priced": 0} for g in ("same", "retimed")}

    def add(g, bp, vp):
        grp[g]["n"] += 1
        for arm, p in (("base", bp), ("vault1.0", vp)):
            if p is not None and p["status"][stake] == "closed":
                grp[g][arm]["n_priced"] += 1
                grp[g][arm]["total_sol"] += p["net"][key] / 1e9
        if g in lfl:
            bo, vo = bp["status"][stake] == "closed", vp["status"][stake] == "closed"
            bs, vs = (bp["net"][key] / 1e9 if bo else 0.0), (vp["net"][key] / 1e9 if vo else 0.0)
            L = lfl[g]
            if bo and vo:
                L["n_both_priced"] += 1; L["base_sol"] += bs; L["vault_sol"] += vs
            elif bo:
                L["n_base_priced_only"] += 1; L["base_sol_one_side"] += bs
            elif vo:
                L["n_vault_priced_only"] += 1; L["vault_sol_one_side"] += vs
            else:
                L["n_neither_priced"] += 1

    by = {}
    for arm, pos in (("b", bpos), ("v", vpos)):
        for p in pos:
            if p["day"] in days:
                by.setdefault((p["pool"], p["day"]), {"b": [], "v": []})[arm].append(p)
    for k in sorted(by):
        bl, vl = sorted(by[k]["b"], key=lambda p: p["t0"]), sorted(by[k]["v"], key=lambda p: p["t0"])
        for bp in list(bl):
            m = next((vp for vp in vl if vp["t0"] == bp["t0"]), None)
            if m is not None:
                add("same", bp, m); bl.remove(bp); vl.remove(m)
        for bp, vp in zip(bl, vl):
            add("retimed", bp, vp)
        for bp in bl[len(vl):]:
            add("base_only", bp, None)
        for vp in vl[len(bl):]:
            add("variant_only", None, vp)
    for g, L in lfl.items():
        L["delta_sol"] = L["vault_sol"] - L["base_sol"]
    tot = {k: lfl["same"][k] + lfl["retimed"][k] for k in lfl["same"] if k != "delta_sol"}
    tot["delta_sol"] = tot["vault_sol"] - tot["base_sol"]
    grp["like_for_like"] = {"same": lfl["same"], "retimed": lfl["retimed"], "same_plus_retimed": tot}
    return grp


def _f(x):
    return "n/a" if x is None else "%.6f" % x


def table(a):
    b, v = json.load(open(a.base)), json.load(open(a.variant))
    for nm, x in (("base", b), ("variant", v)):
        if not x.get("bound_applied") or x.get("to_ms") is None:
            raise SystemExit("refusing: %s pnl file was produced without --to-ms (no 24 h tape-end completeness bound); rerun floor_nov_pnl.py with --to-ms" % nm)
    hb, hv = b.get("header") or {}, v.get("header") or {}
    res = {"end_hour": a.end_hour, "forward_from": a.forward_from, "header": header(a.forward_from), "arms": {"base": hb, "vault1.0": hv},
           "cutover_hour": "2026-11-07T18Z excluded from every entry day (as in ops/reports/2026-11-12/floor-nov-funnel.md)",
           "note": "paper; send 160000 per send; open_at_tape_end = no exit decision by the end of the tape (right-censored), never a feed gap; n_engine_excluded = every engine exclusion reason (feed_gap, missing_v, guard_ref_missing, engine_restart: tools/arena_paper.py:1195,1232-1243), reasons in exclusion_reasons", "legs": {}}
    md = ["# FLOOR forward A/B: base vs vault1.0 (Q_STAR 1.0e9), paper, ending hour %s" % a.end_hour, "", "> " + header(a.forward_from), "",
          "- Days >= %s are FORWARD (after the variant was chosen on tape 11-02T01..11-11T23); earlier days IN-SAMPLE. 11-03..11-09 under-count pools (pool table starts empty at 11-02T01)." % a.forward_from,
          "- The 2026-11-07T18Z tip-follower cut-over hour is EXCLUDED from every entry day (consistent with the funnel report); the A/B still spans pre- and post-deploy hours.",
          "- A day is COMPLETE only if every position entered that day has an exit decision or an engine exclusion (no open_at_tape_end, and tape end >= last entry + 24 h time stop + 1 s tick + 1.55 s exit-leg window). "
          "FLOOR has no stop-loss, so recent entries stay open until TP 1.5x or 24 h; an INCOMPLETE day prints counts only, no total, mean, ex-top-3 or delta.",
          "- Pairs are matched by pool within one UTC entry day. A re-timed entry that crosses midnight UTC appears as one \"only in base\" row and one \"only in vault1.0\" row on adjacent days and is left out of the like-for-like delta, so the base-only and vault1.0-only counts can include re-timed entries and are not counts of new or lost pools.",
          "- base arm: variant=%s frozen_rule=%s; vault arm: variant=%s frozen_rule=%s (both ledgers are registered under the pinned FROZEN_SHA; the vault arm is NOT the frozen rule)." % (
              hb.get("variant"), hb.get("frozen_rule"), hv.get("variant"), hv.get("frozen_rule")), ""]
    for key in sorted(b["entries_by_day"]):
        stake, model = key.split(":")
        bd, vd, bpos, vpos = b["entries_by_day"][key], v["entries_by_day"][key], b.get("positions", []), v.get("positions", [])
        md += ["## stake %s, %s leg" % (stake, model), "",
               "| day | set | status | base entries/closed/engine-excl (all reasons)/open | vault entries/closed/engine-excl (all reasons)/open | base SOL | base ex-top3 (per arm) | vault SOL | vault ex-top3 (per arm) | delta entries | delta SOL (not paired) |",
               "|---|---|---|---|---|---|---|---|---|---|---|"]
        z = {"n_entries": 0, "n_closed": 0, "n_engine_excluded": 0, "exclusion_reasons": {}, "n_open_at_tape_end": 0, "total_sol": 0.0, "ex_top3_sol": None, "complete": True}
        rows, complete_days = [], []
        for d in sorted(set(bd) | set(vd)):
            x, y = bd.get(d, z), vd.get(d, z)
            ok = bool(x["complete"] and y["complete"])
            if ok:
                complete_days.append(d)
            cnt = lambda r: "%d/%d/%d/%d" % (r["n_entries"], r["n_closed"], r["n_engine_excluded"], r["n_open_at_tape_end"])
            row = {"day": d, "set": day_set(a.forward_from, d), "complete": ok, "base": {k: x[k] for k in ("n_entries", "n_closed", "n_engine_excluded", "exclusion_reasons", "n_open_at_tape_end")},
                   "vault1.0": {k: y[k] for k in ("n_entries", "n_closed", "n_engine_excluded", "exclusion_reasons", "n_open_at_tape_end")}}
            if ok:
                row["base"].update(total_sol=x["total_sol"], ex_top3_sol=x["ex_top3_sol"]); row["vault1.0"].update(total_sol=y["total_sol"], ex_top3_sol=y["ex_top3_sol"])
                row["delta"] = {"n_entries": y["n_entries"] - x["n_entries"], "total_sol": y["total_sol"] - x["total_sol"]}
                md.append("| %s | %s | complete | %s | %s | %s | %s | %s | %s | %+d | %+.6f |" % (d, row["set"], cnt(x), cnt(y), _f(x["total_sol"]), _f(x["ex_top3_sol"]), _f(y["total_sol"]), _f(y["ex_top3_sol"]),
                                                                                                    row["delta"]["n_entries"], row["delta"]["total_sol"]))
            else:
                md.append("| %s | %s | **INCOMPLETE** | %s | %s | - | - | - | - | - | - |" % (d, row["set"], cnt(x), cnt(y)))
            rows.append(row)
        md.append("")
        sets = {"IN-SAMPLE": [d for d in complete_days if d < a.forward_from], "FORWARD": [d for d in complete_days if d >= a.forward_from]}
        pooled, paired = {}, {}
        for name, days in sets.items():
            ds = set(days)
            pooled[name] = {"n_complete_days": len(days), "days": days, "base": arm_stats(bpos, stake, model, ds), "vault1.0": arm_stats(vpos, stake, model, ds)}
            paired[name] = pair(bpos, vpos, stake, model, ds)
            n_fwd = [r["day"] for r in rows if r["set"] == name]
            md += ["### pooled %s: %d complete day(s) of %d (%s)" % (name, len(days), len(n_fwd), ", ".join(days) or "none"), "",
                   "| arm | entries | closed | engine-excluded (all reasons) | open | total SOL | mean SOL/closed | ex-top3 SOL (per arm) |", "|---|---|---|---|---|---|---|---|"]
            for arm in ("base", "vault1.0"):
                r = pooled[name][arm]
                md.append("| %s | %d | %d | %d | %d | %s | %s | %s |" % (arm, r["n_entries"], r["n_closed"], r["n_engine_excluded"], r["n_open_at_tape_end"], _f(r["total_sol"]), _f(r["mean_sol"]), _f(r["ex_top3_sol_per_arm"])))
            md += ["", "engine exclusion reasons, %s: base %s; vault1.0 %s" % (name, pooled[name]["base"]["exclusion_reasons"], pooled[name]["vault1.0"]["exclusion_reasons"]), "",
                   "### pool-paired groups, %s complete days (matched by pool and entry day; group totals are per arm and NOT a delta: priced sets differ; the like-for-like delta is the second table)" % name, "",
                   "| group | n | base priced n | base SOL | vault priced n | vault SOL |", "|---|---|---|---|---|---|"]
            for g, lab in (("same", "in both arms, same entry instant"), ("retimed", "in both arms, re-timed entry"), ("base_only", "only in base"), ("variant_only", "only in vault1.0")):
                r = paired[name][g]
                md.append("| %s | %d | %d | %.6f | %d | %.6f |" % (lab, r["n"], r["base"]["n_priced"], r["base"]["total_sol"], r["vault1.0"]["n_priced"], r["vault1.0"]["total_sol"]))
            L = paired[name]["like_for_like"]
            md += ["", "like-for-like, %s: only pairs priced in BOTH arms feed the delta (vault1.0 minus base)" % name, "",
                   "| row | n | base SOL | vault SOL | vault minus base |", "|---|---|---|---|---|"]
            for lab, k in (("both priced, same entry instant", "same"), ("both priced, re-timed", "retimed"), ("both priced, same + re-timed (the like-for-like delta)", "same_plus_retimed")):
                md.append("| %s | %d | %.6f | %.6f | %+.6f |" % (lab, L[k]["n_both_priced"], L[k]["base_sol"], L[k]["vault_sol"], L[k]["delta_sol"]))
            T = L["same_plus_retimed"]
            md += ["| pairs priced in base only (vault excluded/open): NOT in the delta | %d | %.6f | - | - |" % (T["n_base_priced_only"], T["base_sol_one_side"]),
                   "| pairs priced in vault1.0 only (base excluded/open): NOT in the delta | %d | - | %.6f | - |" % (T["n_vault_priced_only"], T["vault_sol_one_side"]),
                   "| pairs priced in neither arm | %d | - | - | - |" % T["n_neither_priced"], ""]
        res["legs"][key] = {"days": rows, "pooled": pooled, "paired": paired}
    json.dump(res, open(a.out_json, "w"), indent=1)
    open(a.out_md, "w").write("\n".join(md) + "\n")


ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
l = sp.add_parser("link"); l.add_argument("--end-hour", required=True); l.add_argument("--out", required=True)
l.add_argument("--copy-dir", default=COPY_DIR); l.add_argument("--live-dir", default=LIVE_DIR)
t = sp.add_parser("table")
for n in ("--base", "--variant", "--out-json", "--out-md", "--end-hour"):
    t.add_argument(n, required=True)
t.add_argument("--forward-from", default="2026-11-12")
if __name__ == "__main__":
    a = ap.parse_args()
    link(a) if a.cmd == "link" else table(a)
