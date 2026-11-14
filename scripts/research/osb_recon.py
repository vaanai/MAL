#!/usr/bin/env python3
"""osb_recon: step R of the open-sim accuracy check (design section 3): open simulation book A against the live FLOOR bot. Runs on research-0; the ONLY reader of the live extract
written by scripts/ops/osb_live_extract.py. PAPER ONLY; an open simulation, not a gate read (DEC-035 Amendment 9). Nothing here says any book makes money.

Usage: osb_recon.py --extract-dir DIR --manifest PATH --out-dir DIR [--now-ms N]
Join: a live entry (a host `entry` row that was sent) to A's entry decisions on (pool, trigger slot, trigger ms). Classes: matched; live_only (a superset violation, band H1); sim_only with a reason code
(restart_lockout, host_down_no_row, executor:<reason>, buy_not_landed, timing_edge within +-10 s, feed_gap_mismatch, unexplained). A live feed_gap refusal on a span that is not host_down while A entered
is a mismatch: gap rows are in the tape and must agree. Matched = both entered and the live buy filled. Live sends whose buy did not fill are `buy_not_landed` against an A entry (sim_only), or live_only
when A has no such entry; they also feed the live failure rate (Wilson 95%, shown beside the flat 15% assumption, not combined with sim figures: DEC-035 section 6).
Per matched filled trade: entry slip in pp (live spend / tokens against A's landing state at the stake rung, constant product with the pool fee tier); live decision-to-landed ms; exit reason live vs sim and
the exit time difference; net per trade live vs sim in pp of stake and SOL at the rung equal to the live stake (a stake with no rung, e.g. 0.03, is marked no_rung and left out of S1/S2); fees against 2 x 160,000
lamports. The sim leg is compared only when A's flat leg state is `filled` (a failed sim leg has no no-fail figure in the ledger positions); the discordant cases are counted, not hidden.
Bands H1, H2, H3, S1..S4 are the module constants below. Status per band PASS / FAIL / INSUFFICIENT; overall FAIL if any band FAILs, else INSUFFICIENT if any is, else PASS.
Output: recon.json and recon.md. Rows carry only order id, a sequential sim trade id (the board's tid), times, stake, SOL, pp, exit reasons, class and reason code. No pool, mint, signature, absolute price,
reserve or slot in any output or on stdout (checked before the write with the board's forbidden-word rule). Atomic writes; one run lock; a real root is refused under pytest.
Exit codes: 0 written; 2 refused.
"""
import argparse, fcntl, hashlib, json, math, os, re, statistics, sys, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import osb_follow as O  # noqa: E402
import osb_board as B  # noqa: E402

SCHEMA = "osb_recon.v1"
EXTRACT_SCHEMA = "osb-live-extract-v1"
EXTRACT_NAME, META_NAME = "osb-live-extract.jsonl", "osb-live-extract.meta.json"
OUT_ROOTS = ("/data2/mal/scratch/floor-open-sim/recon", "~/data/open-sim")
REAL_PREFIXES = ("/data2/mal", "/var/lib/mal", "/home/claude/data", "/home/claude/mal-data")
ARM = "l1-floor-v2"
LANE_BOT = "floor-lane-1"

# ---- pass bands: QP fixes these before the first run (design section 3 table) ----
H1_MAX_LIVE_ONLY = 0                  # live entries with no A entry
H2_MAX_UNEXPLAINED_PER_DAY = 0        # unexplained (and feed_gap_mismatch) sim-only entries per UTC day
H3_MAX_EXIT_MISMATCH = 0              # matched trades whose exit reason differs, uncoded
S_ROLLING_N = 20                      # S1..S4 run on the newest 20 matched filled trades
S1_MIN_MATCHED = 10                   # below this S1 is INSUFFICIENT
S1_MEDIAN_SLIP_PP = (-1.5, 3.0)       # median entry slip band
S1_BEYOND_PP = 8.0                    # a trade beyond +-8 pp ...
S1_MAX_BEYOND_N = 2                   # ... at most 2 of them
S2_MEDIAN_NET_DIFF_PP = 3.0           # median (live - sim) net within +-3 pp of stake
S2_SUM_REL = 0.02                     # |sum live - sum sim| <= 2% of sum stake
S3_FEE_EXCESS_PP = 0.5                # mean live fees <= model + 0.5 pp of stake
S3_MAX_RENT_UNRECLAIMED = 0           # unreclaimed rent, lamports
S4_P50_MS = 1900                      # live decision-to-landed p50
S4_P90_MS = 3000                      # p90
# ---- end of bands ----
MODEL_LANDING_MS = 1900
SEND_FEE_LAMPORTS, SEND_LEGS = 160_000, 2
TIMING_EDGE_MS = 10_000
FLAT_FAIL_ASSUMED = 0.15
STAKE_TOL = 1e-6
FILLED_TOKEN_SOURCES = (None, "meta", "ata_balance")
STALE_MS = B.STALE_MS


