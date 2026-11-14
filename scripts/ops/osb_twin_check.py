#!/usr/bin/env python3
"""osb_twin_check: the read-only first-run identity checker for pricing-only TWIN open simulation books (the manager runs it as a MiScusi job). PAPER only; nothing here says any
book makes money, and a PASS here is not a gate read: it only says a twin may be started under the two QP rulings below.

It reads run directories (a book's out_root as scripts/research/osb_follow.py writes it: manifest.sha256, code.sha256, md5-by-hour.jsonl, status.json, decisions.md5, book/ledger/**) and two
manifests. It never writes into a run directory, never opens a tape-* file, and prints no pool, mint, signature, price, reserve or position id: only md5s, hours and counts.

Conditions implemented, exactly (texts quoted from DEC/DEC-035-live-learning-lane.md Amendment 9 Erratum 1, and from /data/mal/hunt-1008/ops/QP-RULING-1114-osb-latency-twin.md section 4):

kind gap-hold (DEC-035 Am.9 Erratum 1, first-run conditions, QP on #1380):
  C2: "on-path identity on real data. For each strict manifest that gets a twin, run an "on" copy at M (a new scratch book id and out_root, never the live book) over the 48 h proof window of
      job #2632. Every hour's ledger_md5 and decision_md5 must equal the strict book's stored md5-by-hour.jsonl values."
  C3: "run each twin at M over the same window. Its decisions md5 (osb_follow.book_decisions_md5) must equal its strict book's at the same last hour, and its per-hour decision_md5 must be equal
      for every hour."
  C4: "the md5 of the twin's trade rows without `gap_in_hold` equals the md5 of the strict book's trade rows for the same position_ids (canonical lines sorted by position_id and stake,
      `written_ms` removed)."
  C5: "per stake, strict `feed_gap` = twin `feed_gap_entry` + `feed_gap_exit` + twin trade rows flagged `gap_in_hold` (including slot-only and exit_invalid ones)."
kind latency (QP-RULING-1114-osb-latency-twin.md section 4, with -ADD1.md, which changes nothing here):
  L2: "Off-path identity: with `pricing_lat_entry_s` absent, an "on" copy of each strict manifest that gets a twin, run at ML over job #2632's 48 h window, has every hour's ledger_md5 and decision_md5
      equal to the strict book's stored values."
  L3: "Each latency twin at ML, over the same window: decisions md5 (osb_follow.book_decisions_md5) equal to its strict book's at the same last hour, and per-hour decision_md5 equal for every hour."
  L4: "Leg identity: for positions priced in both books, the md5 of (position_id, stake, legs.total.flat_1p3, legs.total.press_1p3, calc."1p3") is equal between twin and strict, because the 1.3 leg is
      untouched."
  L5: "Conservation per stake: twin priced + twin excluded (by reason, incl. `exit_before_entry_landing`) = strict priced + strict excluded, over the same position_ids."
(L1, L6, L7 and C1, C6, C7 are merge, proof and record conditions; they are not checked here.)

Choices where the wording leaves a detail open (each follows the text literally and is stated in the output as `choices`):
  * Window: hours are the keys of md5-by-hour.jsonl. Over the COMMON window (from the later first hour to the earlier last hour of the two runs compared) the two hour sets must be equal one to one;
    a mismatch, a disjoint window, or an unreadable / duplicated line is a refusal (exit 2). The window compared is printed. The on-copy and the twin must cover the same hours.
  * C2/L2 run only when --on-copy-run is given. The on-copy's code.sha256 must equal the twin's (both "at M"), else refused. Strict is at its own (older) commit, so its code.sha256 is not compared.
  * C3/L3: "the same last hour" is the last hour of md5-by-hour.jsonl (it must equal status.json last_hour, else refused as an inconsistent run). If the two last hours differ the decisions-md5 part is
    PENDING (never compared across different hours) and the condition is PENDING, not PASS. The per-hour part is reported separately. PENDING counts as not met (exit 3).
  * C4 canonical line: the whole row as canonical JSON (sort_keys, tight separators, arena_paper.canon) with only `written_ms` removed (the engine_id lives on open/start rows, not trade rows);
    lines sorted by (position_id, stake_sol); md5 of the lines joined by LF. "Twin trade rows without gap_in_hold" = kind trade rows whose gap_in_hold is not true. "The strict book's trade rows for the
    same position_ids" = strict kind trade rows whose position_id is one of those rows' position_ids.
  * C5: literal, over every kind excluded / trade row of both books (a position closed in only one book is reported and shows in the counts). Stake key = stake_sol.
  * L4: rows are kind trade, keyed by (position_id, stake_sol), present in both books; the tuple is (position_id, stake_sol, legs.total.flat_1p3, legs.total.press_1p3, calc["1p3"]) in canonical JSON,
    sorted, one line per row, md5 of the lines joined by LF. Counts of rows priced in only one book are printed.
  * L5: over the position_ids closed (a trade or excluded row) in BOTH books; per stake, priced + excluded rows are counted for each book and must be equal; the per-reason counts are printed.
  * A row condition (C4, C5, L4, L5) with nothing to compare (no row in common) is a FAIL, never a vacuous PASS.
  * The manifests: --strict-manifest and --twin-manifest are required; each must hash (osb_follow.manifest_sha256) to its run's manifest.sha256. For latency the twin must be a twin of the strict by
    osb_follow.twin_manifest_diff; for gap-hold by the same key rule with gap_exclusion "hold" (gap_twin_manifest_diff). Otherwise refused.

Exit codes: 0 every requested condition PASS; 3 any condition FAIL or PENDING; 2 refused (missing or unreadable file, manifest not a twin, windows differ, output file exists). Output: JSON to stdout and
to --out (created O_EXCL). Usage: osb_twin_check.py --kind gap-hold|latency --strict-run DIR --twin-run DIR [--on-copy-run DIR] --strict-manifest F --twin-manifest F --out FILE
"""
import argparse, collections, hashlib, json, os, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "research"))
import osb_follow as O  # noqa: E402

