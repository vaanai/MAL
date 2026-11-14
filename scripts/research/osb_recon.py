#!/usr/bin/env python3
"""osb_recon: step R of the open-sim accuracy check (design section 3): open simulation book A against the live FLOOR bot. Runs on research-0; the ONLY reader of the live extract
written by scripts/ops/osb_live_extract.py. PAPER ONLY; an open simulation, not a gate read (DEC-035 Amendment 9). Nothing here says any book makes money.

Usage: osb_recon.py --extract-dir DIR --manifest PATH --out-dir DIR [--now-ms N] [--twin-manifest PATH]
Join: every live host entry in the window (sent or not) to A's entry decisions on (pool, trigger slot, trigger ms). One A entry is matched by at most ONE live order; a second order on the same key is
live_only (duplicate_key). Classes: matched (both entered, the live buy filled); live_only (a host entry, sent or not, with no A entry: band H1); sim_only with a reason code: restart_lockout,
host_down_no_row, executor:<word> (ONLY the design's allow-list: lane_full, lane_daily_stop, wallet_floor, gap_hold, stale_decision, canary_expired, stop_file and the prefixes a3_pause:, lane_budget_,
latched:; any other word is `unexplained` and counted in executor_unlisted), buy_not_landed (positive evidence only: a bridge buy_failed row or a ledger writeoff buy_expired / not_sent, and no buy_landed
row), timing_edge (a cooldown or block row within +-10 s; a nearby feed_gap refusal that is not host_down is feed_gap_mismatch instead), feed_gap_mismatch, unexplained.
Per matched filled trade: entry slip in pp (live spend / tokens against A's landing state at the live stake, constant product with the pool fee tier); live decision-to-landed ms and in landing steps
(`landing_k`); the exit reason of the executor's sell (bridge sell_sent, else the ledger close reason) against A's, the host exit reason kept apart as exit_reason_host; net per trade live vs sim in pp
of stake and SOL at the rung equal to the live stake (no rung, e.g. 0.03: marked no_rung, out of S1/S2); fees against 2 x 160,000 lamports. A's flat leg is compared only when its state is `filled`.
Gap-in-hold twin (QP ruling 2026-11-14 Q4; optional --twin-manifest, book A's `gap_exclusion: "hold"` twin; refused unless its `twin_of` is A's book_id and its decisions md5 (decisions.md5 file, else computed from its ledger rows) equals A's):
a matched live trade whose strict sim trade is excluded for a feed gap is compared against the twin's price; its row carries gap_in_hold and blind_s (from the twin's ledger trade rows: gap_in_hold, blind_s,
gap_count, gap_holes). S1 and S2 include those trades; S2 is also printed split by flag. A live take-profit whose instant falls inside a sim-side gap of that hold is the coded H3 case tp_in_sim_gap (counted, not
a failure). A trade still excluded (strict without a twin, or in the twin) has the row field no_sim_price = gap_entry | gap_exit | missing_v | other, is counted, and stays outside S1/S2 (as before the twin:
it is a matched row with no sim price). Live failure rates (buy not filled, sell failed) with Wilson 95% intervals, beside the flat 15% assumption, never combined with sim figures (DEC-035 section 6).
Bands H1..H3 (whole window, FAIL at any n) and S1..S4 (the newest 20 matched filled trades at a rung with entry in the last 7 days of the window, INSUFFICIENT below 10) are the module constants below.
Output: recon.json and recon.md. Every reason and exit reason is mapped to a fixed word list (unknown -> other); rows carry only the allow-listed fields; at write time a forbidden word or an id-shaped value
(a run of 20+ base58 characters, 16+ hex characters other than an order id) refuses the write, and the refusal message names the field and the rule, never the value.
Exit codes: 0 written; 2 refused (incl. a malformed extract).
"""
import argparse, fcntl, hashlib, json, math, os, re, statistics, sys, tempfile, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import osb_follow as O  # noqa: E402
import osb_board as B  # noqa: E402
from tools import arena_stack as AS  # noqa: E402

SCHEMA = "osb_recon.v1"
EXTRACT_SCHEMA = "osb-live-extract-v1"
EXTRACT_NAME, META_NAME = "osb-live-extract.jsonl", "osb-live-extract.meta.json"
OUT_ROOTS = ("/data2/mal/scratch/floor-open-sim/recon", "~/data/open-sim")
REAL_PREFIXES = ("/data2/mal", "/var/lib/mal", "/home/claude/data", "/home/claude/mal-data")
ARM = "l1-floor-v2"
DAY_MS = 86_400_000

