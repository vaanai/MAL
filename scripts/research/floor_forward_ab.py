#!/usr/bin/env python3
"""floor_forward_ab helpers (used by floor_forward_ab.sh). PAPER, in-sample/forward research; not a gate read.
  link  --end-hour 2026-11-12T20 --out DIR [--copy-dir D] [--live-dir D]   symlink every closed tape hour 2026-11-02T01..end-hour into DIR; prints to-ms
  table --base PNL.json --variant PNL.json --out-json F --out-md F --end-hour H [--forward-from 2026-11-12]
  board --base PNL.json --variant PNL.json --end-hour H --out-json F --out-md F [--forward-from 2026-11-12]   open sim board v0 (open_sim_board.v1), booked on the EXIT (close) UTC day
Days >= --forward-from are FORWARD (counted only when COMPLETE: see floor_nov_pnl.complete_bound_ms) (after the vault1.0 variant was chosen on 11-03..11-11); earlier days are IN-SAMPLE.
"""
import argparse, collections, json, math, os, statistics, sys
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


CAVEAT = ("MULTIPLE COMPARISONS: 8 bins x 2 stakes x 2 arms, re-read every day; by chance alone some bin will look different on some day. The 0.10 and 0.25 rows are the same entries (not independent evidence). "
          "The bins and edges were chosen after seeing the IN-SAMPLE days shown here, so IN-SAMPLE rows are the hypothesis, not a test. No bin is a finding from this table: a bin contrast counts only if pre-registered as ONE comparison "
          "(study Q6: vault <=0.5 vs >0.5, or ratio <=1.15 vs >1.15, on stake 0.25 flat) and read once at its registered instant, with >= 7 complete forward days and >= 20 forward entries in each compared bin.")
LANDING_HEADER = "paper, hypothesis tracking only; no gate claim; n is small; decide nothing before at least 7 complete forward days"
VAULT_BINS = ("<=0.5", "0.5-0.8", ">0.8")
RATIO_BINS = ("<=1.00", "1.00-1.05", "1.05-1.15", "1.15-1.5", ">1.5")
RATIO_STAKE_SOL = 0.25  # entry_over_mark0 uses the 0.25 stake for every stake row, as the 2026-11-13 landing-vault study did


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def vault_bin(x):
    """upper edges inclusive: 0.5 -> '<=0.5', 0.8 -> '0.5-0.8'; None -> 'unknown'"""
    return "unknown" if x is None else "<=0.5" if x <= 0.5 else "0.5-0.8" if x <= 0.8 else ">0.8"


def ratio_bin(x):
    """upper edges inclusive: 1.00 -> '<=1.00', 1.05 -> '1.00-1.05'; None -> 'unknown'"""
    return "unknown" if x is None else "<=1.00" if x <= 1.0 else "1.00-1.05" if x <= 1.05 else "1.05-1.15" if x <= 1.15 else "1.15-1.5" if x <= 1.5 else ">1.5"


_FEE = []


def _fee_ppm():
    """one-time lazy import (numpy only needed for this table section); repo root added to sys.path once"""
    if not _FEE:
        root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
        if root not in sys.path:
            sys.path.insert(0, root)
        from tools.arena_stack import fee_ppm
        _FEE.append(fee_ppm)
    return _FEE[0]


def landing_fields(p):
    """-> (land_vault_sol, entry_over_mark0), each None when a ledger field is missing/invalid.
    land_vault_sol = (Qe - v) / 1e9; entry_over_mark0 = ((Qe + net) / Be * 1e-3) / mark0, net = 0.25e9 * (1 - fee_ppm(Qe, Be) / 1e6)."""
    L = p.get("landing") or {}
    Qe, Be, v, m0 = L.get("Qe"), L.get("Be"), L.get("v"), L.get("mark0")
    vault = (Qe - v) / 1e9 if _num(Qe) and _num(v) else None
    ratio = None
    if _num(Qe) and _num(Be) and Be > 0 and _num(m0) and m0 > 0:
        fee_ppm = _fee_ppm()
        net = RATIO_STAKE_SOL * 1e9 * (1 - float(fee_ppm(Qe, Be)) / 1e6)
        ratio = (Qe + net) / Be * 1e-3 / m0
    return vault, ratio