class Refusal(Exception):
    """exit 2"""


def utc(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_of(ms):
    return utc(ms)[:10]


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def wilson(k, n, z=1.96):
    if n <= 0:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def pct(xs, q):
    s = sorted(xs)
    return s[max(0, math.ceil(q * len(s)) - 1)] if s else None


def safe_reason(prefix, why):
    """a live refusal word, lower-cased and cut; a word holding a forbidden string (or an odd character) becomes `other`, so no live text can break the output rule"""
    w = re.sub(r"[^a-z0-9_:.-]", "", str(why).lower())[:40]
    if not w or any(f in w.lower() for f in B.FORBIDDEN):
        w = "other"
    return prefix + w


# ------------------------------------------------------------------------------------------------------------------------------ inputs
def read_extract(d):
    """-> (rows, meta). The meta sha256 must equal the extract's; the header schema must match."""
    ex, mt = Path(d) / EXTRACT_NAME, Path(d) / META_NAME
    try:
        data, meta = ex.read_bytes(), json.loads(mt.read_text())
    except (OSError, ValueError):
        raise Refusal("extract or meta unreadable")
    if hashlib.sha256(data).hexdigest() != meta.get("extract_sha256"):
        raise Refusal("extract sha256 does not match its meta")
    try:
        rows = [json.loads(x) for x in data.decode().splitlines() if x.strip()]
    except ValueError:
        raise Refusal("extract does not parse")
    if not rows or rows[0].get("kind") != "header" or rows[0].get("schema") != EXTRACT_SCHEMA or not is_int(rows[0].get("as_of_ms")) or not is_int(rows[0].get("since_ms")):
        raise Refusal("extract header missing or of another schema")
    return rows, meta


def read_sim(manifest_path, roots=None):
    """-> dict(book_id, entries, exits, positions, tids, stakes, end_ms, start_ms) for book A: the board's own pricing (osb_board.inspect_book) and A's decision rows."""
    try:
        m = O.load_manifest(manifest_path)
        O.guard_paths(m, roots or (O.OSB_ROOT,))
    except O.Refused as e:
        raise Refusal("manifest: %s" % e)
    b = {"m": m, "role": "control", "start_ms": 0, "sha": O.manifest_sha256(m)}
    info = B.inspect_book(b, int(time.time() * 1000), roots)
    if info["pnl"] is None or info["end"] is None:
        raise Refusal("book A has no priced hour yet (state %s)" % info["state"])
    run = os.path.realpath(Path(m["out_root"]) / "book")
    entries, exits = [], {}
    d = Path(run) / "decisions" / ARM
    for f in sorted(d.glob("*.jsonl")) if d.is_dir() else []:
        for ln in f.read_bytes().split(b"\n"):
            if not ln.strip():
                continue
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if not isinstance(r, dict) or not is_int(r.get("ms")):
                continue
            if r.get("kind") == "entry" and isinstance(r.get("order_id"), str):
                meta = r.get("meta") if isinstance(r.get("meta"), dict) else {}
                entries.append({"order_id": r["order_id"], "pool": r.get("pool"), "trig_slot": meta.get("signal_slot"), "trig_ms": meta.get("signal_t_recv_ms", r["ms"]), "ms": r["ms"]})
            elif r.get("kind") == "exit" and isinstance(r.get("order_id"), str):
                exits.setdefault(r["order_id"], []).append((r.get("reason"), r["ms"]))
    positions = {p["pid"]: p for p in info["pnl"].get("positions", [])}
    order = sorted(info["pnl"].get("positions", []), key=lambda q: (q["t0"], q["pid"]))
    tids = {p["pid"]: "%s-%05d" % (m["book_id"], i) for i, p in enumerate(order, 1)}
    start_ms = B.parse_instant(m["start_hour"] + ":00:00Z") if re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d", str(m["start_hour"])) else 0
    return {"book_id": m["book_id"], "entries": entries, "exits": exits, "positions": positions, "tids": tids, "stakes": m["stakes_sol"], "end_ms": info["end"], "start_ms": start_ms, "state": info["state"]}


# ------------------------------------------------------------------------------------------------------------------------------ core
def key_of(pool, slot, ms):
    return (pool, slot, ms) if isinstance(pool, str) and slot is not None and ms is not None else None


def sim_price(pos, stake_sol):
    """A's landing price at the live stake: constant product on the landing state (Qe, Be), pool fee tier of that state (tools.arena_stack.fee_ppm). SOL per UI token. None when not derivable."""
    land = (pos or {}).get("landing") or {}
    Qe, Be = land.get("Qe"), land.get("Be")
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0 for x in (Qe, Be)):
        return None
    try:
        from tools import arena_stack
        fee = float(arena_stack.fee_ppm(Qe, Be))
    except Exception:  # noqa: BLE001
        return None
    s = stake_sol * 1e9
    net = s * (1 - fee / 1e6)
    tok = Be * net / (Qe + net)
    return s / (tok * 1000.0) if tok > 0 else None