# ---- pass bands: QP fixes these before the first run (design section 3 table; QP rulings on PR #1378) ----
H1_MAX_LIVE_ONLY = 0                  # host entries (sent or not) with no A entry
H2_MAX_UNEXPLAINED_PER_DAY = 0        # unexplained + feed_gap_mismatch (+ executor_unlisted) sim-only entries per UTC day
H3_MAX_EXIT_MISMATCH = 0              # uncoded exit mismatches (missing reason, open vs closed past tolerance included)
S_ROLLING_N = 20                      # S1..S4 run on the newest 20 matched filled trades ...
S_RECENT_MS = 7 * DAY_MS              # ... with entry in the last 7 days of the window
S1_MIN_MATCHED = 10                   # below this S1..S4 are INSUFFICIENT
S1_MEDIAN_SLIP_PP = (-1.5, 3.0)       # median entry slip band (positive = live paid more)
S1_BEYOND_PP = 8.0                    # a trade beyond +-8 pp ...
S1_MAX_BEYOND_N = 2                   # ... at most 2 of them
S2_MEDIAN_NET_DIFF_PP = 3.0           # median (live - sim) net within +-3 pp of stake
S2_SUM_REL = 0.02                     # |sum live - sum sim| <= 2% of sum stake
S3_FEE_EXCESS_PP = 0.5                # MEAN (live fees + unreclaimed rent - model) <= 0.5 pp of stake at n >= 10
S3_MAX_RENT_UNRECLAIMED = 0           # unreclaimed rent, lamports: any closed matched trade above this is FAIL at any n
S4_P50_MS = 1900                      # live decision-to-landed p50
S4_P90_MS = 3000                      # p90
S4_K_P50 = AS.k_slots(1.9, 0.4)       # the same latencies in landing steps (the model counts k slots from the decision), 0.4 s per step
S4_K_P90 = AS.k_slots(3.0, 0.4)
H3_OPEN_TOLERANCE_MS = 3_600_000      # one side closed and the other still open longer than this: mismatch
# ---- end of bands ----
MODEL_LANDING_MS = 1900
SEND_FEE_LAMPORTS, SEND_LEGS = 160_000, 2
TIMING_EDGE_MS = 10_000
FLAT_FAIL_ASSUMED = 0.15
STAKE_TOL = 1e-6
FILLED_TOKEN_SOURCES = ("meta", "ata_balance")
STALE_MS = B.STALE_MS
EXEC_EXACT = ("lane_full", "lane_daily_stop", "wallet_floor", "gap_hold", "stale_decision", "canary_expired", "stop_file")
EXEC_PREFIX = ("a3_pause:", "lane_budget_", "latched:")
NO_SIM_WORDS = ("gap_entry", "gap_exit", "missing_v", "other")  # the fixed no_sim_price list
CODED_TP_GAP = "tp_in_sim_gap"
CODED_NONRULE, NONRULE_CLOSE_WORD = "live_nonrule_exit", "paper_refused"  # QP ruling 2026-11-14 point 2 (coded AFTER the first run: post hoc)
NONRULE_LANDING_SLACK_MS = 60_000
H3_NONRULE_MAX_N, H3_NONRULE_MAX_FRAC = 1, 0.10  # more than 1 such trade in the window's matched trades, or more than 10% of them: H3 FAIL
EXIT_VOCAB = ("tp_1.5x", "time_24h", "time_stop", "time_stop_backstop", "sl_backstop")
CODED_SIM, CODED_LIVE = "tp_1.5x", "time_stop_backstop"
B58_RUN = re.compile(r"[1-9A-HJ-NP-Za-km-z]{20,}")
HEX_RUN = re.compile(r"[0-9a-fA-F]{16,}")
OID_OK = re.compile(r"[0-9a-f]{16}|[A-Za-z0-9_-]{1,15}")
NUM_FIELDS = ("trig_slot", "trig_ms", "decision_ms", "stake_lamports", "led_stake_lamports", "led_net_lamports", "landed_ms", "landed_slot", "decision_slot", "exit_ms", "sell_landed_ms",
              "led_close_ms", "led_fees_lamports", "led_rent_unreclaimed_lamports", "led_intent_ms", "fill_spend_lamports", "fill_tokens_received", "sell_send_ms", "send_ms")


class Refusal(Exception):
    """exit 2; the message names a field and a rule, never a value"""