def _bin_stats(rows):
    """rows: (net_lamports, stake_lamports, tp) of priced positions"""
    nets = sorted((r[0] for r in rows), reverse=True)
    pct = [r[0] / r[1] * 100.0 for r in rows]
    return {"n": len(rows), "total_sol": sum(nets) / 1e9, "ex_top3_sol": (sum(nets[3:]) / 1e9) if len(nets) > 3 else None,
            "mean_pct": (sum(pct) / len(pct)) if pct else None, "median_pct": statistics.median(pct) if pct else None, "tp": sum(1 for r in rows if r[2])}


def landing_bins(pos, stake, days):
    """flat leg of one stake over the given (complete) entry days. Guarded entries (paper guard fired: no position) are counted apart, not in a bin.
    n_unpriced = positions in the bin the pricer could not price (engine-excluded or open at tape end); counted in the bin of their landing fields when another stake's row carries them, else in `unknown`."""
    key = f"{stake}:flat"
    ps = [p for p in pos if p["day"] in days]
    out = {"n_entries": len(ps), "n_guarded": 0, "vault": {}, "ratio": {}}
    acc = {"vault": {b: [[], 0] for b in VAULT_BINS + ("unknown",)}, "ratio": {b: [[], 0] for b in RATIO_BINS + ("unknown",)}}
    for p in ps:
        vault, ratio = landing_fields(p)
        bins = {"vault": vault_bin(vault), "ratio": ratio_bin(ratio)}
        if p["status"][stake] != "closed":
            for k, b in bins.items():
                acc[k][b][1] += 1
            continue
        if (p.get("leg_state") or {}).get(stake) == "guarded":
            out["n_guarded"] += 1
            continue
        row = (p["net"][key], float(stake) * 1e9, str((p.get("exit_reason") or {}).get(stake) or "").startswith("tp"))
        for k, b in bins.items():
            acc[k][b][0].append(row)
    for k in ("vault", "ratio"):
        for b, (rows, nun) in acc[k].items():
            out[k][b] = dict(_bin_stats(rows), n_unpriced=nun)
    return out


def landing_section(res_bins, forward_from):
    md = ["", "## landing-state bins (flat leg, base and vault1.0 arms)", "", "> " + LANDING_HEADER, "",
          "- COMPLETE days only; IN-SAMPLE = days < %s, FORWARD = complete days >= %s (the split used above)." % (forward_from, forward_from),
          "- land_vault_sol = (calc.1p9.Qe - meta.v) / 1e9 SOL; bins by upper edge inclusive. entry_over_mark0 = entry price / meta.mark0 with the entry price computed at the 0.25 stake for both stake rows (as in the 2026-11-13 landing-vault study).",
          "- n = priced, not guarded; guarded = paper guard fired (no position), counted apart and in no bin; n unpriced = not priced at this stake (engine-excluded or open at tape end); counted in the bin of their landing fields when another stake's row carries them, else in `unknown`.",
          "- The registered contrast (study Q6) is on the coarse scheme (vault <=0.5 vs >0.5, or ratio <=1.15 vs >1.15); the 5 ratio bins refine it and are descriptive. calc.<LAT> is the latency leg the pnl file was built with (1p9 in the daily job).",
          "- " + CAVEAT, ""]
    for arm, stakes in res_bins.items():
        for stake, sets in stakes.items():
            for name, d in sets.items():
                md += ["### %s arm, stake %s flat, %s: %d entries, %d guarded" % (arm, stake, name, d["n_entries"], d["n_guarded"]), ""]
                for k, lab, order in (("vault", "land_vault_sol", VAULT_BINS), ("ratio", "entry_over_mark0", RATIO_BINS)):
                    md += ["| %s | n | total SOL | ex-top3 SOL | mean %% | median %% | TP | n unpriced |" % lab, "|---|---|---|---|---|---|---|---|"]
                    for b in order + ("unknown",):
                        r = d[k][b]
                        md.append("| %s | %d | %s | %s | %s | %s | %d | %d |" % (b, r["n"], _f(r["total_sol"]), _f(r["ex_top3_sol"]), _f(r["mean_pct"]), _f(r["median_pct"]), r["tp"], r["n_unpriced"]))
                    md.append("")
    return md


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
    lbins = {"base": {}, "vault1.0": {}}
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
            if model == "flat":
                for arm, ap_ in (("base", bpos), ("vault1.0", vpos)):
                    lbins[arm].setdefault(stake, {})[name] = landing_bins(ap_, stake, ds)
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
    res["landing_bins"] = {"header": LANDING_HEADER, "caveat": CAVEAT, "ratio_stake_sol": RATIO_STAKE_SOL, "bins": lbins}
    md += landing_section(lbins, a.forward_from)
    json.dump(res, open(a.out_json, "w"), indent=1)
    open(a.out_md, "w").write("\n".join(md) + "\n")