def live_price(o):
    sp, tk = o.get("fill_spend_lamports"), o.get("fill_tokens_received")
    if o.get("fill_estimated") or o.get("fill_tokens_source") not in FILLED_TOKEN_SOURCES or not all(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0 for x in (sp, tk)):
        return None
    return sp / (tk * 1000.0)


def rung_of(stake_sol, stakes):
    return next((s for s in stakes if stake_sol is not None and abs(s - stake_sol) < STAKE_TOL), None)


def classify(ex_rows, sim, win_from, win_to):
    """-> (rows, pairs, live_sent). rows: output rows (class, reason...); pairs: matched details for the bands; live_sent: [(oid, filled)] of the live sends in the window."""
    orders = [r for r in ex_rows if r["kind"] == "order"]
    refusals = [r for r in ex_rows if r["kind"] == "gap_refusal"]
    spans = [(r["down_from_ms"], r["down_to_ms"]) for r in ex_rows if r["kind"] == "restart" and not r.get("failed") and is_int(r.get("down_from_ms")) and is_int(r.get("down_to_ms"))]

    def tms(o):
        return o["trig_ms"] if is_int(o.get("trig_ms")) else o.get("decision_ms")

    inwin = lambda t: t is not None and win_from <= t < win_to
    live_by_key = {}
    for o in orders:
        k = key_of(o.get("pool"), o.get("trig_slot"), o.get("trig_ms"))
        if k and o.get("has_host_entry"):
            live_by_key[k] = o
    ref_by_key = {}
    for r in refusals:
        k = key_of(r.get("pool"), r.get("trig_slot"), r.get("trig_ms"))
        if k:
            ref_by_key[k] = r
    sim_entries = [e for e in sim["entries"] if inwin(e["trig_ms"])]
    sim_keys = {key_of(e["pool"], e["trig_slot"], e["trig_ms"]): e for e in sim_entries}
    filled = lambda o: bool(o.get("led_ok_buy"))
    rows, pairs, live_sent = [], [], []
    used = set()
    for o in sorted(orders, key=lambda o: (tms(o) or 0, o["order_id"])):
        if not o.get("sent") or not inwin(tms(o)):
            continue
        live_sent.append((o["order_id"], filled(o)))
        k = key_of(o.get("pool"), o.get("trig_slot"), o.get("trig_ms"))
        e = sim_keys.get(k) if k else None
        base = {"order_id": o["order_id"], "tid": None, "entry_utc": utc(tms(o)), "stake_sol": (o.get("stake_lamports") or o.get("led_stake_lamports") or 0) / 1e9}
        if e is None:
            rows.append(dict(base, **{"class": "live_only", "reason": "superset_violation"}))
        elif not filled(o):
            used.add(k)
            rows.append(dict(base, tid=sim["tids"].get(e["order_id"]), **{"class": "sim_only", "reason": "buy_not_landed"}))
        else:
            used.add(k)
            pairs.append((o, e))
    for e in sorted(sim_entries, key=lambda e: (e["trig_ms"], e["order_id"])):
        k = key_of(e["pool"], e["trig_slot"], e["trig_ms"])
        if k in used or any(p[1] is e for p in pairs):
            continue
        tid = sim["tids"].get(e["order_id"])
        base = {"order_id": None, "tid": tid, "entry_utc": utc(e["trig_ms"]), "stake_sol": None, "class": "sim_only"}
        lo, rf, t = live_by_key.get(k), ref_by_key.get(k), e["trig_ms"]
        if lo is not None and not lo.get("sent"):
            reason = safe_reason("executor:", lo.get("refused_why")) if lo.get("refused_why") else "unexplained"
        elif rf is not None:
            why = str(rf.get("reason") or "")
            if why.startswith("pool_blocked_after_restart") or "restart_shadow" in why or (why == "feed_gap" and rf.get("gap_kind") == "host_down"):
                reason = "restart_lockout"
            elif why == "feed_gap":
                reason = "feed_gap_mismatch"
            else:
                reason = "unexplained"
        elif any(a <= t <= b for a, b in spans):
            reason = "host_down_no_row"
        elif any(r.get("pool") == e["pool"] and ((is_int(r.get("trig_ms")) and abs(r["trig_ms"] - t) <= TIMING_EDGE_MS) or (is_int(r.get("blocked_until_ms")) and abs(r["blocked_until_ms"] - t) <= TIMING_EDGE_MS)) for r in refusals) \
                or any(o.get("pool") == e["pool"] and not o.get("sent") and is_int(tms(o)) and abs(tms(o) - t) <= TIMING_EDGE_MS for o in orders):
            reason = "timing_edge"
        else:
            reason = "unexplained"
        rows.append(dict(base, reason=reason))
    return rows, pairs, live_sent