def utc(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


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


def executor_code(why):
    """the design's allow-list only; the id-carrying suffix of a prefixed word is never kept. None = not listed"""
    w = str(why or "")
    if w in EXEC_EXACT:
        return "executor:" + w
    for p in EXEC_PREFIX:
        if w.startswith(p):
            return "executor:" + p.rstrip(":_")
    return None


def no_sim_word(reason):
    """an engine exclusion reason as a word of the fixed list (never the raw text). A plain strict `feed_gap` does not say entry or exit, so it is `other`."""
    r = str(reason or "").lower()
    if r == "feed_gap_entry":
        return "gap_entry"
    if r == "feed_gap_exit":
        return "gap_exit"
    if "missing_v" in r:
        return "missing_v"
    return "other"


def is_gap_reason(reason):
    return str(reason or "").startswith("feed_gap")


def xr(raw):
    """an exit reason as it may be shown: a word of the fixed list, else `other`"""
    return None if raw is None else (raw if raw in EXIT_VOCAB else "other")


# ------------------------------------------------------------------------------------------------------------------------------ inputs
def read_extract(d):
    """-> (rows, meta). The meta sha256 must equal the extract's; the header schema must match; every row is an object and its numeric fields are numbers (a refusal names the field, never a value)."""
    ex, mt = Path(d) / EXTRACT_NAME, Path(d) / META_NAME
    try:
        data, meta = ex.read_bytes(), json.loads(mt.read_text())
    except (OSError, ValueError):
        raise Refusal("extract or meta unreadable")
    if not isinstance(meta, dict) or hashlib.sha256(data).hexdigest() != meta.get("extract_sha256"):
        raise Refusal("extract sha256 does not match its meta")
    try:
        rows = [json.loads(x) for x in data.decode().splitlines() if x.strip()]
    except ValueError:
        raise Refusal("extract does not parse")
    if not rows or any(not isinstance(r, dict) or not isinstance(r.get("kind"), str) for r in rows):
        raise Refusal("extract rows are not objects with a kind")
    h = rows[0]
    if h.get("kind") != "header" or h.get("schema") != EXTRACT_SCHEMA or not is_int(h.get("as_of_ms")) or not is_int(h.get("since_ms")):
        raise Refusal("extract header missing or of another schema")
    for r in rows[1:]:
        if r["kind"] == "order":
            if not isinstance(r.get("order_id"), str):
                raise Refusal("extract field order_id has the wrong type")
            for f in NUM_FIELDS:
                if r.get(f) is not None and not is_num(r[f]):
                    raise Refusal("extract field %s has the wrong type" % f)
        elif r["kind"] in ("gap_refusal", "restart", "start"):
            for f in ("trig_ms", "t_ms", "down_from_ms", "down_to_ms", "blocked_until_ms", "prev_last_ms"):
                if r.get(f) is not None and not is_num(r[f]):
                    raise Refusal("extract field %s has the wrong type" % f)
    return rows, meta


TWIN_MAY_DIFFER = ("book_id", "title", "out_root", "twin_of", "gap_exclusion", "start_hour", "seed_from_hour")  # start keys only while the Am.9 erratum is not in force (QP ruling on PR #1379)


def load_twin_manifest(path):
    """the twin's manifest as the follow loader returns it, with twin_of / gap_exclusion "hold" put back. Until the follow loader knows those two keys they are set aside for its validation only."""
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raise Refusal("twin manifest unreadable")
    if not isinstance(raw, dict):
        raise Refusal("twin manifest is not an object")
    try:
        return O.load_manifest(path)
    except O.Refused:
        pass
    plain = {k: v for k, v in raw.items() if k != "twin_of"}
    if plain.get("gap_exclusion") == "hold":
        plain["gap_exclusion"] = "on"
    with tempfile.TemporaryDirectory(prefix="osb-recon-tm-") as d:
        f = Path(d) / "m.json"
        f.write_text(json.dumps(plain))
        try:
            m = O.load_manifest(str(f))
        except O.Refused:
            raise Refusal("twin manifest refused")
    if "twin_of" in raw:
        m["twin_of"] = raw["twin_of"]
    if "gap_exclusion" in raw:
        m["gap_exclusion"] = raw["gap_exclusion"]
    return m


def blind_ok(v):
    return is_num(v) and math.isfinite(v) and v >= 0


def read_twin_flags(run_dir):
    """{(position_id, stake): {gap_in_hold, blind_s, gap_count, gap_holes}} from the twin run's ledger trade rows; absent fields stay absent (tolerant)"""
    out = {}
    for f in sorted(Path(run_dir, "ledger").glob("*/trades-*.jsonl")):
        data = f.read_bytes()
        for ln in data[: data.rfind(b"\n") + 1].split(b"\n"):
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if isinstance(r, dict) and r.get("kind") == "trade" and isinstance(r.get("position_id"), str) and is_num(r.get("stake_sol")):
                if r.get("blind_s") is not None and not blind_ok(r["blind_s"]):
                    raise Refusal("twin ledger field blind_s is malformed")
                out[(r["position_id"], str(float(r["stake_sol"])))] = {k: r[k] for k in ("gap_in_hold", "blind_s", "gap_count", "gap_holes") if k in r}
    return out


def read_twin(twin_path, a_manifest, roots=None):
    """-> {positions, flags, book_id}: the twin's own board pricing. Refused unless it is the declared twin of A (twin_of == A's book_id, gap_exclusion hold) and its decisions.md5 equals A's when both exist."""
    m = load_twin_manifest(twin_path)
    if m.get("twin_of") != a_manifest["book_id"] or m.get("book_id") == a_manifest["book_id"]:
        raise Refusal("twin manifest: twin_of is not book A")
    if m.get("gap_exclusion") != "hold":
        raise Refusal("twin manifest: gap_exclusion is not hold")
    try:
        O.guard_paths(m, roots or (O.OSB_ROOT,))
    except O.Refused:
        raise Refusal("twin manifest refused")
    for k in sorted((set(m) | set(a_manifest)) - set(TWIN_MAY_DIFFER)):
        if m.get(k) != a_manifest.get(k):
            raise Refusal("twin manifest differs from book A in key %s" % k)
    try:  # each side: its decisions.md5 file when present, else computed from that book's ledger rows (osb_follow.book_decisions_md5: the one definition; never written here)
        da, dt = O.book_decisions_md5(a_manifest["out_root"]), O.book_decisions_md5(m["out_root"])
    except (OSError, ValueError):
        da = dt = None
    if da is None or dt is None:
        raise Refusal("decisions md5 unreadable for book A or the twin (no file and no ledger rows)")
    if da != dt:
        raise Refusal("twin decisions md5 differs from book A")
    b = {"m": m, "role": "variant", "start_ms": 0, "sha": O.manifest_sha256(m)}
    link = Path(m["out_root"]) / "book"
    run0 = os.path.realpath(link)
    info = B.inspect_book(b, int(time.time() * 1000), roots)
    if info["pnl"] is None or info["end"] is None:
        raise Refusal("twin has no priced hour yet (state %s)" % info["state"])
    flags = read_twin_flags(run0)
    if os.path.realpath(link) != run0:
        raise Refusal("twin link moved while reading")
    return {"book_id": m["book_id"], "positions": {p["pid"]: p for p in info["pnl"].get("positions", [])}, "flags": flags}


def read_sim(manifest_path, roots=None, twin_path=None):
    """-> dict for book A: the board's own pricing (osb_board.inspect_book) and A's decision rows, both from ONE run directory (a moved `book` link is refused)."""
    try:
        m = O.load_manifest(manifest_path)
        O.guard_paths(m, roots or (O.OSB_ROOT,))
    except O.Refused:
        raise Refusal("manifest refused")
    b = {"m": m, "role": "control", "start_ms": 0, "sha": O.manifest_sha256(m)}
    link = Path(m["out_root"]) / "book"
    run0 = os.path.realpath(link)
    info = B.inspect_book(b, int(time.time() * 1000), roots)
    if info["pnl"] is None or info["end"] is None:
        raise Refusal("book A has no priced hour yet (state %s)" % info["state"])
    entries, exits = [], {}
    d = Path(run0) / "decisions" / ARM
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
    if os.path.realpath(link) != run0:
        raise Refusal("book A link moved while reading")
    positions = {p["pid"]: p for p in info["pnl"].get("positions", [])}
    order = sorted(info["pnl"].get("positions", []), key=lambda q: (q["t0"], q["pid"]))  # the board's own tid ordering (osb_board.book_trades)
    tids = {p["pid"]: "%s-%05d" % (m["book_id"], i) for i, p in enumerate(order, 1)}
    start_ms = B.parse_instant(m["start_hour"] + ":00:00Z") if re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d", str(m["start_hour"])) else 0
    twin = read_twin(twin_path, m, roots) if twin_path else None
    return {"twin": twin, "book_id": m["book_id"], "entries": entries, "exits": exits, "positions": positions, "tids": tids, "stakes": m["stakes_sol"], "end_ms": info["end"], "start_ms": start_ms, "state": info["state"]}


# ------------------------------------------------------------------------------------------------------------------------------ core
def key_of(pool, slot, ms):
    return (pool, slot, ms) if isinstance(pool, str) and is_num(slot) and is_num(ms) else None


def sim_price(pos, stake_sol):
    """A's landing price at the live stake: constant product on the landing state (Qe, Be), pool fee tier of that state (tools.arena_stack.fee_ppm). SOL per UI token. None when not derivable."""
    land = (pos or {}).get("landing") or {}
    Qe, Be = land.get("Qe"), land.get("Be")
    if not all(is_num(x) and x > 0 for x in (Qe, Be)):
        return None
    try:
        fee = float(AS.fee_ppm(Qe, Be))
    except Exception:  # noqa: BLE001
        return None
    s = stake_sol * 1e9
    net = s * (1 - fee / 1e6)
    tok = Be * net / (Qe + net)
    return s / (tok * 1000.0) if tok > 0 else None


def live_price(o):
    sp, tk = o.get("fill_spend_lamports"), o.get("fill_tokens_received")
    if o.get("fill_estimated") or o.get("fill_tokens_source") not in FILLED_TOKEN_SOURCES or not all(is_num(x) and x > 0 for x in (sp, tk)):
        return None
    return sp / (tk * 1000.0)


def rung_of(stake_sol, stakes):
    return next((s for s in stakes if stake_sol is not None and abs(s - stake_sol) < STAKE_TOL), None)


def tms(o):
    for f in ("trig_ms", "decision_ms", "led_intent_ms"):
        if is_num(o.get(f)):
            return o[f]
    return None


def down_spans(ex_rows, with_starts=False):
    """non-failed restart spans. with_starts: also the span of a crash, from a start row's prev_last_ms (last host log row before it) to the start's own instant. Only the live_nonrule_exit
    check asks for these: a quiet host leaves a long prev_last_ms gap that is not proof of a down span, so H2's host_down_no_row and the tp-in-hole code keep the restart rows alone."""
    out = [(r["down_from_ms"], r["down_to_ms"]) for r in ex_rows if r["kind"] == "restart" and not r.get("failed") and is_num(r.get("down_from_ms")) and is_num(r.get("down_to_ms"))]
    if with_starts:
        out += [(r["prev_last_ms"], r["t_ms"]) for r in ex_rows if r["kind"] == "start" and is_num(r.get("prev_last_ms")) and is_num(r.get("t_ms")) and r["prev_last_ms"] < r["t_ms"]]
    return out


def restart_type(r):
    why = str(r.get("reason") or "")
    return why.startswith("pool_blocked_after_restart") or "restart_shadow" in why or (why == "feed_gap" and r.get("gap_kind") == "host_down")


def classify(ex_rows, sim, win_from, win_to):
    """-> (rows, pairs, live_sent, extras). rows: output rows that are not matched; pairs: (live order, sim entry) of the matched; live_sent: [(oid, filled)] of the live sends in the window."""
    orders = [r for r in ex_rows if r["kind"] == "order"]
    refusals = [r for r in ex_rows if r["kind"] == "gap_refusal"]
    spans = down_spans(ex_rows)
    inwin = lambda t: t is not None and win_from <= t < win_to
    ref_by_key = {}  # ALL refusal rows per key (QP ruling 2026-11-14 point 1: the last row no longer wins)
    for r in refusals:
        k = key_of(r.get("pool"), r.get("trig_slot"), r.get("trig_ms"))
        if k:
            ref_by_key.setdefault(k, []).append(r)
    sim_keys, dup_sim = {}, []
    for e in sorted((e for e in sim["entries"] if inwin(e["trig_ms"])), key=lambda e: (e["trig_ms"], e["order_id"])):
        k = key_of(e["pool"], e["trig_slot"], e["trig_ms"])
        if k is None or k in sim_keys:
            dup_sim.append(e)
        else:
            sim_keys[k] = e
    extras = {"executor_unlisted": 0, "duplicate_keys": len(dup_sim)}
    rows, pairs, live_sent = [], [], []
    used, unsent = set(), {}
    filled = lambda o: bool(o.get("led_ok_buy"))
    for o in sorted((o for o in orders if inwin(tms(o)) and (o.get("has_host_entry") or o.get("sent"))), key=lambda o: (tms(o), o["order_id"])):
        sent = bool(o.get("sent"))
        if sent:
            live_sent.append((o["order_id"], filled(o)))
        k = key_of(o.get("pool"), o.get("trig_slot"), o.get("trig_ms")) if o.get("has_host_entry") else None
        base = {"order_id": o["order_id"], "tid": None, "entry_utc": utc(tms(o)), "stake_sol": (o.get("stake_lamports") or o.get("led_stake_lamports") or 0) / 1e9, "class": "live_only"}
        if k is None or k not in sim_keys:
            rows.append(dict(base, reason="superset_violation"))
        elif k in used or k in unsent:
            rows.append(dict(base, reason="duplicate_key"))
        elif not sent:
            unsent[k] = o
        else:
            used.add(k)
            tid = sim["tids"].get(sim_keys[k]["order_id"])
            if filled(o):
                pairs.append((o, sim_keys[k]))
            elif (o.get("buy_failed") or o.get("led_writeoff") in ("buy_expired", "not_sent")) and o.get("landed_ms") is None:
                rows.append(dict(base, tid=tid, **{"class": "sim_only", "reason": "buy_not_landed"}))
            else:
                rows.append(dict(base, tid=tid, **{"class": "sim_only", "reason": "unexplained"}))

    def sim_reason(e, k):
        t, lo = e["trig_ms"], unsent.get(k)
        if lo is not None:
            if lo.get("refused_why"):
                code = executor_code(lo["refused_why"])
                if code:
                    return code
                extras["executor_unlisted"] += 1
            return "unexplained"
        rfs = ref_by_key.get(k)
        if rfs:
            if any(restart_type(r) for r in rfs):  # (a)
                return "restart_lockout"
            if any(str(r.get("reason") or "") == "feed_gap" for r in rfs):  # (b) a feed_gap that is not host_down (host_down is restart-type, caught above)
                return "feed_gap_mismatch"
            if all(str(r.get("reason") or "") == "pool_blocked_after_gap" for r in rfs):  # (c) the same pool's earlier feed_gap refusals whose block window covers this trigger
                cause = [r for r in refusals if r.get("pool") == e["pool"] and str(r.get("reason") or "") == "feed_gap" and is_num(r.get("trig_ms")) and r["trig_ms"] < t
                         and (not is_num(r.get("blocked_until_ms")) or r["blocked_until_ms"] >= t)]
                if any(r.get("gap_kind") != "host_down" for r in cause):
                    return "feed_gap_mismatch"
                return "restart_lockout" if cause else "unexplained"
            return "unexplained"  # (d)
        if any(a <= t <= b for a, b in spans):
            return "host_down_no_row"
        same = [r for r in refusals if r.get("pool") == e["pool"]]
        near = lambda r: is_num(r.get("trig_ms")) and abs(r["trig_ms"] - t) <= TIMING_EDGE_MS
        if any(near(r) and r.get("reason") == "feed_gap" and r.get("gap_kind") != "host_down" for r in same):
            return "feed_gap_mismatch"  # never absorbed by timing_edge: gap rows are in the tape and must agree
        if any((near(r) and (str(r.get("reason") or "").startswith(("pool_blocked", "cooldown")))) or (is_num(r.get("blocked_until_ms")) and abs(r["blocked_until_ms"] - t) <= TIMING_EDGE_MS) for r in same):
            return "timing_edge"
        return "unexplained"

    for k, e in sim_keys.items():
        if k not in used:
            rows.append({"order_id": None, "tid": sim["tids"].get(e["order_id"]), "entry_utc": utc(e["trig_ms"]), "stake_sol": None, "class": "sim_only", "reason": sim_reason(e, k)})
    for e in dup_sim:
        rows.append({"order_id": None, "tid": sim["tids"].get(e["order_id"]), "entry_utc": utc(e["trig_ms"]), "stake_sol": None, "class": "sim_only", "reason": "unexplained"})
    return rows, pairs, live_sent, extras


def pair_metrics(o, e, sim, spans, win_to, nr_spans=()):
    lam = o.get("led_stake_lamports") or o.get("stake_lamports") or 0
    stake = lam / 1e9
    rung = rung_of(stake, sim["stakes"])
    sk = str(float(rung)) if rung is not None else None
    pos = sim["positions"].get(e["order_id"])
    twin_pos, flag, no_word = None, {}, None
    if pos is not None and sk and (pos.get("status") or {}).get(sk) == "engine_excluded":
        why = (pos.get("reason") or {}).get(sk)
        tw = sim.get("twin")
        if tw is not None and is_gap_reason(why):
            tp = tw["positions"].get(e["order_id"])
            fl = tw["flags"].get((e["order_id"], sk)) or {}
            if tp is not None and (tp.get("status") or {}).get(sk) == "closed":
                if fl.get("gap_in_hold") is True:
                    twin_pos, flag = tp, fl
                else:
                    no_word = "other"  # a twin price for a trade strict left out for a gap, without the gap_in_hold flag: the inputs disagree
            else:
                no_word = no_sim_word(((tp or {}).get("reason") or {}).get(sk) if tp is not None else why)
        else:
            no_word = no_sim_word(why)
    if twin_pos is not None:
        pos = twin_pos
    gih = twin_pos is not None  # the flag is "priced by the twin"
    blind = flag.get("blind_s") if blind_ok(flag.get("blind_s")) else None
    m = {"order_id": o["order_id"], "tid": sim["tids"].get(e["order_id"]), "entry_utc": utc(tms(o)), "stake_sol": stake, "no_rung": rung is None, "class": "matched", "reason": "",
         "gap_in_hold": gih, "blind_s": blind if gih else None, "no_sim_price": no_word,
         "entry_slip_pp": None, "landing_ms": None, "landing_k": None, "exit_reason_live": None, "exit_reason_host": None, "exit_reason_sim": None, "exit_dt_ms": None, "exit_coded": None,
         "net_live_sol": None, "net_sim_sol": None, "net_live_pp": None, "net_sim_pp": None, "fees_live_pp": None, "fees_model_pp": None, "rent_unreclaimed": o.get("led_rent_unreclaimed_lamports") or 0,
         "_t": tms(o)}
    lp, sp = live_price(o), sim_price(pos, stake)
    if lp and sp and no_word is None:
        m["entry_slip_pp"] = (lp / sp - 1.0) * 100.0
    if is_num(o.get("landed_ms")) and is_num(o.get("decision_ms")):
        m["landing_ms"] = o["landed_ms"] - o["decision_ms"]
    if is_num(o.get("landed_slot")) and is_num(o.get("decision_slot")):
        m["landing_k"] = o["landed_slot"] - o["decision_slot"]
    sx = sim["exits"].get(e["order_id"]) or []
    if twin_pos is not None:  # the twin's own exit (take-profit blind in a hole) rather than the decision row
        sim_reason, sim_ms = ((pos.get("exit_reason") or {}).get(sk), pos.get("t1"))
        if sim_reason is None and sx:
            sim_reason, sim_ms = sx[0]
    else:
        sim_reason, sim_ms = (sx[0][0], sx[0][1]) if sx else (((pos or {}).get("exit_reason") or {}).get(sk) if sk else None, (pos or {}).get("t1"))
    sim_closed = bool(sx) or (pos or {}).get("t1") is not None
    live_closed = o.get("led_close_ev") == "exit" or is_num(o.get("sell_landed_ms"))
    live_reason = o.get("sell_reason_bridge") or (o.get("led_close_reason") if o.get("led_close_ev") == "exit" else None)
    host = o.get("exit_reason") if o.get("has_host_exit") else None
    close_ms = o.get("sell_landed_ms") if is_num(o.get("sell_landed_ms")) else o.get("led_close_ms")
    m["exit_reason_live"], m["exit_reason_host"], m["exit_reason_sim"] = xr(live_reason), xr(host), xr(sim_reason)
    if is_num(close_ms) and is_num(sim_ms):
        m["exit_dt_ms"] = close_ms - sim_ms
    h3 = "open"
    nonrule = bool(live_closed and live_reason == NONRULE_CLOSE_WORD and not o.get("has_host_exit") and is_num(o.get("decision_ms"))
                   and is_num(close_ms) and any(o["decision_ms"] < a <= close_ms + NONRULE_LANDING_SLACK_MS for a, _b in nr_spans))
    if nonrule:
        m["exit_coded"], m["reason"], h3 = True, CODED_NONRULE, "live_nonrule"
    elif live_closed and sim_closed:
        if live_reason is None or sim_reason is None:
            h3 = "mismatch"
        elif live_reason != sim_reason:
            coded = bool(sim_reason == CODED_SIM and live_reason == CODED_LIVE and not o.get("has_host_exit") and is_num(sim_ms) and any(a <= sim_ms <= b for a, b in spans))
            holes = [h for h in (flag.get("gap_holes") or []) if isinstance(h, (list, tuple)) and len(h) == 2 and is_num(h[0]) and is_num(h[1])]
            t_in, t_out = (pos or {}).get("t0"), sim_ms
            hx_ms = o.get("exit_ms")
            tp_gap = bool(not coded and gih and live_reason == CODED_SIM and o.get("has_host_exit") and host == CODED_SIM and is_num(hx_ms) and sim_reason == "time_24h"
                          and is_num(t_in) and is_num(t_out) and any(a <= hx_ms <= b and t_in <= a and b <= t_out for a, b in holes))
            m["exit_coded"] = coded or tp_gap
            if tp_gap:
                m["reason"] = CODED_TP_GAP
            h3 = "coded" if (coded or tp_gap) else "mismatch"
        elif host is not None and host != live_reason:
            h3 = "mismatch"
        else:
            h3 = "ok"
    elif live_closed != sim_closed:
        cm = close_ms if live_closed else sim_ms
        h3 = "mismatch" if is_num(cm) and win_to - cm > H3_OPEN_TOLERANCE_MS else "pending"
    m["_h3"] = h3
    m["_blind_priced"] = twin_pos is not None
    closed_cash = o.get("led_close_ev") == "exit" and lam > 0 and is_int(o.get("led_net_lamports"))
    if closed_cash:
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


def evaluate(rows, metrics, win_to, extras):
    """H1..H3 on the whole window (FAIL at any n); S1..S4 on the newest S_ROLLING_N matched filled trades at a rung with entry in the last 7 days (INSUFFICIENT below S1_MIN_MATCHED)"""
    lo = [r for r in rows if r["class"] == "live_only"]
    bad = [r for r in rows if r["class"] == "sim_only" and r["reason"] in ("unexplained", "feed_gap_mismatch")]
    per_day = {}
    for r in bad:
        per_day[r["entry_utc"][:10]] = per_day.get(r["entry_utc"][:10], 0) + 1
    nothing = not rows and not metrics
    h1 = band("INSUFFICIENT" if nothing else ("PASS" if len(lo) <= H1_MAX_LIVE_ONLY else "FAIL"), n_live_only=len(lo), max=H1_MAX_LIVE_ONLY)
    h2 = band("INSUFFICIENT" if nothing else ("PASS" if all(v <= H2_MAX_UNEXPLAINED_PER_DAY for v in per_day.values()) else "FAIL"), unexplained_by_day=dict(sorted(per_day.items())),
              max_per_day=H2_MAX_UNEXPLAINED_PER_DAY, executor_unlisted=extras["executor_unlisted"], duplicate_keys=extras["duplicate_keys"])
    cmp_ = [m for m in metrics if m["_h3"] in ("ok", "coded", "mismatch", "live_nonrule")]
    mism = [m for m in metrics if m["_h3"] == "mismatch"]
    nr = [m for m in metrics if m["_h3"] == "live_nonrule"]
    nr_cap = len(nr) > H3_NONRULE_MAX_N or len(nr) > H3_NONRULE_MAX_FRAC * len(metrics)
    h3 = band("FAIL" if len(mism) > H3_MAX_EXIT_MISMATCH or nr_cap else ("INSUFFICIENT" if not cmp_ else "PASS"), n_compared=len(cmp_), n_mismatch_uncoded=len(mism), n_coded=sum(1 for m in metrics if m["_h3"] == "coded"),
              n_tp_in_sim_gap=sum(1 for m in metrics if m.get("reason") == CODED_TP_GAP),
              n_live_nonrule_exit=len(nr), nonrule_cap_exceeded=nr_cap, nonrule_cap="max %d and %d%% of matched trades" % (H3_NONRULE_MAX_N, int(100 * H3_NONRULE_MAX_FRAC)),
              nonrule_net_live_sol=[m["net_live_sol"] for m in nr])
    roll = [m for m in sorted(metrics, key=lambda m: m["_t"]) if not m["no_rung"] and win_to - S_RECENT_MS <= m["_t"] < win_to][-S_ROLLING_N:]
    slips = [m["entry_slip_pp"] for m in roll if m["entry_slip_pp"] is not None]
    if len(slips) < S1_MIN_MATCHED:
        s1 = band("INSUFFICIENT", n=len(slips), min=S1_MIN_MATCHED)
    else:
        med, beyond = statistics.median(slips), sum(1 for x in slips if abs(x) > S1_BEYOND_PP)
        s1 = band("PASS" if S1_MEDIAN_SLIP_PP[0] <= med <= S1_MEDIAN_SLIP_PP[1] and beyond <= S1_MAX_BEYOND_N else "FAIL", n=len(slips), median_slip_pp=med, n_beyond=beyond, band_pp=list(S1_MEDIAN_SLIP_PP))
    both = [m for m in roll if m["net_live_pp"] is not None and m["net_sim_pp"] is not None and m["_h3"] != "live_nonrule"]  # a live non-rule exit is not comparable: out of S2 only

    def split(xs):
        d = [m["net_live_pp"] - m["net_sim_pp"] for m in xs]
        return {"n": len(xs), "median_diff_pp": statistics.median(d) if d else None, "sum_live_sol": sum(m["net_live_sol"] for m in xs), "sum_sim_sol": sum(m["net_sim_sol"] for m in xs)}
    by_flag = {"flagged": split([m for m in both if m.get("gap_in_hold")]), "unflagged": split([m for m in both if not m.get("gap_in_hold")])}
    if len(both) < S1_MIN_MATCHED:
        s2 = band("INSUFFICIENT", n=len(both), min=S1_MIN_MATCHED, by_flag=by_flag)
    else:
        diffs = [m["net_live_pp"] - m["net_sim_pp"] for m in both]
        sl, ss, st = sum(m["net_live_sol"] for m in both), sum(m["net_sim_sol"] for m in both), sum(m["stake_sol"] for m in both)
        med = statistics.median(diffs)
        s2 = band("PASS" if abs(med) <= S2_MEDIAN_NET_DIFF_PP and abs(sl - ss) <= S2_SUM_REL * st else "FAIL", n=len(both), median_diff_pp=med, sum_live_sol=sl, sum_sim_sol=ss, sum_stake_sol=st, by_flag=by_flag)
    fm = [m for m in roll if m["fees_live_pp"] is not None]
    rent_all = sum(m["rent_unreclaimed"] for m in metrics)  # any closed matched trade in the window
    if rent_all > S3_MAX_RENT_UNRECLAIMED:
        s3 = band("FAIL", n=len(fm), rent_unreclaimed_lamports=rent_all)
    elif len(fm) < S1_MIN_MATCHED:
        s3 = band("INSUFFICIENT", n=len(fm), min=S1_MIN_MATCHED)
    else:
        exc = statistics.fmean(m["fees_live_pp"] - m["fees_model_pp"] for m in fm)
        s3 = band("PASS" if exc <= S3_FEE_EXCESS_PP else "FAIL", n=len(fm), mean_fee_excess_pp=exc, rent_unreclaimed_lamports=rent_all)
    lat = [m for m in roll if m["landing_ms"] is not None and m["landing_k"] is not None]
    if len(lat) < S1_MIN_MATCHED:
        s4 = band("INSUFFICIENT", n=len(lat), min=S1_MIN_MATCHED)
    else:
        p50, p90 = pct([m["landing_ms"] for m in lat], 0.5), pct([m["landing_ms"] for m in lat], 0.9)
        k50, k90 = pct([m["landing_k"] for m in lat], 0.5), pct([m["landing_k"] for m in lat], 0.9)
        s4 = band("PASS" if p50 <= S4_P50_MS and p90 <= S4_P90_MS and k50 <= S4_K_P50 and k90 <= S4_K_P90 else "FAIL", n=len(lat), p50_ms=p50, p90_ms=p90, landing_k_p50=k50, landing_k_p90=k90,
                  model_ms=MODEL_LANDING_MS, model_k_p50=S4_K_P50, model_k_p90=S4_K_P90)
    bands = {"h1": h1, "h2": h2, "h3": h3, "s1": s1, "s2": s2, "s3": s3, "s4": s4}
    sts = [b["status"] for b in bands.values()]
    return bands, ("FAIL" if "FAIL" in sts else "INSUFFICIENT" if "INSUFFICIENT" in sts else "PASS")


def dropped_bridge_only(meta):
    """the extract meta's stats.dropped_bridge_only (orders only in a shared bridge log, dropped as not ours); 0 when the key is absent or not a whole number"""
    st = meta.get("stats") if isinstance(meta.get("stats"), dict) else {}
    v = st.get("dropped_bridge_only", meta.get("dropped_bridge_only", 0))
    return v if is_int(v) and v >= 0 else 0


def wilson_block(k, n):
    lo, hi = wilson(k, n)
    return {"n": n, "n_failed": k, "rate": (k / n) if n else None, "wilson95_lo": lo, "wilson95_hi": hi}


def reconcile(ex_rows, ex_meta, sim, now_ms):
    head = ex_rows[0]
    win_from, win_to = max(head["since_ms"], sim["start_ms"]), min(head["as_of_ms"], sim["end_ms"])
    spans, nr_spans = down_spans(ex_rows), down_spans(ex_rows, with_starts=True)
    rows, pairs, live_sent, extras = classify(ex_rows, sim, win_from, win_to)
    metrics = [pair_metrics(o, e, sim, spans, win_to, nr_spans) for o, e in pairs]
    bands, status = evaluate(rows, metrics, win_to, extras)
    sent_ids = {oid for oid, _ in live_sent}
    sent = [r for r in ex_rows if r["kind"] == "order" and r["order_id"] in sent_ids]
    reasons = {}
    for r in rows:
        k = r["class"] + ":" + r["reason"]
        reasons[k] = reasons.get(k, 0) + 1
    nsp = {w: sum(1 for m in metrics if m.get("no_sim_price") == w) for w in NO_SIM_WORDS}
    leg = {"sim_leg_failed_live_filled": sum(1 for m in metrics if m["_sim_leg_state"] not in (None, "filled")), "no_rung": sum(1 for m in metrics if m["no_rung"])}
    out_rows = rows + [{k: v for k, v in m.items() if not k.startswith("_")} for m in metrics]
    out_rows.sort(key=lambda r: (r["entry_utc"], str(r.get("order_id") or ""), str(r.get("tid") or "")))
    buy = wilson_block(sum(1 for _, f in live_sent if not f), len(live_sent))
    sells = [r for r in sent if is_num(r.get("sell_send_ms"))]
    sell = wilson_block(sum(1 for r in sells if r.get("sell_failed")), len(sells))
    return {"schema": SCHEMA, "banner": B.BANNER, "generated_utc": utc(now_ms), "generated_ms": now_ms, "status": status, "book_id": sim["book_id"],
            "window": {"from_utc": utc(win_from), "to_utc": utc(win_to)}, "extract_as_of_utc": utc(head["as_of_ms"]), "extract_as_of_ms": head["as_of_ms"], "extract_sha256_12": ex_meta["extract_sha256"][:12],
            **bands, "twin_book_id": (sim.get("twin") or {}).get("book_id"), "dropped_bridge_only": dropped_bridge_only(ex_meta),
            "no_sim_price_counts": nsp, "gap_in_hold_n": sum(1 for m in metrics if m.get("gap_in_hold")),
            "counts": {"matched": len(metrics), "live_only": sum(1 for r in rows if r["class"] == "live_only"), "sim_only": sum(1 for r in rows if r["class"] == "sim_only")},
            "counts_by_reason": dict(sorted(reasons.items())), "executor_unlisted": extras["executor_unlisted"], "discordance": leg,
            "live_failure": {"n_sent": buy["n"], "n_not_filled": buy["n_failed"], "rate": buy["rate"], "wilson95_lo": buy["wilson95_lo"], "wilson95_hi": buy["wilson95_hi"], "flat_fail_assumed": FLAT_FAIL_ASSUMED,
                             "sells": {"n_sent": sell["n"], "n_failed": sell["n_failed"], "rate": sell["rate"], "wilson95_lo": sell["wilson95_lo"], "wilson95_hi": sell["wilson95_hi"]},
                             "note": "live only; not combined with sim figures"},
            "rows": out_rows}


# ------------------------------------------------------------------------------------------------------------------------------ output
def f2(x):
    return "n/a" if x is None else "%.2f" % x


def recon_md(res):
    md = ["# Open sim recon: book %s against the live FLOOR bot (paper)" % res["book_id"], "", "> " + res["banner"], "",
          "- Status: **%s**. Window %s to %s; extract as of %s (%s). Generated %s." % (res["status"], res["window"]["from_utc"], res["window"]["to_utc"], res["extract_as_of_utc"], res["extract_sha256_12"], res["generated_utc"]),
          "- Counts: matched %d, live-only %d, sim-only %d. By reason: %s." % (res["counts"]["matched"], res["counts"]["live_only"], res["counts"]["sim_only"], json.dumps(res["counts_by_reason"], sort_keys=True))
          + " Bridge-only orders dropped from the extract (not ours): %d. Gap-in-hold trades in the twin set: %d. No sim figure (no_sim, outside S1/S2): gap at entry %d, gap at exit %d, missing V %d, other %d. Take-profits in a sim-side hole (tp_in_sim_gap): %d. Strict can overstate: in Nov 2-12 the trades it left out for a feed gap were net negative (DEC-035 Am.6 Erratum 3). Twin: %s." % (
              res["dropped_bridge_only"], res["gap_in_hold_n"], *(res["no_sim_price_counts"][w] for w in NO_SIM_WORDS), res["h3"].get("n_tp_in_sim_gap", 0), res["twin_book_id"] or "none"),
          "- Live non-rule exits (coded live_nonrule_exit, set after the first run: post hoc; excluded from S2, kept in S1, S3, S4; cap %s): %d%s." % (
              res["h3"]["nonrule_cap"], res["h3"]["n_live_nonrule_exit"],
              "".join("; order %s live net %s SOL" % (r["order_id"], f2(r.get("net_live_sol"))) for r in res["rows"] if r.get("reason") == CODED_NONRULE)),
          "- Bands: " + "; ".join("%s %s" % (k.upper(), res[k]["status"]) for k in ("h1", "h2", "h3", "s1", "s2", "s3", "s4")) + ". Constants are fixed by QP before the first run.",
          "- Live failure (buy not filled / sent): %d / %d, Wilson 95%% %s to %s; sells failed / sent %d / %d; beside the flat %d%% assumption; live only, not combined with sim figures." % (
              res["live_failure"]["n_not_filled"], res["live_failure"]["n_sent"], f2(None if res["live_failure"]["wilson95_lo"] is None else 100 * res["live_failure"]["wilson95_lo"]),
              f2(None if res["live_failure"]["wilson95_hi"] is None else 100 * res["live_failure"]["wilson95_hi"]), res["live_failure"]["sells"]["n_failed"], res["live_failure"]["sells"]["n_sent"], int(100 * FLAT_FAIL_ASSUMED)),
          "- S2 by flag (gap-in-hold through the twin / not): " + "; ".join("%s n %d, median diff %s pp, live %s SOL, sim %s SOL" % (
              k, v["n"], f2(v["median_diff_pp"]), f2(v["sum_live_sol"]), f2(v["sum_sim_sol"])) for k, v in res["s2"]["by_flag"].items()) + ". Neither set is decision-grade (recon NOT COMPARABLE).",
          "", "| order | tid | class | reason | entry UTC | stake | slip pp | landing ms | landing k | exit live | exit host | exit sim | exit dt ms | net live SOL | net sim SOL | gap-in-hold | blind s | no_sim |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in res["rows"][-200:]:
        md.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r.get("order_id") or "-", r.get("tid") or "-", r["class"] + (" no_rung" if r.get("no_rung") else ""), r["reason"] or "-", r["entry_utc"], "n/a" if r.get("stake_sol") is None else "%.2f" % r["stake_sol"],
            f2(r.get("entry_slip_pp")), r.get("landing_ms", "-"), r.get("landing_k", "-"), r.get("exit_reason_live") or "-", r.get("exit_reason_host") or "-", r.get("exit_reason_sim") or "-", r.get("exit_dt_ms", "-"),
            f2(r.get("net_live_sol")), f2(r.get("net_sim_sol")), "yes" if r.get("gap_in_hold") else "-", f2(r.get("blind_s")) if r.get("blind_s") is not None else "-", r.get("no_sim_price") or "-"))
    return "\n".join(md) + "\n"