# ---- open sim board v0 (schema open_sim_board.v1). Paper, exploration, not a gate read. Reads only the two pnl json files; writes no pool id, mint, signature, price or reserve field. ----
BANNER = "open simulation, paper, not a gate read; no book has cleared the promotion gate"
BOARD_CAVEAT = "N books are watched daily; the best-looking one overstates. Days before 2026-11-12 are in-sample for the wider rule."
IMPACT_LABELS = {0.10: "model; live fills exist", 0.25: "model; live check pending", 0.5: "model only, extrapolated; not for steering", 1.0: "model only, extrapolated; not for steering"}
RECON = {"status": "NOT COMPARABLE", "note": "v0 sims have no lane gap guard; live entries were locked out until about 2026-11-14T14:48Z"}
DAY_MS = 86_400_000
BOOKS = (("D0", "FLOOR rule as live, no gap guard (paper replay)", "control", "base"), ("C0", "wider rule vault1.0, no gap guard", "variant", "variant"))


def _utc(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def _days_between(a, b):
    """inclusive list of UTC day strings a..b (empty when a > b)"""
    out, d, end = [], datetime.strptime(a, "%Y-%m-%d"), datetime.strptime(b, "%Y-%m-%d")
    while d <= end:
        out.append(d.strftime("%Y-%m-%d")); d += timedelta(days=1)
    return out


def _blob_id(path):
    """git blob id of a file, computed without git: sha1 of b'blob <n>' NUL bytes"""
    import hashlib
    try:
        b = open(path, "rb").read()
    except OSError:
        return None
    return hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest()


def sim_version():
    here = os.path.dirname(os.path.abspath(__file__))
    ids = {n: _blob_id(os.path.join(here, n)) for n in ("floor_nov_paper.py", "floor_nov_pnl.py", "floor_forward_ab.py")}
    return "v0" if not all(ids.values()) else "v0 " + " ".join("%s=%s" % kv for kv in ids.items())


def _book_trades(pnl, stake):
    """-> (trades in entry order with sequential ids, open list). Only closed positions at this stake are trades; engine-excluded ones are in neither."""
    sk, trades, opens = str(stake), [], []
    for p in sorted(pnl.get("positions", []), key=lambda q: q["t0"]):
        st = p["status"].get(sk)
        if st == "closed":
            if p.get("t1") is None:
                raise SystemExit("refusing: pnl file has no exit decision ms (t1); rerun floor_nov_pnl.py from this code version")
            trades.append({"entry_ms": p["t0"], "exit_ms": p["t1"], "stake": stake, "flat": p["net"][sk + ":flat"], "press": p["net"][sk + ":press"],
                           "reason": str((p.get("exit_reason") or {}).get(sk) or ""), "close_day": _day(p["t1"]), "entry_day": p["day"]})
        elif st == "open_at_tape_end":
            opens.append({"entry_ms": p["t0"], "stake": stake})
    for i, t in enumerate(trades, 1):
        t["tid"] = "T%04d" % i
    for i, o in enumerate(opens, 1):
        o["tid"] = "O%04d" % i
    return trades, opens


def _top3_rest(lam):
    s = sorted(lam, reverse=True)
    return sum(s[3:]) / 1e9 if len(s) > 3 else None


def _window(trades, days, n_open):
    ds = set(days)
    sel = [t for t in trades if t["close_day"] in ds]
    flat = [t["flat"] for t in sel]
    byday = collections.defaultdict(int)
    for t in sel:
        byday[t["close_day"]] += t["flat"]
    return {"net_sol_flat": sum(flat) / 1e9, "net_sol_press": sum(t["press"] for t in sel) / 1e9, "n_closed": len(sel), "n_open": n_open,
            "win_rate": (sum(1 for x in flat if x > 0) / len(flat)) if flat else None,
            "tp_count": sum(1 for t in sel if t["reason"].startswith("tp")), "time_count": sum(1 for t in sel if t["reason"].startswith("time")),
            "worst_sol": (min(flat) / 1e9) if flat else None, "ex_top3_sol": _top3_rest(flat),
            "days_pos": sum(1 for d in days if byday.get(d, 0) > 0), "days": len(days)}


def build_book(bid, title, role, pnl, stake, forward_from, to_ms, end_hour):
    trades, opens = _book_trades(pnl, stake)
    last = _day(to_ms // DAY_MS * DAY_MS - DAY_MS)  # newest UTC day wholly inside the tape
    first = min((t["close_day"] for t in trades), default=last)
    all_days = _days_between(first, last)
    n_open = len(opens)
    summary = {"today": _window(trades, all_days[-1:], n_open), "d7": _window(trades, all_days[-7:], n_open),
               "forward_all": _window(trades, [d for d in all_days if d >= forward_from], n_open), "incl_backfill": _window(trades, all_days, n_open)}
    summary["today"]["day"] = all_days[-1] if all_days else None
    per_day = []
    for d in all_days:
        w = _window(trades, [d], n_open)
        per_day.append({"day": d, "in_sample": d < forward_from, "n": w["n_closed"], "net_sol_flat": w["net_sol_flat"], "net_sol_press": w["net_sol_press"], "ex_top3_sol": w["ex_top3_sol"]})
    hdr = pnl.get("header") or {}
    return {"id": "%s-%.2f" % (bid, stake), "book": bid, "title": title, "role": role, "stake_sol": stake, "impact_label": IMPACT_LABELS[stake],
            "params": {"variant": hdr.get("variant"), "frozen_rule": hdr.get("frozen_rule"), "gap_guard": False, "send_lamports": 160000},
            "backfill_from": "2026-11-02T01:00:00Z", "in_sample_until": forward_from + "T00:00:00Z", "booking": "exit (close) UTC day",
            "status": {"state": "replay snapshot", "last_hour": end_hour}, "summary": summary, "per_day": per_day,
            "trades": [{"tid": t["tid"], "entry_utc": _utc(t["entry_ms"]), "exit_utc": _utc(t["exit_ms"]), "stake_sol": t["stake"], "net_sol_flat": t["flat"] / 1e9, "net_sol_press": t["press"] / 1e9,
                        "ret_pct": 100.0 * t["flat"] / (t["stake"] * 1e9), "exit_reason": t["reason"], "in_sample": t["entry_day"] < forward_from} for t in trades],
            "open": [{"tid": o["tid"], "entry_utc": _utc(o["entry_ms"]), "stake_sol": o["stake"], "age_h": (to_ms - o["entry_ms"]) / 3_600_000.0} for o in opens]}


def board_md(res):
    f = lambda x: "n/a" if x is None else "%+.4f" % x
    md = ["# Open sim board v0 (FLOOR, paper)", "", "> " + res["banner"], "",
          "- Generated %s from the daily replay; tape through %s; sim_version %s." % (res["generated_utc"], res["tape_through_utc"], res["sim_version"]),
          "- SOL is booked on the EXIT (close) UTC day (the `table` step books on the entry day). Net SOL is shown for the flat 15%% fail leg / the pressure leg. Complete UTC days only: today = newest complete day, 7-day = last 7 complete days, forward = complete days >= %s, all = complete days since the backfill start 2026-11-02 (in-sample before %s)." % (res["forward_from"], res["forward_from"]),
          "- Impact labels: 0.10 = \"%s\"; 0.25 = \"%s\"; 0.5 and 1.0 = \"%s\"." % (IMPACT_LABELS[0.10], IMPACT_LABELS[0.25], IMPACT_LABELS[0.5]),
          "- Live: null (%s). Reconciliation: %s (%s)." % (res["live_note"], res["recon"]["status"], res["recon"]["note"]),
          "- " + res["caveat"], "",
          "| book | stake | label | today flat / press (n) | 7-day flat / press (n) | forward flat / press (n) | all incl. backfill flat / press (n) | open |", "|---|---|---|---|---|---|---|---|"]
    cell = lambda w: "%s / %s (%d)" % (f(w["net_sol_flat"]), f(w["net_sol_press"]), w["n_closed"])
    for b in res["books"]:
        s = b["summary"]
        md.append("| %s %s | %.2f | %s | %s | %s | %s | %s | %d |" % (b["book"], b["title"], b["stake_sol"], b["impact_label"], cell(s["today"]), cell(s["d7"]), cell(s["forward_all"]), cell(s["incl_backfill"]), s["incl_backfill"]["n_open"]))
    for b in res["books"]:
        if b["stake_sol"] != 0.25:
            continue
        last = b["trades"][-30:]
        md += ["", "## %s, stake 0.25: last %d of %d closed trades (flat / pressure net SOL; IS = in-sample)" % (b["book"], len(last), len(b["trades"])), "",
               "| id | entry UTC | exit UTC | flat | press | ret % | exit | IS |", "|---|---|---|---|---|---|---|---|"]
        for t in last:
            md.append("| %s | %s | %s | %s | %s | %.2f | %s | %s |" % (t["tid"], t["entry_utc"], t["exit_utc"], f(t["net_sol_flat"]), f(t["net_sol_press"]), t["ret_pct"], t["exit_reason"], "y" if t["in_sample"] else "n"))
    return md


def board(a, now=None):
    pn = {}
    for nm, path in (("base", a.base), ("variant", a.variant)):
        x = json.load(open(path))
        if not x.get("bound_applied") or x.get("to_ms") is None:
            raise SystemExit("refusing: %s pnl file was produced without --to-ms" % nm)
        pn[nm] = x
    to_ms = min(pn["base"]["to_ms"], pn["variant"]["to_ms"])
    books = []
    for bid, title, role, src in BOOKS:
        for stake in sorted({float(k.split(":")[0]) for k in pn[src]["entries_by_day"]}):
            if stake in IMPACT_LABELS:
                books.append(build_book(bid, title, role, pn[src], stake, a.forward_from, to_ms, a.end_hour))
    res = {"schema": "open_sim_board.v1", "banner": BANNER, "n_books_watched": len(books), "generated_utc": _utc((now or datetime.now(timezone.utc)).timestamp() * 1000),
           "tape_through_utc": _utc(to_ms), "sim_version": sim_version(), "forward_from": a.forward_from, "end_hour": a.end_hour,
           "booking": "net SOL is booked on the exit (close) UTC day; only complete UTC days enter the today, d7, forward_all and incl_backfill blocks",
           "caveat": BOARD_CAVEAT, "books": books, "live": None, "live_note": "live line added by the manager's job", "recon": dict(RECON)}
    json.dump(res, open(a.out_json, "w"), indent=1)
    open(a.out_md, "w").write("\n".join(board_md(res)) + "\n")
    return res


ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
l = sp.add_parser("link"); l.add_argument("--end-hour", required=True); l.add_argument("--out", required=True)
l.add_argument("--copy-dir", default=COPY_DIR); l.add_argument("--live-dir", default=LIVE_DIR)
t = sp.add_parser("table")
for n in ("--base", "--variant", "--out-json", "--out-md", "--end-hour"):
    t.add_argument(n, required=True)
t.add_argument("--forward-from", default="2026-11-12")
bd = sp.add_parser("board")
for n in ("--base", "--variant", "--out-json", "--out-md", "--end-hour"):
    bd.add_argument(n, required=True)
bd.add_argument("--forward-from", default="2026-11-12")
if __name__ == "__main__":
    a = ap.parse_args()
    {"link": link, "table": table, "board": board}[a.cmd](a)