Refused = O.Refused
EXIT_PASS, EXIT_REFUSED, EXIT_NOT_MET = 0, 2, 3
HOUR_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}")


def md5_lines(lines):
    return hashlib.md5("\n".join(lines).encode()).hexdigest()


# ------------------------------------------------------------------------------------------------------------------------------ reading a run
def read_hours(run):
    """{hour: (ledger_md5, decision_md5)} from run/md5-by-hour.jsonl. Any unreadable line, duplicate hour or malformed value is a refusal."""
    p = Path(run) / "md5-by-hour.jsonl"
    try:
        lines = [ln for ln in p.read_text().splitlines() if ln.strip()]
    except OSError as e:
        raise Refused("%s unreadable: %s" % (p.name, type(e).__name__))
    out = {}
    for i, ln in enumerate(lines):
        try:
            d = json.loads(ln)
            h, a, b = d["hour"], d["ledger_md5"], d["decision_md5"]
        except (ValueError, KeyError, TypeError):
            raise Refused("%s line %d of %s is unreadable" % (p.name, i + 1, Path(run).name))
        if not (isinstance(h, str) and HOUR_RE.fullmatch(h) and isinstance(a, str) and isinstance(b, str)) or h in out:
            raise Refused("%s line %d of %s is malformed or a duplicate hour" % (p.name, i + 1, Path(run).name))
        out[h] = (a, b)
    if not out:
        raise Refused("%s of %s has no hour" % (p.name, Path(run).name))
    return out


def read_text1(run, name):
    try:
        return (Path(run) / name).read_text().strip()
    except OSError as e:
        raise Refused("%s of %s unreadable: %s" % (name, Path(run).name, type(e).__name__))


def read_run(run, label):
    run = Path(run)
    if not run.is_dir():
        raise Refused("%s run is not a directory" % label)
    hours = read_hours(run)
    last = max(hours)
    try:
        st = json.loads(read_text1(run, "status.json"))
    except ValueError:
        raise Refused("status.json of the %s run is not JSON" % label)
    if not isinstance(st, dict) or st.get("last_hour") != last:
        raise Refused("status.json last_hour of the %s run is not the last hour of md5-by-hour.jsonl (an inconsistent or mid-hour run)" % label)
    led = run / "book" / "ledger"
    if not led.is_dir():
        raise Refused("the %s run has no book/ledger" % label)
    dmd5 = O.book_decisions_md5(run)
    if dmd5 is None:
        raise Refused("the %s run has no readable decisions md5" % label)
    return {"label": label, "dir": run, "hours": hours, "last": last, "decisions_md5": dmd5, "ledger": led,
            "manifest_sha256": read_text1(run, "manifest.sha256"), "code_sha256": read_text1(run, "code.sha256")}