def _walk(x, path=""):
    if isinstance(x, dict):
        for k, v in x.items():
            yield from _walk(v, "%s.%s" % (path, k) if path else str(k))
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from _walk(v, "%s[%d]" % (path, i))
    elif isinstance(x, str):
        yield path, x


def check_output(res, md):
    """the write-time rules. A refusal names the field (or recon.md) and the rule; never the value."""
    for path, s in _walk(res):
        field = re.sub(r"\[\d+\]", "", path).rsplit(".", 1)[-1]
        if field == "order_id":
            if not OID_OK.fullmatch(s):
                raise Refusal("output field %s: order id shape rule" % re.sub(r"\[\d+\]", "[]", path))
            continue
        low = s.lower()
        for w in B.FORBIDDEN:
            if w in low:
                raise Refusal("output field %s: forbidden word rule (%s)" % (re.sub(r"\[\d+\]", "[]", path), w))
        if B58_RUN.search(s) or HEX_RUN.search(s):
            raise Refusal("output field %s: id-shaped value rule" % re.sub(r"\[\d+\]", "[]", path))
    oids = sorted({r["order_id"] for r in res["rows"] if r.get("order_id")}, key=len, reverse=True)
    text = md
    for oid in oids:
        text = text.replace(oid, "OID")
    low = text.lower()
    for w in B.FORBIDDEN:
        if w in low:
            raise Refusal("recon.md: forbidden word rule (%s)" % w)
    if B58_RUN.search(text) or HEX_RUN.search(text):
        raise Refusal("recon.md: id-shaped value rule")


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
    try:
        return B.guard_out_dir(out_dir, out_roots or OUT_ROOTS)
    except B.Refusal:
        raise Refusal("--out-dir is outside the allowed output roots")


