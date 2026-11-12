"""r4bt train/serve universe counts check (QP-RULING-1107-r4bt-p0-fail 4(d) item 4). COUNTS ONLY: integers and ratios, nothing per pool or per mint, nothing about returns.

  python -m tools.r4bt_trainserve_counts train --out OUT.json ROOT [ROOT ...]
  python -m tools.r4bt_trainserve_counts serve --out OUT.json --through YYYY-MM-DD [--counts FILE]

--out must not exist (no silent rerun). train needs ALL THREE allowed roots in one call.

PASS RULE (fixed in writing before any run; manager decision 2026-11-12), printed verbatim in the output header: RULE below.

train: ROOT must be exactly one of the three allowlisted exploration blocks (ALLOWED_TRAIN); no symlink anywhere below it. Assumed layout (cannot be checked without opening data; exit 2
  if it does not hold): ROOT/migrations/migrations-<YYYY-MM-DDTHH>.jsonl[.zst], or ROOT/w*/migrations/... for explore-0814; each line a JSON row with `type` in {complete, migration}
  and mint, block_time, pool, quote_mint (slot, tx_index, event_index optional). VERDICT coverage = distinct complete mints (block_time < block end - 3 h) whose complete-row quote is WSOL
  or absent (serve needs quote == WSOL, tools/r4b_live.py:1027) that have a migration whose EARLIEST rows give pool == canonical pool(mint) and quote == WSOL (the engine's rule,
  tools/r4b_live.py:735-744, key (slot, tx_index, event_index, file order)); counts for "any matching row", for all complete mints and for the known-non-WSOL mints are printed beside it.
serve: a day's complete / pool_create count is the sum over its hours of rows{}["pumpfun|complete"] / rows{}["pumpswap|pool_create"] (a missing key is 0), by UTC date of the row's `hour`
  key (the only method that reproduces the QP ruling's pre-deploy days); and DRIVES the verdict. Cross-check: adapter_cumulative.rows_complete|rows_pool_create hourly differences
  (counter drop = restart, delta is the new value; a missing key = not available for that row, never 0). If the two methods disagree on a day's PASS/DISCLOSE the day is DISCLOSE.
  The counted days are FIXED by --through: exactly 2026-11-08 .. THROUGH (must be before today's UTC date); a counted day that is absent or not 24 hours makes the verdict DISCLOSE.
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
INF = float("inf")
LIMITS = ("Serve is a row-count ratio of the arena tip writer's counts log, not per-mint coverage: it does not show PDA or WSOL equality of served migration rows, that a migration row "
          "arrived before the mark the engine decides at, the s0 window or V0 band, or day-boundary attribution. Ratios above 1 show pool_create rows are not one-to-one with "
          "graduations. The serve threshold was fixed after the manager had seen 2026-11-08..11-11 and part of 11-12, so a serve PASS on those days is a description, not a test. "
          "The train blocks are three exploration blocks, not established to be r4-b's training set; the train denominator is complete-row graduations with WSOL or absent quote, "
          "without the V band or Mayhem exclusion of the scorer's universe.")


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
        try:
            p = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE)
        except OSError:
            raise Refuse("cannot run zstd")
        try:
            yield from p.stdout
        finally:
            if p.poll() is None:
                p.kill()
            rc = p.wait()
        if rc > 0:
            raise Refuse("zstd failed on an input file")
    else:
        with open(path, "rb") as fh:
            yield from fh


def plain(path):
    """No symlink in the path below the (already realpath'd) root, and a clean name."""
    if os.path.realpath(path) != os.path.abspath(path):
        raise Refuse("symlink below an allowed root")
    check_name(path)
    return path


def hour_files(root):
    out = []
    for sub in [root] + [os.path.join(root, d) for d in sorted(os.listdir(root)) if d.startswith("w") and os.path.isdir(os.path.join(root, d))]:
        md = os.path.join(sub, "migrations")
        if os.path.isdir(md):
            plain(sub), plain(md)
            out += [plain(os.path.join(md, f)) for f in sorted(os.listdir(md)) if f.startswith("migrations-") and (f.endswith(".jsonl.zst") or f.endswith(".jsonl"))]
    return out


def file_hour(path):
    s = os.path.basename(path)[len("migrations-"):].split(".")[0]
    return dt.datetime.strptime(s, "%Y-%m-%dT%H").replace(tzinfo=dt.timezone.utc)


def day_of(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")


def ratio(a, b):
    return a / b if b else None


def okey(r, seq):
    """tools/r4b_live.py:735-744 ordering: (slot, tx_index, event_index, arrival)."""
    f = lambda k, d: d if r.get(k) is None else int(r[k])  # noqa: E731
    return (f("slot", INF), f("tx_index", -1), f("event_index", -1), seq)


def train_block(root, pda_fn):
    files = hour_files(root)
    if not files:
        raise Refuse("no migrations hour files under %s (layout assumption failed)" % os.path.basename(root))
    t0 = min(file_hour(f) for f in files)
    t1 = max(file_hour(f) for f in files) + dt.timedelta(hours=1)
    s0, s1 = int(t0.timestamp()), int(t1.timestamp())
    cst = {}  # mint -> [complete block_time (min), complete quote or None]
    mst = {}  # mint -> [pool_key, pool, quote_key, quote, n_rows, any_ok]
    rows, pda, seq = collections.Counter(), {}, 0
    perday = collections.defaultdict(collections.Counter)
    for f in files:
        for line in jsonl(f):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                typ, mint, bt = r.get("type"), r.get("mint"), r.get("block_time")
                seq += 1
                if typ not in ("complete", "migration"):
                    rows["other_types"] += 1
                    continue
                if not isinstance(mint, str):
                    rows["null_mint_rows"] += 1
                    continue
                if typ == "complete":
                    if not isinstance(bt, int):
                        rows["complete_rows_no_block_time"] += 1
                        continue
                    rows["complete_rows"] += 1
                    perday[day_of(bt)]["complete_rows"] += 1
                    v = cst.setdefault(mint, [bt, r.get("quote_mint")])
                    v[0] = min(v[0], bt)
                    if v[1] is None:
                        v[1] = r.get("quote_mint")
                    continue
                rows["migration_rows"] += 1
                if isinstance(bt, int):
                    perday[day_of(bt)]["migration_rows"] += 1
                else:
                    rows["migration_rows_no_block_time"] += 1
                k = okey(r, seq)
                m = mst.setdefault(mint, [None, None, None, None, 0, False])
                m[4] += 1
                rows["migration_rows_no_pool"] += r.get("pool") is None
                rows["migration_rows_no_quote"] += r.get("quote_mint") is None
                if r.get("pool") is not None and (m[0] is None or k < m[0]):
                    m[0], m[1] = k, str(r["pool"])
                if r.get("quote_mint") is not None and (m[2] is None or k < m[2]):
                    m[2], m[3] = k, str(r["quote_mint"])
                if r.get("quote_mint") == WSOL and r.get("pool") is not None:
                    if mint not in pda:
                        try:
                            pda[mint] = pda_fn(mint)
                        except Exception:
                            pda[mint] = None
                            rows["pda_errors"] += 1
                    if pda[mint] is not None and str(r["pool"]) == pda[mint]:
                        m[5] = True
            except (ValueError, TypeError, AttributeError):
                raise Refuse("unparseable row (layout assumption failed)")
    if not rows["complete_rows"]:
        raise Refuse("no complete rows in block %s (layout assumption failed)" % os.path.basename(root))
    if not rows["migration_rows"] or rows["migration_rows_no_pool"] == rows["migration_rows"] or rows["migration_rows_no_quote"] == rows["migration_rows"]:
        raise Refuse("no migration row in block %s carries pool or quote_mint (layout miss, not a result)" % os.path.basename(root))
    cens = s1 - CENSOR_S

    def get_pda(mint):
        if mint not in pda:
            try:
                pda[mint] = pda_fn(mint)
            except Exception:
                pda[mint] = None
                rows["pda_errors"] += 1
        return pda[mint]

    def earliest_ok(mint):
        m = mst.get(mint)
        return bool(m) and m[3] == WSOL and m[1] is not None and get_pda(mint) is not None and m[1] == get_pda(mint)

    keep = {m: v for m, v in cst.items() if v[0] < cens}
    drive = [m for m, v in keep.items() if v[1] in (None, WSOL)]
    for m, v in cst.items():
        d = perday[day_of(v[0])]
        d["censored" if v[0] >= cens else "complete_mints"] += 1
        if v[0] < cens and v[1] in (None, WSOL):
            d["drive_mints"] += 1
            d["covered_mints"] += earliest_ok(m)
    cov = sum(earliest_ok(m) for m in drive)
    with_mig = [m for m in drive if m in mst]
    days = []
    for d in sorted(perday):
        c = perday[d]
        ds = int(dt.datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())
        days.append({"date": d, "partial": not (ds >= s0 and ds + 86400 <= s1), **{k: c[k] for k in ("complete_rows", "migration_rows", "complete_mints", "drive_mints", "covered_mints", "censored")},
                     "row_ratio": ratio(c["migration_rows"], c["complete_rows"]), "coverage": ratio(c["covered_mints"], c["drive_mints"])})
    return {"hour_files": len(files), "block_start_utc": t0.isoformat(), "block_end_utc": t1.isoformat(),
            **{k: rows[k] for k in ("complete_rows", "migration_rows", "migration_rows_no_block_time", "migration_rows_no_pool", "migration_rows_no_quote", "complete_rows_no_block_time", "null_mint_rows", "other_types", "pda_errors")},
            "complete_mints_uncensored": len(keep), "complete_mints_censored": len(cst) - len(keep),
            "complete_mints_quote_wsol": sum(v[1] == WSOL for v in keep.values()), "complete_mints_quote_absent": sum(v[1] is None for v in keep.values()),
            "complete_mints_quote_other": sum(v[1] not in (None, WSOL) for v in keep.values()),
            "verdict_denominator_mints": len(drive), "covered_mints": cov, "coverage": ratio(cov, len(drive)),
            "drive_mints_no_migration_row": len(drive) - len(with_mig),
            "drive_mints_mig_pool_not_pda": sum(1 for m in with_mig if mst[m][1] is None or mst[m][1] != get_pda(m)),
            "drive_mints_mig_quote_not_wsol": sum(1 for m in with_mig if mst[m][3] != WSOL),
            "migration_mints_without_complete": sum(1 for m in mst if m not in cst),
            "covered_mints_any_matching_row": sum(1 for m in drive if mst.get(m) and mst[m][5]),
            "coverage_all_complete_mints": ratio(sum(earliest_ok(m) for m in keep), len(keep)),
            "mints_with_multiple_migration_rows": sum(1 for v in mst.values() if v[4] > 1), "migration_mints": len(mst),
            "row_ratio": ratio(rows["migration_rows"], rows["complete_rows"]), "per_date": days}


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
    if not tm and (set(roots) != set(ALLOWED_TRAIN) or len(roots) != len(ALLOWED_TRAIN)):
        raise Refuse("train needs exactly all three allowed roots in one invocation")
    blocks = {os.path.basename(r): train_block(r, pda_fn) for r in roots}
    for r in roots:
        v = os.path.join(r, "VIEW.sha256")
        blocks[os.path.basename(r)]["view_sha256_file_sha256"] = hashlib.sha256(open(plain(v), "rb").read()).hexdigest() if os.path.exists(v) else None
    ok = all(b["coverage"] is not None and b["coverage"] >= TRAIN_MIN for b in blocks.values())
    return {"schema": "r4bt_trainserve_train_v3", "rule": RULE, "limits": LIMITS, "script_git_blob": blob_id(), "inputs": [os.path.basename(r) for r in roots], "blocks": blocks,
            "verdict_coverage": "earliest migration row (engine rule) over complete mints with complete-row quote WSOL or absent", "train_verdict": "PASS" if ok else "DISCLOSE"}


def cmd_serve(args):
    tm = test_mode(args)
    path = args.counts or ALLOWED_COUNTS
    check_name(path)
    if not tm and os.path.realpath(path) != ALLOWED_COUNTS:
        raise Refuse("path not on the serve allowlist")
    prev, seen = None, set()
    hrs = collections.defaultdict(set)
    rsum = collections.defaultdict(lambda: [0, 0])
    dsum = collections.defaultdict(lambda: [0, 0, 0])  # delta complete, delta pool_create, hours with a delta
    try:
        for line in jsonl(path):
            if not line.strip():
                continue
            r = json.loads(line)
            h, rw, ad = r.get("hour"), r.get("rows"), r.get("adapter_cumulative")
            if not isinstance(h, str) or not isinstance(rw, dict):
                raise Refuse("counts row without hour / rows (layout assumption failed)")
            if h in seen:
                raise Refuse("duplicate hour in counts log")
            seen.add(h)
            d = h[:10]
            hrs[d].add(h)
            rsum[d][0] += int(rw.get("pumpfun|complete", 0))
            rsum[d][1] += int(rw.get("pumpswap|pool_create", 0))
            cur = None
            if isinstance(ad, dict) and "rows_complete" in ad and "rows_pool_create" in ad:
                cur = (int(ad["rows_complete"]), int(ad["rows_pool_create"]))
            if cur is not None and prev is not None:
                x = dsum[d]
                x[0] += cur[0] - prev[0] if cur[0] >= prev[0] else cur[0]
                x[1] += cur[1] - prev[1] if cur[1] >= prev[1] else cur[1]
                x[2] += 1
            prev = cur
    except (ValueError, TypeError, AttributeError):
        raise Refuse("unparseable counts row (layout assumption failed)")
    try:
        d0, d1 = dt.date.fromisoformat(FIRST_FULL_DAY), dt.date.fromisoformat(args.through)
    except ValueError:
        raise Refuse("--through must be YYYY-MM-DD")
    if d1 < d0 or d1 >= dt.datetime.now(dt.timezone.utc).date():
        raise Refuse("--through must be >= %s and before today's UTC date" % FIRST_FULL_DAY)
    fixed = {(d0 + dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)}
    days = []
    for d in sorted(set(hrs) | fixed):
        full, post = len(hrs[d]) == 24, d >= FIRST_FULL_DAY
        c, p = rsum[d]
        rr = ratio(p, c)
        x = dsum[d]
        xr = ratio(x[1], x[0]) if x[2] == len(hrs[d]) else None  # cross-check only if every hour of the day has a delta
        v_rows = rr is not None and rr >= SERVE_MIN
        disagree = xr is not None and (xr >= SERVE_MIN) != v_rows
        days.append({"date": d, "hours": len(hrs[d]), "full": full, "post_deploy": post, "counted": d in fixed, "partial": not full, "complete": c, "pool_create": p, "ratio": rr,
                     "day_pass": v_rows and not disagree, "cross_check_hours_with_delta": x[2], "cross_check_incomplete": xr is None, "cross_check_complete": x[0] if xr is not None else None,
                     "cross_check_pool_create": x[1] if xr is not None else None, "cross_check_ratio": xr, "methods_disagree": disagree})
    counted = [x for x in days if x["counted"]]
    ok = bool(counted) and all(x["full"] and x["day_pass"] for x in counted)
    return {"schema": "r4bt_trainserve_serve_v3", "rule": RULE, "limits": LIMITS, "script_git_blob": blob_id(), "inputs": [os.path.basename(path)], "counted_days": sorted(fixed), "days": days,
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
    sp.choices["serve"].add_argument("--through", required=True, help="last counted full UTC day; counted days are 2026-11-08 .. THROUGH")
    a = ap.parse_args(argv)
    if os.path.lexists(a.out):
        print("REFUSED: --out exists (no silent rerun)", file=sys.stderr)
        return 2
    try:
        res = a.fn(a)
    except Refuse as e:
        print("REFUSED: %s" % e, file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, KeyError) as e:
        print("REFUSED: %s while reading inputs" % type(e).__name__, file=sys.stderr)
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