def rows_of(run):
    """-> {kind: [row]} for kinds trade and excluded of the active run's ledger (complete, parseable lines only: osb_follow.decision_rows)"""
    out = {"trade": [], "excluded": []}
    for r in O.decision_rows(run["ledger"]):
        if r.get("kind") in out and isinstance(r.get("position_id"), str):
            out[r["kind"]].append(r)
    return out


# ------------------------------------------------------------------------------------------------------------------------------ manifests
def gap_twin_manifest_diff(twin, strict):
    """the gap-in-hold twin's key rule (DEC-035 Am.9 Erratum 1: "same rule, parameters, host, stakes and send; only `gap_exclusion` "hold" differs; manifest key `twin_of`"): [] = fine"""
    bad = []
    if strict.get("book_id") != twin.get("twin_of"):
        bad.append("twin_of is not the strict manifest's book_id")
    if twin.get("gap_exclusion") != "hold":
        bad.append("twin gap_exclusion is not 'hold'")
    if "pricing_lat_entry_s" in twin:
        bad.append("twin carries pricing_lat_entry_s")
    for k in ("twin_of", "pricing_lat_entry_s"):
        if k in strict:
            bad.append("strict manifest carries %s" % k)
    if strict.get("gap_exclusion", "on") != "on":
        bad.append("strict manifest gap_exclusion is not 'on'")
    for k in sorted((set(twin) | set(strict)) - O.TWIN_FREE_KEYS - {"gap_exclusion"}):
        if twin.get(k, None) != strict.get(k, None) or (k in twin) != (k in strict):
            bad.append(k)
    return bad


def check_manifests(kind, strict_run, twin_run, strict_path, twin_path):
    sm, tm = O.load_manifest(strict_path), O.load_manifest(twin_path)
    for m, run, lab in ((sm, strict_run, "strict"), (tm, twin_run, "twin")):
        if O.manifest_sha256(m) != run["manifest_sha256"]:
            raise Refused("the %s manifest file does not hash to the %s run's manifest.sha256" % (lab, lab))
    bad = O.twin_manifest_diff(tm, sm) if kind == "latency" else gap_twin_manifest_diff(tm, sm)
    if kind == "latency" and "pricing_lat_entry_s" not in tm:
        bad = bad + ["twin has no pricing_lat_entry_s"]
    if bad:
        raise Refused("the twin manifest is not a pricing-only twin of the strict manifest (%s kind): %s" % (kind, bad))
    return tm.get("pricing_lat_entry_s") if kind == "latency" else None


# ------------------------------------------------------------------------------------------------------------------------------ windows and per-hour identity
def common_window(a, b, what):
    """-> sorted common hours. The two hour sets must be equal one to one between the later first hour and the earlier last hour; anything else is a refusal."""
    ha, hb = sorted(a["hours"]), sorted(b["hours"])
    lo, hi = max(ha[0], hb[0]), min(ha[-1], hb[-1])
    if lo > hi:
        raise Refused("%s: the two runs' windows are disjoint" % what)
    sa, sb = [h for h in ha if lo <= h <= hi], [h for h in hb if lo <= h <= hi]
    if sa != sb:
        raise Refused("%s: the hours do not match one to one over the common window %s..%s" % (what, lo, hi))
    return sa


def window_info(hours):
    return {"first_hour": hours[0], "last_hour": hours[-1], "n_hours": len(hours)}


def per_hour_compare(a, b, hours, fields):
    """fields: indexes into (ledger_md5, decision_md5). -> counts and the first mismatching hour"""
    n_mis, first = 0, None
    for h in hours:
        if any(a["hours"][h][i] != b["hours"][h][i] for i in fields):
            n_mis += 1
            first = first or h
    return {"n_hours_compared": len(hours), "n_mismatching_hours": n_mis, "first_mismatching_hour": first}


def cond_identity(name, on_copy, strict):
    """C2 / L2: on-copy vs strict, every hour's ledger_md5 and decision_md5"""
    hours = common_window(on_copy, strict, name)
    d = per_hour_compare(on_copy, strict, hours, (0, 1))
    d.update(window=window_info(hours), status="PASS" if d["n_mismatching_hours"] == 0 else "FAIL")
    return d