def run(a, roots=None, out_roots=None, now_ms=None):
    out_dir = guard([a.extract_dir, a.manifest, a.out_dir] + ([a.twin_manifest] if a.twin_manifest else []), a.out_dir, out_roots)
    now = now_ms if now_ms is not None else (a.now_ms if a.now_ms is not None else int(time.time() * 1000))
    os.makedirs(out_dir, exist_ok=True)
    lock = open(os.path.join(out_dir, ".osb-recon.lock"), "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise Refusal("another osb_recon run holds the lock")
    ex_rows, ex_meta = read_extract(a.extract_dir)
    sim = read_sim(a.manifest, roots, a.twin_manifest)
    res = reconcile(ex_rows, ex_meta, sim, now)
    md = recon_md(res)
    check_output(res, md)
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
    ap.add_argument("--twin-manifest", default=None, help="book A's gap_exclusion hold twin (optional)")
    a = ap.parse_args(argv)
    try:
        return run(a, **kw)
    except Refusal as e:
        print("osb_recon: refused: %s" % e, file=sys.stderr, flush=True)
        return 2
    except (B.Refusal, O.Refused):
        print("osb_recon: refused: manifest or book check", file=sys.stderr, flush=True)
        return 2
    except (AttributeError, TypeError, ValueError, KeyError, IndexError) as e:
        print("osb_recon: refused: malformed input (%s)" % type(e).__name__, file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
