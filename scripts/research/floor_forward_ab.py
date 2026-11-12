#!/usr/bin/env python3
"""floor_forward_ab helpers (used by floor_forward_ab.sh). PAPER, in-sample/forward research; not a gate read.
  link  --end-hour 2026-11-12T20 --out DIR [--copy-dir D] [--live-dir D]   symlink every closed tape hour 2026-11-02T01..end-hour into DIR; prints to-ms
  table --base PNL.json --variant PNL.json --out-json F --out-md F --end-hour H [--forward-from 2026-11-12]
Days >= --forward-from are FORWARD (after the vault1.0 variant was chosen on 11-03..11-11); earlier days are IN-SAMPLE.
"""
import argparse, json, os
from datetime import datetime, timedelta, timezone

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
    os.makedirs(a.out, exist_ok=True)
    missing = []
    for h in hours(end):
        name = "tape-%s.jsonl.zst" % h.strftime("%Y-%m-%dT%H")
        src = next((os.path.join(d, name) for d in (a.copy_dir, a.live_dir) if os.path.exists(os.path.join(d, name))), None)
        if src is None:
            missing.append(name); continue
        dst = os.path.join(a.out, name)
        if os.path.lexists(dst):
            os.remove(dst)
        os.symlink(src, dst)
    if missing:
        raise SystemExit("missing closed hours: %s" % missing[:5])
    print(int((end + timedelta(hours=1)).timestamp() * 1000))


def table(a):
    b, v = json.load(open(a.base)), json.load(open(a.variant))
    res = {"end_hour": a.end_hour, "forward_from": a.forward_from, "note": "paper, in-sample/forward research, not a gate read; send 160000 per send; n_unpriced = engine exclusion (feed_gap)", "legs": {}}
    md = ["# FLOOR forward A/B: base vs vault1.0 (Q_STAR 1.0e9), paper, ending hour %s" % a.end_hour, "",
          "Days >= %s are FORWARD (out-of-sample for the variant choice); earlier days IN-SAMPLE. Paper only; not a gate read. 11-03..11-09 under-count pools (pool table starts empty at 11-02T01)." % a.forward_from, ""]
    for key in sorted(b["entries_by_day"]):
        stake, leg = key.split(":")
        bd, vd = b["entries_by_day"][key], v["entries_by_day"][key]
        rows = []
        md += ["## stake %s, %s leg" % (stake, leg), "",
               "| day | set | base n_ent | base n_priced | base n_unpr | base SOL | base ex-top3 | vault n_ent | vault n_priced | vault n_unpr | vault SOL | vault ex-top3 | delta n_ent | delta SOL | delta ex-top3 |",
               "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        z = {"n_entries": 0, "n_priced": 0, "n_unpriced": 0, "total_sol": 0.0, "ex_top3_sol": 0.0}
        for d in sorted(set(bd) | set(vd)):
            x, y = bd.get(d, z), vd.get(d, z)
            ex = lambda r: r["ex_top3_sol"] if r["ex_top3_sol"] is not None else 0.0
            row = {"day": d, "set": "FORWARD" if d >= a.forward_from else "IN-SAMPLE", "base": x, "vault1.0": y,
                   "delta": {"n_entries": y["n_entries"] - x["n_entries"], "total_sol": y["total_sol"] - x["total_sol"], "ex_top3_sol": ex(y) - ex(x)}}
            rows.append(row)
            md.append("| %s | %s | %d | %d | %d | %.6f | %.6f | %d | %d | %d | %.6f | %.6f | %+d | %+.6f | %+.6f |" % (
                d, row["set"], x["n_entries"], x["n_priced"], x["n_unpriced"], x["total_sol"], ex(x), y["n_entries"], y["n_priced"], y["n_unpriced"], y["total_sol"], ex(y),
                row["delta"]["n_entries"], row["delta"]["total_sol"], row["delta"]["ex_top3_sol"]))
        res["legs"][key] = rows
        md.append("")
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