def pair_metrics(o, e, sim, spans):
    stake = (o.get("led_stake_lamports") or o.get("stake_lamports") or 0) / 1e9
    rung = rung_of(stake, sim["stakes"])
    sk = str(float(rung)) if rung is not None else None
    pos = sim["positions"].get(e["order_id"])
    m = {"order_id": o["order_id"], "tid": sim["tids"].get(e["order_id"]), "entry_utc": utc(o["trig_ms"]), "stake_sol": stake, "no_rung": rung is None, "class": "matched", "reason": "",
         "entry_slip_pp": None, "landing_ms": None, "exit_reason_live": None, "exit_reason_sim": None, "exit_dt_ms": None, "exit_coded": None, "net_live_sol": None, "net_sim_sol": None,
         "net_live_pp": None, "net_sim_pp": None, "fees_live_pp": None, "fees_model_pp": None, "rent_unreclaimed": o.get("led_rent_unreclaimed_lamports") or 0}
    lp, sp = live_price(o), sim_price(pos, stake)
    if lp and sp:
        m["entry_slip_pp"] = (lp / sp - 1.0) * 100.0
    if is_int(o.get("landed_ms")) and is_int(o.get("decision_ms")):
        m["landing_ms"] = o["landed_ms"] - o["decision_ms"]
    sx = sim["exits"].get(e["order_id"]) or []
    sim_reason, sim_ms = (sx[0][0], sx[0][1]) if sx else (((pos or {}).get("exit_reason") or {}).get(sk) if sk else None, (pos or {}).get("t1"))
    closed_live = o.get("led_close_ev") == "exit"
    live_reason = o.get("exit_reason") if o.get("has_host_exit") else (o.get("sell_reason_bridge") or o.get("led_close_reason") if closed_live else None)
    m["exit_reason_live"], m["exit_reason_sim"] = live_reason, sim_reason
    if is_int(o.get("exit_ms")) and is_int(sim_ms):
        m["exit_dt_ms"] = o["exit_ms"] - sim_ms
    if live_reason is not None and sim_reason is not None and live_reason != sim_reason:
        down = is_int(sim_ms) and any(a <= sim_ms <= b for a, b in spans)
        m["exit_coded"] = bool(down and not o.get("has_host_exit") and str(sim_reason).startswith("tp"))
    lam = o.get("led_stake_lamports") or o.get("stake_lamports") or 0
    if closed_live and lam > 0 and isinstance(o.get("led_net_lamports"), int):
        m["net_live_sol"], m["net_live_pp"] = o["led_net_lamports"] / 1e9, o["led_net_lamports"] / lam * 100.0
        m["fees_live_pp"] = ((o.get("led_fees_lamports") or 0) + m["rent_unreclaimed"]) / lam * 100.0
        m["fees_model_pp"] = SEND_LEGS * SEND_FEE_LAMPORTS / lam * 100.0
    sim_ok = bool(pos and sk and pos["status"].get(sk) == "closed" and (pos.get("leg_state") or {}).get(sk) == "filled" and (sk + ":flat") in pos.get("net", {}))
    if sim_ok and lam > 0:
        n = pos["net"][sk + ":flat"]
        m["net_sim_sol"], m["net_sim_pp"] = n / 1e9, n / (rung * 1e9) * 100.0
    m["_sim_leg_state"] = (pos.get("leg_state") or {}).get(sk) if pos and sk else None
    return m


