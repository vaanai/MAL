"""r4bt train/serve universe counts check (QP-RULING-1107-r4bt-p0-fail 4(d) item 4). COUNTS ONLY: integers and ratios, nothing per pool or per mint, nothing about returns.

  python -m tools.r4bt_trainserve_counts train --out OUT.json ROOT [ROOT ...]
  python -m tools.r4bt_trainserve_counts serve --out OUT.json [--counts FILE]

PASS RULE (fixed in writing before any run; manager decision 2026-11-12), printed verbatim in the output header: RULE below.

train: ROOT must be exactly one of the three allowlisted exploration blocks (ALLOWED_TRAIN). Assumed layout (cannot be checked without opening data; refused with exit 2 if it does
  not hold): ROOT/migrations/migrations-<YYYY-MM-DDTHH>.jsonl[.zst], or ROOT/w*/migrations/... for explore-0814; each line a JSON row with `type` in {complete, migration} and
  fields mint, block_time, pool, quote_mint. Coverage = distinct complete mints (block_time < block end - 3 h) having a migration row with pool == canonical pool(mint) and
  quote_mint == WSOL, anywhere in the block; mints are held in memory only and never printed.
serve: reads only the writer's counts log; a day's complete / pool_create count is the sum of the hourly differences of adapter_cumulative.rows_complete / rows_pool_create,
  each difference attributed to the UTC date of the `hour` key of the LATER row (the row that closes the hour); a counter that drops (writer restart) restarts from 0 so that
  row's delta is its own value; the first row of the log has no predecessor and yields no delta; a day is full only with 24 hour-deltas; full days from 2026-11-08 only.
Seals: never fresh-08*, exp011-*, forward-*, walk2, sealed, tape-, and fresh-0903 is NOT allowed. No tape, no ledger, no ssh, no network.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys

RULE = ("train side PASS if migration-row coverage of complete-row graduations >= 0.95 on every allowed block; serve side PASS if pool_create/complete >= 0.90 on every full "
        "post-deploy UTC day; any other outcome is DISCLOSE (the repaired universe differs from the development universe) and is reported in DEC-031 Am.30; there is no retune "
        "and no model change either way.")
TRAIN_MIN, SERVE_MIN = 0.95, 0.90
CV = "/data/mal/clean-view/"
ALLOWED_TRAIN = (CV + "explore-0814", CV + "oracle-insample-2026-09-22_25", CV + "fast-pool-2026-09-18T23_2026-09-22T00")
ALLOWED_COUNTS = "/data2/mal/tip-health/fast-inputs/counts.jsonl"
FIRST_FULL_DAY = "2026-11-08"  # first full UTC day after the deploy at 2026-11-07T18:03:21Z
WSOL = "So11111111111111111111111111111111111111112"
CENSOR_S = 3 * 3600
FORBIDDEN = ("fresh-08", "exp011-", "forward-", "walk2", "sealed", "tape-")


class Refuse(Exception):
    pass


def blob_id(path=None):
    with open(path or __file__, "rb") as fh:
        b = fh.read()
    return hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest()


def check_name(path):
    """Refuse by component name regardless of mode (also fresh-0903, which is not on the allowlist)."""
    for part in os.path.realpath(path).split(os.sep):
        if part.startswith("fresh-0") or any(f in part for f in FORBIDDEN):
            raise Refuse("refused path component %r" % part)


def test_mode(args):
    if not getattr(args, "allow_test_path", False):
        return False
    if os.environ.get("MISCUSI_JOB_ID"):
        raise Refuse("--allow-test-path refuses to run inside a MiScusi job")
    return True


def jsonl(path):
    if path.endswith(".zst"):
        p = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE)
        yield from p.stdout
        if p.wait() != 0:
            raise Refuse("zstd failed on an input file")
    else:
        with open(path, "rb") as fh:
            yield from fh


def hour_files(root):
    out = []
    for sub in [root] + [os.path.join(root, d) for d in sorted(os.listdir(root)) if d.startswith("w") and os.path.isdir(os.path.join(root, d))]:
        md = os.path.join(sub, "migrations")
        if os.path.isdir(md):
            out += [os.path.join(md, f) for f in sorted(os.listdir(md)) if f.startswith("migrations-") and (f.endswith(".jsonl.zst") or f.endswith(".jsonl"))]
    return out


def file_hour(path):
    s = os.path.basename(path)[len("migrations-"):].split(".")[0]
    return dt.datetime.strptime(s, "%Y-%m-%dT%H").replace(tzinfo=dt.timezone.utc)


def day_of(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")


def ratio(a, b):
    return a / b if b else None


def train_block(root, pda_fn):
    files = hour_files(root)
    if not files:
        raise Refuse("no migrations hour files under %s (layout assumption failed)" % os.path.basename(root))
    t0 = min(file_hour(f) for f in files)
    t1 = max(file_hour(f) for f in files) + dt.timedelta(hours=1)
    s0, s1 = int(t0.timestamp()), int(t1.timestamp())
    cmin, mig_ok, mig_mints, pda = {}, set(), set(), {}
    rows = collections.Counter()
    perday = collections.defaultdict(collections.Counter)
    for f in files:
        for line in jsonl(f):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                raise Refuse("unparseable row (layout assumption failed)")
            typ, mint, bt = r.get("type"), r.get("mint"), r.get("block_time")
            if typ not in ("complete", "migration"):
                rows["other_types"] += 1
                continue
            if not isinstance(mint, str) or not isinstance(bt, int):
                rows["null_mint_or_block_time_rows"] += 1
                continue
            d = day_of(bt)
            if typ == "complete":
                rows["complete_rows"] += 1
                perday[d]["complete_rows"] += 1
                cmin[mint] = min(bt, cmin.get(mint, bt))
            else:
                rows["migration_rows"] += 1
                perday[d]["migration_rows"] += 1
                mig_mints.add(mint)
                if r.get("quote_mint") == WSOL and r.get("pool") is not None:
                    if mint not in pda:
                        try:
                            pda[mint] = pda_fn(mint)
                        except Exception:
                            pda[mint] = None
                            rows["pda_errors"] += 1
                    if pda[mint] is not None and str(r["pool"]) == pda[mint]:
                        mig_ok.add(mint)
    if not rows["complete_rows"]:
        raise Refuse("no complete rows in block %s (layout assumption failed)" % os.path.basename(root))
    keep = [m for m, bt in cmin.items() if bt < s1 - CENSOR_S]
    covered = sum(1 for m in keep if m in mig_ok)
    for m, bt in cmin.items():
        d = day_of(bt)
        perday[d]["censored" if bt >= s1 - CENSOR_S else "complete_mints"] += 1
        if bt < s1 - CENSOR_S and m in mig_ok:
            perday[d]["covered_mints"] += 1
    days = []
    for d in sorted(perday):
        c = perday[d]
        ds = int(dt.datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())
        days.append({"date": d, "partial": not (ds >= s0 and ds + 86400 <= s1), **{k: c[k] for k in ("complete_rows", "migration_rows", "complete_mints", "covered_mints", "censored")},
                     "row_ratio": ratio(c["migration_rows"], c["complete_rows"]), "coverage": ratio(c["covered_mints"], c["complete_mints"])})
    return {"hour_files": len(files), "block_start_utc": t0.isoformat(), "block_end_utc": t1.isoformat(), **{k: rows[k] for k in ("complete_rows", "migration_rows", "other_types", "pda_errors",
            "null_mint_or_block_time_rows")}, "complete_mints_uncensored": len(keep), "complete_mints_censored": len(cmin) - len(keep), "covered_mints": covered,
            "migration_mints": len(mig_mints), "coverage": ratio(covered, len(keep)), "row_ratio": ratio(rows["migration_rows"], rows["complete_rows"]), "per_date": days}


def cmd_train(args):
    tm = test_mode(args)
    roots = []
    for r in args.roots:
        check_name(r)
        rp = os.path.realpath(r)
        if not tm and rp not in ALLOWED_TRAIN:
            raise Refuse("path not on the train allowlist: %s" % os.path.basename(rp))
        roots.append(rp)
    if args.pda_fn is None:
        from tools.exp016_screen import canonical_pool_str as pda_fn
    else:
        pda_fn = args.pda_fn
    blocks = {os.path.basename(r): train_block(r, pda_fn) for r in roots}
    ok = all(b["coverage"] is not None and b["coverage"] >= TRAIN_MIN for b in blocks.values())
    return {"schema": "r4bt_trainserve_train_v1", "rule": RULE, "script_git_blob": blob_id(), "inputs": [os.path.basename(r) for r in roots], "blocks": blocks,
            "train_verdict": "PASS" if ok else "DISCLOSE"}


def cmd_serve(args):
    tm = test_mode(args)
    path = args.counts or ALLOWED_COUNTS
    check_name(path)
    if not tm and os.path.realpath(path) != ALLOWED_COUNTS:
        raise Refuse("path not on the serve allowlist")
    prev, seen, hours = None, set(), collections.defaultdict(dict)
    rowsum = collections.defaultdict(lambda: [0, 0])
    for line in jsonl(path):
        if not line.strip():
            continue
        r = json.loads(line)
        h, ad, rw = r.get("hour"), r.get("adapter_cumulative"), r.get("rows") or {}
        if not isinstance(h, str) or not isinstance(ad, dict) or "rows_complete" not in ad or "rows_pool_create" not in ad:
            raise Refuse("counts row without hour / adapter_cumulative.rows_complete|rows_pool_create (layout assumption failed)")
        if h in seen:
            raise Refuse("duplicate hour in counts log")
        seen.add(h)
        cur = (int(ad["rows_complete"]), int(ad["rows_pool_create"]))
        d = h[:10]
        rs = rowsum[d]
        rs[0] += int(rw.get("pumpfun|complete", 0))
        rs[1] += int(rw.get("pumpswap|pool_create", 0))
        if prev is not None:
            hours[d][h] = tuple(c - p if c >= p else c for c, p in zip(cur, prev))  # a drop = counter reset: new baseline 0
        prev = cur
    days = []
    for d in sorted(hours):
        v = hours[d].values()
        c, p = sum(x[0] for x in v), sum(x[1] for x in v)
        full, post = len(hours[d]) == 24, d >= FIRST_FULL_DAY
        days.append({"date": d, "hours": len(hours[d]), "full": full, "post_deploy": post, "counted": full and post, "partial": not full, "complete": c, "pool_create": p,
                     "ratio": ratio(p, c), "rows_sum_complete": rowsum[d][0], "rows_sum_pool_create": rowsum[d][1]})
    counted = [x for x in days if x["counted"]]
    ok = bool(counted) and all(x["ratio"] is not None and x["ratio"] >= SERVE_MIN for x in counted)
    return {"schema": "r4bt_trainserve_serve_v1", "rule": RULE, "script_git_blob": blob_id(), "inputs": [os.path.basename(path)], "first_full_day": FIRST_FULL_DAY, "days": days,
            "serve_verdict": "PASS" if ok else "DISCLOSE"}


def main(argv=None, pda_fn=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    for n, fn in (("train", cmd_train), ("serve", cmd_serve)):
        p = sp.add_parser(n)
        p.add_argument("--out", required=True)
        p.add_argument("--allow-test-path", action="store_true", help="tests only; refuses inside a MiScusi job")
        p.set_defaults(fn=fn, pda_fn=pda_fn)
    sp.choices["train"].add_argument("roots", nargs="+")
    sp.choices["serve"].add_argument("--counts")
    a = ap.parse_args(argv)
    try:
        res = a.fn(a)
    except Refuse as e:
        print("REFUSED: %s" % e, file=sys.stderr)
        return 2
    tmp = a.out + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, a.out)
    print("RULE:", RULE)
    print(res.get("train_verdict") or res.get("serve_verdict"), a.cmd, "script_git_blob", res["script_git_blob"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
