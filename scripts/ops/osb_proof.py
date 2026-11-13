#!/usr/bin/env python3
"""osb_proof: the 48 h real-tape proof job for the open-simulation follow driver (scripts/research/osb_follow.py). PAPER only; QP rule on PR #1362 (verdict at 3b4acb54).

Per manifest (book A first, then one override manifest) three legs over the same S, hours H (48):
  L1  CLI `osb_follow.py M --once` (subprocess) over every present hour of a per-leg tape dir that holds hard links to exactly the seed hours and the H window hours;
  L2  one-shot in-process: apply_overrides + build_book(m, dir) with NO test kwargs, then ONE host.run(S, S+H h);
  L3  CLI `--max-hours H/2` into a fresh out_root, then a second CLI `--once` start that verifies the stored hours and follows on to H (`book` -> runs/0002).
PASS: L1 == L2 on the four digests (entry/exit decision md5, all-kinds digest, ordered (kind, pool, reason) sequence md5, ledger tree_md5); L3 md5-by-hour.jsonl byte-equal to L1's (H lines);
L3 final digests == L1's; every exit code 0. VACUOUS (book A, internal checks only, nothing printed beyond the flags): n_entries < 5, no time exit, no position open across an hour boundary,
no open/trade row in the ledger. Exit 0 PASS, 1 FAIL, 3 VACUOUS, 2 refused. Output: md5s, row counts by kind, hour count, exit codes, HEAD, manifest sha256s, peak RSS and wall seconds per leg.
No P&L, no exit-reason split, no pool id, mint or price is printed or written to proof.json. Usage: osb_proof.py --manifest A.json --manifest B.json --start-hour YYYY-MM-DDTHH --hours 48 --proof-root DIR --keep DIR
The tests use the `_test` argument of run_proof (roots, table_mode, engine_kw, leg runner, git check, env check, min hour); the CLI exposes none of them.
Derived manifests: a copy of the given manifest with `out_root` AND `tape_dir` changed (tape_dir -> the per-leg window dir of hard links; the QP leg text needs "all present hours" to be exactly the window).
"""
import argparse, collections, hashlib, json, math, os, resource, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "research"))
import osb_follow as O  # noqa: E402
from tools import arena_rule_l1 as L  # noqa: E402
from tools import floor_lane_host as H  # noqa: E402
from tools.arena_declog import DecisionLog  # noqa: E402
from tools.arena_paper import canon  # noqa: E402

HOUR_MS = O.HOUR_MS
MIN_HOUR = "2026-11-02T01"
KINDS_ALL = ("entry", "exit", "entry_refused_gap", "entry_gap_span_ignored")
GUARD_LINE = "guard path not exercised on real tape; the synthetic gap tests carry it"
EXIT_PASS, EXIT_FAIL, EXIT_REFUSED, EXIT_VACUOUS = 0, 1, 2, 3


class Refusal(Exception):
    """exit 2"""


def need_hours(m):
    """The driver's own seed length (osb_follow.load_manifest computes it inline; same formula)."""
    age_hi = m["rule_overrides"].get("AGE_HI", L.AGE_HI)
    return max(H.SEED_MIN_H, math.ceil(age_hi / 3600) + 1)