def band(status, **kw):
    return dict(status=status, **kw)


def evaluate(rows, metrics, live_sent):
    """the bands H1..H3 on the whole window, S1..S4 on the newest S_ROLLING_N matched filled trades at a rung"""
    lo = [r for r in rows if r["class"] == "live_only"]
    so = [r for r in rows if r["class"] == "sim_only"]
    bad = [r for r in so if r["reason"] in ("unexplained", "feed_gap_mismatch")]
    per_day = {}
    for r in bad:
        d = r["entry_utc"][:10]
        per_day[d] = per_day.get(d, 0) + 1
    nothing = not rows and not metrics
    h1 = band("INSUFFICIENT" if nothing else ("PASS" if len(lo) <= H1_MAX_LIVE_ONLY else "FAIL"), n_live_only=len(lo), max=H1_MAX_LIVE_ONLY)
    h2 = band("INSUFFICIENT" if nothing else ("PASS" if all(v <= H2_MAX_UNEXPLAINED_PER_DAY for v in per_day.values()) else "FAIL"), unexplained_by_day=dict(sorted(per_day.items())), max_per_day=H2_MAX_UNEXPLAINED_PER_DAY)
    cmp_ = [m for m in metrics if m["exit_reason_live"] is not None and m["exit_reason_sim"] is not None]
    mism = [m for m in cmp_ if m["exit_reason_live"] != m["exit_reason_sim"] and not m["exit_coded"]]
    h3 = band("INSUFFICIENT" if not cmp_ else ("PASS" if len(mism) <= H3_MAX_EXIT_MISMATCH else "FAIL"), n_compared=len(cmp_), n_mismatch_uncoded=len(mism), n_coded=sum(1 for m in cmp_ if m["exit_coded"]))
    roll = [m for m in sorted(metrics, key=lambda m: m["entry_utc"]) if not m["no_rung"]][-S_ROLLING_N:]
    slips = [m["entry_slip_pp"] for m in roll if m["entry_slip_pp"] is not None]
    if len(slips) < S1_MIN_MATCHED:
        s1 = band("INSUFFICIENT", n=len(slips), min=S1_MIN_MATCHED)
    else:
        med, beyond = statistics.median(slips), sum(1 for x in slips if abs(x) > S1_BEYOND_PP)
        s1 = band("PASS" if S1_MEDIAN_SLIP_PP[0] <= med <= S1_MEDIAN_SLIP_PP[1] and beyond <= S1_MAX_BEYOND_N else "FAIL", n=len(slips), median_slip_pp=med, n_beyond=beyond, band_pp=list(S1_MEDIAN_SLIP_PP))
    both = [m for m in roll if m["net_live_pp"] is not None and m["net_sim_pp"] is not None]
    if len(slips) < S1_MIN_MATCHED or len(both) < S1_MIN_MATCHED:
        s2 = band("INSUFFICIENT", n=len(both), min=S1_MIN_MATCHED)
    else:
        diffs = [m["net_live_pp"] - m["net_sim_pp"] for m in both]
        sl, ss, st = sum(m["net_live_sol"] for m in both), sum(m["net_sim_sol"] for m in both), sum(m["stake_sol"] for m in both)
        med = statistics.median(diffs)
        s2 = band("PASS" if abs(med) <= S2_MEDIAN_NET_DIFF_PP and abs(sl - ss) <= S2_SUM_REL * st else "FAIL", n=len(both), median_diff_pp=med, sum_live_sol=sl, sum_sim_sol=ss, sum_stake_sol=st)
    fm = [m for m in roll if m["fees_live_pp"] is not None]
    if len(roll) < S1_MIN_MATCHED or not fm:
        s3 = band("INSUFFICIENT", n=len(fm), min=S1_MIN_MATCHED)
    else:
        exc = statistics.fmean(m["fees_live_pp"] - m["fees_model_pp"] for m in fm)
        rent = sum(m["rent_unreclaimed"] for m in fm)
        s3 = band("PASS" if exc <= S3_FEE_EXCESS_PP and rent <= S3_MAX_RENT_UNRECLAIMED else "FAIL", n=len(fm), mean_fee_excess_pp=exc, rent_unreclaimed_lamports=rent)
    lat = [m["landing_ms"] for m in roll if m["landing_ms"] is not None]
    if len(lat) < S1_MIN_MATCHED:
        s4 = band("INSUFFICIENT", n=len(lat), min=S1_MIN_MATCHED)
    else:
        p50, p90 = pct(lat, 0.5), pct(lat, 0.9)
        s4 = band("PASS" if p50 <= S4_P50_MS and p90 <= S4_P90_MS else "FAIL", n=len(lat), p50_ms=p50, p90_ms=p90, model_ms=MODEL_LANDING_MS)
    bands = {"h1": h1, "h2": h2, "h3": h3, "s1": s1, "s2": s2, "s3": s3, "s4": s4}
    sts = [b["status"] for b in bands.values()]
    return bands, ("FAIL" if "FAIL" in sts else "INSUFFICIENT" if "INSUFFICIENT" in sts else "PASS")