def cond_decisions(name, twin, strict):
    """C3 / L3: decisions md5 at the same last hour, and per-hour decision_md5 for every hour"""
    hours = common_window(twin, strict, name)
    ph = per_hour_compare(twin, strict, hours, (1,))
    ph["status"] = "PASS" if ph["n_mismatching_hours"] == 0 else "FAIL"
    same_last = twin["last"] == strict["last"]
    dm = {"twin_last_hour": twin["last"], "strict_last_hour": strict["last"], "twin_decisions_md5": twin["decisions_md5"], "strict_decisions_md5": strict["decisions_md5"]}
    if not same_last:
        dm["status"] = "PENDING"
        dm["note"] = "last hours differ: the decisions md5 is not compared across different hours"
    else:
        dm["status"] = "PASS" if twin["decisions_md5"] == strict["decisions_md5"] else "FAIL"
    if ph["status"] == "FAIL" or dm["status"] == "FAIL":
        st = "FAIL"
    elif dm["status"] == "PENDING":
        st = "PENDING"
    else:
        st = "PASS"
    return {"status": st, "decisions_md5": dm, "per_hour_decision_md5": ph, "window": window_info(hours)}


# ------------------------------------------------------------------------------------------------------------------------------ row conditions
def stake_of(r):
    return r.get("stake_sol")


def line(r, drop=("written_ms",)):
    return O.canon({k: v for k, v in r.items() if k not in drop})


def cond_c4(twin_rows, strict_rows):
    unflagged = [r for r in twin_rows["trade"] if r.get("gap_in_hold") is not True]
    pids = {r["position_id"] for r in unflagged}
    srows = [r for r in strict_rows["trade"] if r["position_id"] in pids]
    key = lambda r: (r["position_id"], str(stake_of(r)))  # noqa: E731
    tl, sl = [line(r) for r in sorted(unflagged, key=key)], [line(r) for r in sorted(srows, key=key)]
    a, b = md5_lines(tl), md5_lines(sl)
    return {"status": "PASS" if a == b else "FAIL", "twin_unflagged_trade_rows_md5": a, "strict_trade_rows_md5": b, "n_twin_unflagged_rows": len(tl), "n_strict_rows_same_positions": len(sl),
            "n_twin_flagged_rows": sum(1 for r in twin_rows["trade"] if r.get("gap_in_hold") is True), "n_positions_compared": len(pids)}


def cond_c5(twin_rows, strict_rows):
    stakes = sorted({str(stake_of(r)) for rs in (twin_rows, strict_rows) for k in rs for r in rs[k]})
    per, ok = {}, True
    for s in stakes:
        sg = sum(1 for r in strict_rows["excluded"] if str(stake_of(r)) == s and r.get("exclusion_reason") == "feed_gap")
        te = sum(1 for r in twin_rows["excluded"] if str(stake_of(r)) == s and r.get("exclusion_reason") == "feed_gap_entry")
        tx = sum(1 for r in twin_rows["excluded"] if str(stake_of(r)) == s and r.get("exclusion_reason") == "feed_gap_exit")
        tf = sum(1 for r in twin_rows["trade"] if str(stake_of(r)) == s and r.get("gap_in_hold") is True)
        per[s] = {"strict_feed_gap": sg, "twin_feed_gap_entry": te, "twin_feed_gap_exit": tx, "twin_gap_in_hold": tf, "equal": sg == te + tx + tf}
        ok = ok and per[s]["equal"]
    pos = lambda rs: {r["position_id"] for k in rs for r in rs[k]}  # noqa: E731
    ps, pt = pos(strict_rows), pos(twin_rows)
    return {"status": "PASS" if ok and stakes else "FAIL", "per_stake": per, "n_positions_only_in_strict": len(ps - pt), "n_positions_only_in_twin": len(pt - ps)}


def cond_l4(twin_rows, strict_rows):
    def tup(r):
        t = (r.get("legs") or {}).get("total") or {}
        return O.canon([r["position_id"], stake_of(r), t.get("flat_1p3"), t.get("press_1p3"), (r.get("calc") or {}).get("1p3")])
    key = lambda r: (r["position_id"], str(stake_of(r)))  # noqa: E731
    td, sd = {key(r): r for r in twin_rows["trade"]}, {key(r): r for r in strict_rows["trade"]}
    both = sorted(set(td) & set(sd))
    a, b = md5_lines([tup(td[k]) for k in both]), md5_lines([tup(sd[k]) for k in both])
    return {"status": "PASS" if a == b and both else "FAIL", "twin_md5": a, "strict_md5": b, "n_rows_priced_in_both": len(both), "n_rows_priced_only_in_strict": len(set(sd) - set(td)),
            "n_rows_priced_only_in_twin": len(set(td) - set(sd))}