def sha256_file(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ------------------------------------------------------------------------------------------------------------------------------ pre-checks
def git_state():
    """-> (clean, detached, head)"""
    def g(*a):
        return subprocess.run(["git", "-C", str(REPO)] + list(a), capture_output=True, text=True)
    return (g("status", "--porcelain").stdout.strip() == "", g("symbolic-ref", "-q", "HEAD").returncode != 0, g("rev-parse", "HEAD").stdout.strip())


def prechecks(manifests, start_hour, hours, proof_root, keep, t):
    roots = t.get("roots") or (O.OSB_ROOT,)
    clean, detached, head = (t.get("git") or git_state)()
    if not clean:
        raise Refusal("git worktree is not clean (git status --porcelain is not empty)")
    if not detached:
        raise Refusal("git worktree is not detached (HEAD is a branch)")
    if not t.get("skip_env") and os.environ.get("MAL_ARENA_TEST"):
        raise Refusal("MAL_ARENA_TEST is set")
    if not isinstance(hours, int) or hours < 2 or hours % 2:
        raise Refusal("--hours must be an even integer >= 2")
    try:
        S = H.parse_hour(start_hour)
    except Exception as e:
        raise Refusal("start-hour: %s" % e)
    min_ms = H.parse_hour(t.get("min_hour", MIN_HOUR))
    if not manifests:
        raise Refusal("at least one --manifest is required")
    keep_p = Path(keep)
    if not keep_p.is_dir():
        raise Refusal("keep dir is not a directory")
    from tools import floor_tape_paths  # noqa: F401  (guard imported by the driver as TP)
    why = O.TP.refusal(str(keep_p), roots)
    if why:
        raise Refusal("keep: %s" % why)
    pr = Path(proof_root)
    if pr.exists() or pr.is_symlink():
        raise Refusal("proof-root already exists (it must be a new directory)")
    why = O.TP.refusal(str(pr), roots)
    if why:
        raise Refusal("proof-root: %s" % why)
    rel = None
    for r in roots:
        try:
            rel = os.path.relpath(os.path.realpath(str(pr.parent)) + "/" + pr.name, os.path.realpath(r))
            break
        except ValueError:
            pass
    if not rel or rel.startswith("..") or not rel.split(os.sep)[0].startswith("proof-"):
        raise Refusal("proof-root must sit under <root>/proof-<date>/")
    if O.TP._under(os.path.realpath(str(pr.parent)) + "/" + pr.name, os.path.realpath(str(keep_p))):
        raise Refusal("proof-root must not be inside keep")
    ms = []
    for p in manifests:
        m = O.load_manifest(p)  # Refused -> exit 2 via the caller
        if H.parse_hour(m["start_hour"]) != S:
            raise Refusal("manifest %s: start_hour is not --start-hour" % p)
        if os.path.realpath(m["tape_dir"]) != os.path.realpath(str(keep_p)):
            raise Refusal("manifest %s: tape_dir is not the keep dir" % p)
        need_ms = need_hours(m) * HOUR_MS
        if H.parse_hour(m["seed_from_hour"]) != S - need_ms:
            raise Refusal("manifest %s: seed_from_hour must be exactly %d h before start_hour" % (p, need_hours(m)))
        lo = H.parse_hour(m["seed_from_hour"])
        if lo < min_ms:
            raise Refusal("manifest %s: seed hours start before %s" % (p, t.get("min_hour", MIN_HOUR)))
        ms.append(m)
    lo = min(H.parse_hour(m["seed_from_hour"]) for m in ms)
    missing = [O.hour_key(x) for x in range(lo, S + hours * HOUR_MS, HOUR_MS) if O.hour_file(keep_p, x) is None]
    if missing:
        raise Refusal("%d hour file(s) missing in keep (first %s, last %s)" % (len(missing), missing[0], missing[-1]))
    return ms, S, head


# ------------------------------------------------------------------------------------------------------------------------------ legs
def window_dir(keep, dest, lo, S, hours):
    """Hard links (never copies) to exactly the seed + window hours; names only."""
    dest = Path(dest)
    dest.mkdir(parents=True)
    for x in range(lo, S + hours * HOUR_MS, HOUR_MS):
        f = O.hour_file(keep, x)
        try:
            os.link(f, dest / f.name)
        except OSError as e:
            raise Refusal("hard link failed for an hour file: %s" % type(e).__name__)
    return dest


def derive(m, tape_dir, out_root, path):
    d = dict(m)
    d["tape_dir"], d["out_root"] = str(tape_dir), str(out_root)
    Path(path).write_text(json.dumps(d, indent=1, sort_keys=True) + "\n")
    return d


def cli_subprocess(manifest_path, args, log):
    """osb_follow.py as a child; -> (exit code, peak RSS KiB of that child, wall s)"""
    t0 = time.time()
    with open(log, "ab") as fh:
        p = subprocess.Popen([sys.executable, str(REPO / "scripts" / "research" / "osb_follow.py"), str(manifest_path)] + list(args), stdout=fh, stderr=fh, cwd=str(REPO))
        _, status, ru = os.wait4(p.pid, 0)
    return os.waitstatus_to_exitcode(status), ru.ru_maxrss, round(time.time() - t0, 1)


def digests(run_dir):
    run_dir = Path(run_dir)
    rows = DecisionLog(str(run_dir)).rows(L.BOT_ID)
    seq = [(r["kind"], r.get("pool"), r.get("reason")) for r in rows]
    kinds = collections.Counter(r["kind"] for r in rows)
    return {"decision_md5": O.decision_md5(run_dir), "all_kinds_digest": H.decision_digest(run_dir, kinds=KINDS_ALL),
            "sequence_md5": hashlib.md5(canon(seq).encode()).hexdigest(), "ledger_tree_md5": O.tree_md5(run_dir / "ledger")}, dict(sorted(kinds.items()))


def book_dir(out_root):
    return (Path(out_root) / "book").resolve()


def vacuity(run_dir, min_entries=5):
    """Internal booleans for book A only; the facts behind them (reasons, pools, times) are never printed."""
    run_dir = Path(run_dir)
    rows = DecisionLog(str(run_dir)).rows(L.BOT_ID)
    ent = [r for r in rows if r["kind"] == "entry"]
    exits = [r for r in rows if r["kind"] == "exit"]
    led = [r for _, r in O.ledger_rows(run_dir / "ledger")]
    return {"n_entries": len(ent), "min_entries": min_entries, "time_exit": any(_is_time_exit(r) for r in exits), "open_across_hour": _across_hour(ent, exits),
            "ledger_open_or_trade": any(r.get("kind") in ("open", "trade") for r in led)}


def _is_time_exit(r):
    return str(r.get("reason", "")).startswith("time")


def _across_hour(ent, exits):
    """Some entry whose exit (same order_id) falls in a later UTC hour (decision-row `ms`)."""
    ex = {r.get("order_id"): r.get("ms") for r in exits}
    return any(isinstance(e.get("ms"), int) and isinstance(ex.get(e.get("order_id")), int) and ex[e["order_id"]] // HOUR_MS != e["ms"] // HOUR_MS for e in ent)


def run_manifest(tag, m, orig_path, proof_root, keep, S, hours, t):
    pr = Path(proof_root) / tag
    need_ms = need_hours(m) * HOUR_MS
    tape = window_dir(keep, pr / "tape", S - need_ms, S, hours)
    cli = t.get("cli") or cli_subprocess
    legs = {}
    base = {"book_id": m["book_id"]}

    def mpath(leg):
        return pr / ("manifest-%s.json" % leg)

    def cli_leg(leg, out_root, runs):
        d = derive(m, tape, out_root, mpath(leg))
        res = []
        for args in runs:
            res.append(cli(str(mpath(leg)), args, str(pr / ("%s.log" % leg))))
        return d, res

    # L1
    out1 = pr / "L1" / "out"
    d1, r1 = cli_leg("L1", out1, [["--once"]])
    legs["L1"] = {"exit_codes": [r[0] for r in r1], "peak_rss_kib": max(r[1] for r in r1), "wall_s": round(sum(r[2] for r in r1), 1)}
    # L2
    t0 = time.time()
    d2 = derive(m, tape, pr / "L2" / "out", mpath("L2"))
    run2 = pr / "L2" / "run"
    code2 = 0
    restore = O.apply_overrides(d2["rule_overrides"])
    try:
        host, eng, cfg = O.build_book(d2, run2, **({k: t[k] for k in ("table_mode", "engine_kw") if k in t}))
        host.run(S, S + hours * HOUR_MS)
    except Exception as e:  # a failed one-shot is a FAIL with the class name only
        code2 = 1
        base["L2_error"] = type(e).__name__
    finally:
        restore()
    legs["L2"] = {"exit_codes": [code2], "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, "wall_s": round(time.time() - t0, 1)}
    # L3
    out3 = pr / "L3" / "out"
    d3, r3 = cli_leg("L3", out3, [["--max-hours", str(hours // 2)], ["--once"]])
    legs["L3"] = {"exit_codes": [r[0] for r in r3], "peak_rss_kib": max(r[1] for r in r3), "wall_s": round(sum(r[2] for r in r3), 1)}

    res = {"tag": tag, "manifest_sha256": sha256_file(orig_path), "derived_manifest_sha256": {"L1": sha256_file(mpath("L1")), "L2": sha256_file(mpath("L2")), "L3": sha256_file(mpath("L3"))},
           "driver_manifest_sha256": O.manifest_sha256(m), "legs": legs, **base}
    fails = []
    if any(c != 0 for lg in legs.values() for c in lg["exit_codes"]):
        fails.append("exit code")
    dig1 = dig3 = dig2 = None
    try:
        dig1, kinds1 = digests(book_dir(out1))
        dig3, kinds3 = digests(book_dir(out3))
        res["row_counts_by_kind"] = kinds1
        res["L1_digests"] = dig1
        if code2 == 0:
            dig2, kinds2 = digests(run2)
            res["L2_digests"] = dig2
            if dig1 != dig2 or kinds1 != kinds2:
                fails.append("L1 vs L2 digests")
        res["L3_digests"] = dig3
        if dig3 != dig1:
            fails.append("L3 final digests vs L1")
        if book_dir(out3).name != "0002":
            fails.append("L3 book is not runs/0002")
        if book_dir(out1).name != "0001":
            fails.append("L1 book is not runs/0001")
        b1, b3 = (out1 / "md5-by-hour.jsonl").read_bytes(), (out3 / "md5-by-hour.jsonl").read_bytes()
        res["md5_by_hour_lines"] = {"L1": len(b1.splitlines()), "L3": len(b3.splitlines())}
        res["md5_by_hour_sha256"] = hashlib.sha256(b1).hexdigest()
        if b1 != b3:
            fails.append("md5-by-hour.jsonl L1 vs L3")
        if len(b1.splitlines()) != hours:
            fails.append("md5-by-hour lines != hours")
        res["hours"] = hours
        res["_vac"] = vacuity(book_dir(out1), t.get("min_entries", 5))
    except Exception as e:
        fails.append("digest step: %s" % type(e).__name__)
    res["fails"] = fails
    return res


def run_proof(manifests, start_hour, hours, proof_root, keep, _test=None):
    """-> (exit code, report dict, final line)"""
    t = _test or {}
    try:
        ms, S, head = prechecks(manifests, start_hour, hours, proof_root, keep, t)
        Path(proof_root).mkdir(parents=True)
        out = {"head": head, "start_hour": start_hour, "hours": hours, "manifests": []}
        for i, (m, p) in enumerate(zip(ms, manifests)):
            out["manifests"].append(run_manifest("M%d" % i, m, p, proof_root, keep, S, hours, t))
    except (Refusal, O.Refused) as e:
        return EXIT_REFUSED, None, "OSB-PROOF REFUSED %s" % e
    fails = [f for r in out["manifests"] for f in r["fails"]]
    vac = out["manifests"][0].pop("_vac", None)
    for r in out["manifests"][1:]:
        r.pop("_vac", None)
    vac_why = []
    if vac is not None:
        if vac["n_entries"] < vac["min_entries"]:
            vac_why.append("n_entries<5")
        if not vac["time_exit"]:
            vac_why.append("no_time_exit")
        if not vac["open_across_hour"]:
            vac_why.append("no_position_open_across_an_hour_boundary")
        if not vac["ledger_open_or_trade"]:
            vac_why.append("no_open_or_trade_row_in_ledger")
    elif not fails:
        vac_why.append("vacuity_not_computed")
    kinds_a = out["manifests"][0].get("row_counts_by_kind", {})
    out["guard_path_exercised"] = kinds_a.get("entry_refused_gap", 0) > 0
    if fails:
        verdict, code = "FAIL", EXIT_FAIL
    elif vac_why:
        verdict, code = "VACUOUS", EXIT_VACUOUS
    else:
        verdict, code = "PASS", EXIT_PASS
    out["verdict"], out["fails"], out["vacuous_because"] = verdict, fails, vac_why
    if verdict == "PASS" and not out["guard_path_exercised"]:
        out["note"] = GUARD_LINE
    Path(proof_root, "proof.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    return code, out, "OSB-PROOF %s head=%s hours=%d manifests=%d fails=%s vacuous_because=%s" % (verdict, out["head"][:12], hours, len(ms), ",".join(fails) or "-", ",".join(vac_why) or "-")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--manifest", action="append", default=[], required=True)
    ap.add_argument("--start-hour", required=True)
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("--proof-root", required=True)
    ap.add_argument("--keep", required=True)
    a = ap.parse_args(argv)
    code, rep, line = run_proof(a.manifest, a.start_hour, a.hours, a.proof_root, a.keep)
    if rep is not None:
        print(json.dumps(rep, indent=1, sort_keys=True))
        if rep.get("note"):
            print(rep["note"])
    print(line)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