def reconcile(ex_rows, ex_meta, sim, now_ms):
    head = ex_rows[0]
    win_from, win_to = max(head["since_ms"], sim["start_ms"]), min(head["as_of_ms"], sim["end_ms"])
    spans = [(r["down_from_ms"], r["down_to_ms"]) for r in ex_rows if r["kind"] == "restart" and not r.get("failed") and is_int(r.get("down_from_ms")) and is_int(r.get("down_to_ms"))]
    rows, pairs, live_sent = classify(ex_rows, sim, win_from, win_to)
    metrics = [pair_metrics(o, e, sim, spans) for o, e in pairs]
    bands, status = evaluate(rows, metrics, live_sent)
    n_fail = sum(1 for _, f in live_sent if not f)
    lo, hi = wilson(n_fail, len(live_sent))
    reasons = {}
    for r in rows:
        k = r["class"] + ":" + r["reason"]
        reasons[k] = reasons.get(k, 0) + 1
    leg = {"sim_leg_failed_live_filled": sum(1 for m in metrics if m["_sim_leg_state"] not in (None, "filled")), "no_rung": sum(1 for m in metrics if m["no_rung"])}
    out_rows = rows + [{k: v for k, v in m.items() if not k.startswith("_")} for m in metrics]
    out_rows.sort(key=lambda r: (r["entry_utc"], str(r.get("order_id") or ""), str(r.get("tid") or "")))
    return {"schema": SCHEMA, "banner": B.BANNER, "generated_utc": utc(now_ms), "generated_ms": now_ms, "status": status, "book_id": sim["book_id"],
            "window": {"from_utc": utc(win_from), "to_utc": utc(win_to)}, "extract_as_of_utc": utc(head["as_of_ms"]), "extract_sha256_12": ex_meta["extract_sha256"][:12],
            **bands, "counts": {"matched": len(metrics), "live_only": sum(1 for r in rows if r["class"] == "live_only"), "sim_only": sum(1 for r in rows if r["class"] == "sim_only")},
            "counts_by_reason": dict(sorted(reasons.items())), "discordance": leg,
            "live_failure": {"n_sent": len(live_sent), "n_not_filled": n_fail, "rate": (n_fail / len(live_sent)) if live_sent else None, "wilson95_lo": lo, "wilson95_hi": hi, "flat_fail_assumed": FLAT_FAIL_ASSUMED,
                             "note": "live only; not combined with sim figures"},
            "rows": out_rows}


# ------------------------------------------------------------------------------------------------------------------------------ output
def f2(x):
    return "n/a" if x is None else "%.2f" % x