def cond_l5(twin_rows, strict_rows):
    closed = lambda rs: {r["position_id"] for k in rs for r in rs[k]}  # noqa: E731
    ids = closed(twin_rows) & closed(strict_rows)

    def count(rs):
        out = collections.defaultdict(lambda: {"priced": 0, "excluded": 0, "excluded_by_reason": collections.Counter()})
        for r in rs["trade"]:
            if r["position_id"] in ids:
                out[str(stake_of(r))]["priced"] += 1
        for r in rs["excluded"]:
            if r["position_id"] in ids:
                c = out[str(stake_of(r))]
                c["excluded"] += 1
                c["excluded_by_reason"][str(r.get("exclusion_reason"))] += 1
        return {s: {"priced": c["priced"], "excluded": c["excluded"], "total": c["priced"] + c["excluded"], "excluded_by_reason": dict(sorted(c["excluded_by_reason"].items()))} for s, c in sorted(out.items())}
    t, s = count(twin_rows), count(strict_rows)
    ok = bool(t) and set(t) == set(s) and all(t[k]["total"] == s[k]["total"] for k in t)
    return {"status": "PASS" if ok else "FAIL", "twin": t, "strict": s, "n_positions_closed_in_both": len(ids),
            "n_positions_closed_only_in_strict": len(closed(strict_rows) - ids), "n_positions_closed_only_in_twin": len(closed(twin_rows) - ids)}


# ------------------------------------------------------------------------------------------------------------------------------ main
def run_check(kind, strict_dir, twin_dir, on_copy_dir, strict_manifest, twin_manifest):
    strict, twin = read_run(strict_dir, "strict"), read_run(twin_dir, "twin")
    lat = check_manifests(kind, strict, twin, strict_manifest, twin_manifest)
    names = ("C2", "C3", "C4", "C5") if kind == "gap-hold" else ("L2", "L3", "L4", "L5")
    out = {"kind": kind, "pricing_lat_entry_s": lat, "conditions": {}}
    if on_copy_dir is not None:
        oc = read_run(on_copy_dir, "on-copy")
        if oc["code_sha256"] != twin["code_sha256"]:
            raise Refused("the on-copy run's code.sha256 differs from the twin's: not run at the twin's commit")
        if sorted(oc["hours"]) != sorted(twin["hours"]):
            raise Refused("the on-copy run and the twin run do not cover the same hours")
        out["conditions"][names[0]] = cond_identity(names[0], oc, strict)
    out["conditions"][names[1]] = cond_decisions(names[1], twin, strict)
    tr, sr = rows_of(twin), rows_of(strict)
    if kind == "gap-hold":
        out["conditions"]["C4"], out["conditions"]["C5"] = cond_c4(tr, sr), cond_c5(tr, sr)
    else:
        out["conditions"]["L4"], out["conditions"]["L5"] = cond_l4(tr, sr), cond_l5(tr, sr)
    st = [c["status"] for c in out["conditions"].values()]
    out["overall"] = "PASS" if all(s == "PASS" for s in st) else "NOT_MET"
    out["not_requested"] = [] if on_copy_dir is not None else [names[0]]
    out["note"] = "read-only identity checks of a pricing-only twin; not a gate read; no stake, limit or live step rests on them; nothing here says any book makes money"
    return out


def write_out(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--kind", required=True, choices=("gap-hold", "latency"))
    ap.add_argument("--strict-run", required=True)
    ap.add_argument("--twin-run", required=True)
    ap.add_argument("--on-copy-run", default=None)
    ap.add_argument("--strict-manifest", required=True)
    ap.add_argument("--twin-manifest", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        if os.path.lexists(a.out):
            raise Refused("--out exists (O_EXCL): %s" % Path(a.out).name)
        res = run_check(a.kind, a.strict_run, a.twin_run, a.on_copy_run, a.strict_manifest, a.twin_manifest)
        text = json.dumps(res, indent=1, sort_keys=True) + "\n"
        write_out(a.out, text)
    except Refused as e:
        print("osb_twin_check: refused: %s" % e, file=sys.stderr)
        return EXIT_REFUSED
    except (OSError, ValueError) as e:
        print("osb_twin_check: refused: %s" % type(e).__name__, file=sys.stderr)
        return EXIT_REFUSED
    sys.stdout.write(text)
    return EXIT_PASS if res["overall"] == "PASS" else EXIT_NOT_MET


if __name__ == "__main__":
    raise SystemExit(main())