def recon_md(res):
    md = ["# Open sim recon: book %s against the live FLOOR bot (paper)" % res["book_id"], "", "> " + res["banner"], "",
          "- Status: **%s**. Window %s to %s; extract as of %s (%s). Generated %s." % (res["status"], res["window"]["from_utc"], res["window"]["to_utc"], res["extract_as_of_utc"], res["extract_sha256_12"], res["generated_utc"]),
          "- Counts: matched %d, live-only %d, sim-only %d. By reason: %s." % (res["counts"]["matched"], res["counts"]["live_only"], res["counts"]["sim_only"], json.dumps(res["counts_by_reason"], sort_keys=True)),
          "- Bands: " + "; ".join("%s %s" % (k.upper(), res[k]["status"]) for k in ("h1", "h2", "h3", "s1", "s2", "s3", "s4")) + ". Constants are fixed by QP before the first run.",
          "- Live failure (not filled / sent): %d / %d, Wilson 95%% %s to %s, beside the flat %d%% assumption; live only, not combined with sim figures." % (
              res["live_failure"]["n_not_filled"], res["live_failure"]["n_sent"], f2(None if res["live_failure"]["wilson95_lo"] is None else 100 * res["live_failure"]["wilson95_lo"]),
              f2(None if res["live_failure"]["wilson95_hi"] is None else 100 * res["live_failure"]["wilson95_hi"]), int(100 * FLAT_FAIL_ASSUMED)),
          "", "| order | tid | class | reason | entry UTC | stake | slip pp | landing ms | exit live | exit sim | exit dt ms | net live SOL | net sim SOL |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in res["rows"][-200:]:
        md.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (r.get("order_id") or "-", r.get("tid") or "-", r["class"] + (" no_rung" if r.get("no_rung") else ""), r["reason"] or "-", r["entry_utc"],
                                                                                         "n/a" if r.get("stake_sol") is None else "%.2f" % r["stake_sol"], f2(r.get("entry_slip_pp")), r.get("landing_ms", "-"), r.get("exit_reason_live") or "-",
                                                                                         r.get("exit_reason_sim") or "-", r.get("exit_dt_ms", "-"), f2(r.get("net_live_sol")), f2(r.get("net_sim_sol"))))
    return "\n".join(md) + "\n"


def write_atomic(path, text):
    tmp = "%s.tmp.%d" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def guard(paths, out_dir, out_roots=None):
    if os.environ.get("PYTEST_CURRENT_TEST"):
        for p in paths:
            rp = os.path.realpath(os.path.expanduser(str(p)))
            if any(rp == r or rp.startswith(r + os.sep) for r in REAL_PREFIXES):
                raise Refusal("a real root is refused under pytest")
    return B.guard_out_dir(out_dir, out_roots or OUT_ROOTS)


def run(a, roots=None, out_roots=None, now_ms=None):
    out_dir = guard([a.extract_dir, a.manifest, a.out_dir], a.out_dir, out_roots)
    now = now_ms if now_ms is not None else (a.now_ms if a.now_ms is not None else int(time.time() * 1000))
    os.makedirs(out_dir, exist_ok=True)
    lock = open(os.path.join(out_dir, ".osb-recon.lock"), "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise Refusal("another osb_recon run holds the lock")
    ex_rows, ex_meta = read_extract(a.extract_dir)
    sim = read_sim(a.manifest, roots)
    res = reconcile(ex_rows, ex_meta, sim, now)
    md = recon_md(res)
    B.check_forbidden(res, md)
    write_atomic(os.path.join(out_dir, "recon.json"), json.dumps(res, indent=1, sort_keys=False) + "\n")
    write_atomic(os.path.join(out_dir, "recon.md"), md)
    print("osb_recon: status %s matched %d live_only %d sim_only %d bands %s" % (res["status"], res["counts"]["matched"], res["counts"]["live_only"], res["counts"]["sim_only"],
                                                                              " ".join("%s=%s" % (k, res[k]["status"]) for k in ("h1", "h2", "h3", "s1", "s2", "s3", "s4"))), flush=True)
    return 0


def main(argv=None, **kw):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--extract-dir", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--now-ms", type=int, default=None)
    a = ap.parse_args(argv)
    try:
        return run(a, **kw)
    except (Refusal, B.Refusal, O.Refused) as e:
        print("osb_recon: refused: %s" % e, file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
