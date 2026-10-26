"""P29 runner: counts-only pass (default) and the pricing look (REFUSED unless authorised for the exact head). PAPER ONLY, 0 Helius, no key.

    python3 -m tools.p29_keeper_run --keeper KEEPER.jsonl --out DIR [--blocks fresh-0903 exp011-0909 ...] [--coverage coverage.json]
    python3 -m tools.p29_keeper_run ... --price      # needs env P29_PRICE_AUTH == this repo's HEAD sha (set by the manager after reviewer verdict + QP OK on the code); one pooled look

Data access is P29's OWN allowlist (tools.p29_keeper_coverage.BLOCKS: exactly the declared blocks; hours = the block's hours up to the end of its last declared day; warm-up hours are
loaded but their events are neither recorded nor priced). The kit's CLI guard is not used and not changed. Still refused here: every kit FORBIDDEN token except 'exp011' (the manager line
in PRICING-DECLARE s4 covers it), any hour >= 2026-10-02T10 (kit SEALED_HOURS), anything outside the allowlisted directories after realpath, any arena / meta-capture path. The cost and
fail stack is the kit's (imported) at send 160,000 (DEC-031 Am.8); its sha256 pins (PRICING-DECLARE s0) are checked before anything is read.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from tools import arena_screen as K
from tools.p29_keeper_coverage import BLOCKS, KEEPER_CUTOFF, fill_map
from tools.p29_keeper_rule import ENTRY_MODES, P29KeeperRule, balance, cluster_boot_lo, landing_disagree, match_controls

REPO = Path(__file__).resolve().parent.parent
PINS = {  # sha256 at main 6669c4cc7be1a76c51abc201374c1dab5178c632 (PRICING-DECLARE s0); the run refuses if any differs
    "tools/arena_screen.py": "b37282c7d23d3bfca310f2574b8bc2b9b0981e0e73ee2537e118f779244fbb57",
    "tools/arena_stack.py": "a39b2ba68dd46b7d3a555fb8e06580718300f354020c1f91280ab7a0e0856778",
    "tools/arena_paper.py": "9d2fdb04a3da9bb38de9e30c91154f5a9ab2de513e6f4d7936c1eb1a54863514",
    "tools/arena_score.py": "c4859323622794f5f97a131eb32256c905ca2ab6c1adf8ab21568fc2a98f27f3",
    "tools/arena_report_legs.py": "65f4d471e3ad19308a038c4d6563f22322134013f09c0afe2d64c648964a0d8b",
    "tools/arena_stack_v2.py": "95876c94d7e029e3959d8a3aed05ee64f1d461bc002ec62d035e74763587acb3",
    "tools/arena_rules.py": "048dd78901c52e0c14fe9b1611f354c49ac6d0bb37fad8e5be3e27c756e3e2af",
}
FORBIDDEN = tuple(w for w in K.FORBIDDEN if w != "exp011") + ("arena", "meta-capture")
MIN_COVERAGE = 0.85
SEND = 160_000  # DEC-031 Amendment 8 stack (arena_rules.stack_config_v2), passed explicitly
DECLARED_EXIT_LAT_S = 1.9
LOCK_DEFAULT = "/data/mal/hunt-1008/push-1016/hunt30/p29/PRICE_READ.lock"
P29_FILES = ("tools/p29_keeper_rule.py", "tools/p29_keeper_run.py", "tools/p29_keeper_coverage.py")
COVERAGE_AT_HEAD = "docs/research/p29-keeper-absorb/coverage.json"


class Refused(Exception):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_pins() -> dict:
    out = {}
    for rel, want in PINS.items():
        got = sha256(REPO / rel)
        out[rel] = got
        if got != want:
            raise Refused(f"pin_mismatch {rel}: {got} != {want}")
    return out


def hour_name(fn: str) -> str | None:
    m = re.fullmatch(r"trades-(\d{4}-\d{2}-\d{2}T\d{2})(?:\.deduped)?\.jsonl\.zst", fn)
    return m.group(1) if m else None


def block_role(label: str) -> str:
    return next(b for b in BLOCKS if b[0] == label)[1]


def list_hours(label: str) -> dict[str, list[str]]:
    """{hour: [paths]} for the declared block: every hour from the block's start through the last declared day; allowlisted dirs only."""
    _, _, dirs, days = next(b for b in BLOCKS if b[0] == label)
    last = max(days) + "T23"
    out: dict[str, list[str]] = collections.defaultdict(list)
    for d in dirs:
        real = os.path.realpath(d)
        if not real.startswith(K.CLEAN_VIEW + "/"):
            raise Refused(f"outside_clean_view: {d}")
        for q in (d, real):
            for w in FORBIDDEN:
                if w in q.lower():
                    raise Refused(f"forbidden_word:{w}: {q}")
        for fn in sorted(os.listdir(real)):
            h = hour_name(fn)
            if h is None:
                continue
            t = datetime.strptime(h, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
            if t >= K.SEALED_HOURS[0]:
                raise Refused(f"sealed_hour: {h}")  # REFUSED, never skipped: any hour >= 2026-10-02T10 in an allowlisted directory stops the run
            if h > last:
                continue  # an hour past the last declared day of a block is not opened (it is a different part of the same exploration view)
            out[h].append(os.path.join(real, fn))
    dup = [h for h, v in out.items() if len(v) > 1]
    if dup:
        raise Refused(f"duplicate_hour_in_block {label}: {dup[:3]}")
    return {h: v for h, v in sorted(out.items())}


class FileTagger:
    """tagger(block, hour) -> {pool: {(slot, is_buy, sol_lamports, token_raw): (signature, kind)}} for PumpSwap rows whose signature is a keeper fill."""

    def __init__(self, fills: dict, files: dict) -> None:
        self.fills, self.files = fills, files

    def __call__(self, block: str, hour: str) -> dict:
        res: dict = {}
        for path in self.files.get(block, {}).get(hour, ()):
            p = subprocess.Popen(["zstd", "-dc", "-q", path], stdout=subprocess.PIPE, text=True)
            for line in p.stdout:
                i = line.find('"signature":"')
                if i < 0:
                    continue
                sig = line[i + 13:line.find('"', i + 13)]
                kind = self.fills.get(sig)
                if kind is None or '"pumpswap"' not in line:
                    continue
                r = json.loads(line)
                if not r.get("pool") or r.get("side") not in ("buy", "sell"):
                    continue
                res.setdefault(r["pool"], {})[(int(r["slot"]), r["side"] == "buy", int(r["sol_lamports"]), int(r["token_raw"]))] = (sig, kind)
            p.wait()
        return res


def exit_shift(sps: float) -> int:
    """Slots added to the kit's exit decision slot so that the sell lands at trigger + D + ceil(1.9 / sps) (declared): the kit adds D + its own exit lag ceil(0.55 / sps) afterwards."""
    return int(K.F.k_slots_vec(DECLARED_EXIT_LAT_S, sps, "ceil")) - int(K.F.exit_lag_vec(np.array([sps]))[0])


class P29Screen(K.Screen):
    """The kit's Screen at the DEC-031 Am.8 stack (send 160,000, passed explicitly), plus report-only captures in the priced look: per-trip lamports of the four deciding sub-cells keyed
    by (crc32(pool), decision slot) for the placebo pairing; gross edge per filled trip (exit spot / entry spot - 1 on the kit's own landing states, before fees and tx cost); and the
    share of entry and exit landing slots where consecutive pool prints disagree on reserves (no outcome)."""

    def __init__(self, *a, **k) -> None:
        super().__init__(*a, **k)
        self.cfg = {K.RENT_DECIDING: K.screen_cfg(K.RENT_DECIDING, send=SEND), K.RENT_CHARGED: K.screen_cfg(K.RENT_CHARGED, send=SEND)}
        self.disagree = {"entry": collections.Counter(), "exit": collections.Counter()}
        self.gross: list[float] = []
        self.trips: dict = {}
        self.both_fired = 0
        self.exit_trips = 0

    def _price_store(self, s, pool, bi, seg_end, clk) -> None:  # noqa: ANN001
        before = len(self.acc[0]) if self.acc else 0
        chunks = list(s.chunks)
        orig = K.resolve_exit

        def declared_exit(a, pp, sd, spec, rate, sps):  # noqa: ANN001
            """The kit's exit decision, shifted so that the sell lands at trigger slot + D + ceil(1.9 / sps) (declared) instead of the kit's + D + ceil(0.55 / sps): the kit then adds D and its own
            exit lag to the shifted slot. Also counts (report only) trips whose exit slot held both a tp-hit and an sl-hit print (the kit's rule: the earliest print wins)."""
            xs, why, clamped = orig(a, pp, sd, spec, rate, sps)
            jd = int(np.searchsorted(a["sl"], sd, "left"))
            spot0 = pp[max(jd - 1, 0)]
            m = a["sl"] == xs
            self.exit_trips += 1
            if m.any() and spec.tp is not None and spec.sl is not None:
                ph = pp[m]
                self.both_fired += bool((ph >= spec.tp * spot0).any() and (ph <= spec.sl * spot0).any())
            return xs + exit_shift(sps), why, clamped

        K.resolve_exit = declared_exit
        try:
            super()._price_store(s, pool, bi, seg_end, clk)
        finally:
            K.resolve_exit = orig
        if self.count_only or not self.acc or len(self.acc[0]) <= before or not chunks:
            return
        a = {k: np.concatenate([c[k] for c in chunks]) for k in chunks[0]}
        a["ib"] = a["ib"].astype(bool)
        part = self.acc[0][-1]
        name = self.blocks[bi]
        rate_by_T = {int(clk["T"][i]): clk["rate"][i] for i in clk["T"]}
        for sd, xs, T in zip(part["sd"].tolist(), part["xs"].tolist(), part["T"].tolist()):
            sps = 1.0 / rate_by_T[int(T)]
            e_land = sd + K.D + int(K.F.k_slots_vec(K.LAT_DECIDING, sps, "ceil")) + 1
            x_land = xs + K.D + int(K.F.exit_lag_vec(np.array([sps]))[0]) + 1
            for tag, slot in (("entry", e_land), ("exit", x_land)):
                r = landing_disagree(a, slot)
                if r is not None:
                    self.disagree[tag][(name, r)] += 1
        Qe, Be, Qx, Bx = part["Qe"][K.LAT_DECIDING], part["Be"][K.LAT_DECIDING], part["Qx"], part["Bx"]
        ok = (Be > 0) & (Bx > 0) & (Qe > 0)
        self.gross.extend((((Qx / Bx) / (Qe / Be)) - 1.0)[ok].tolist())

    def _cell_result(self, ci, names, ndays):  # noqa: ANN001
        parts = self.acc[ci]
        if parts:
            st = K._cat(parts)
            day = st["T"] // 86400
            for rule, fail in K.SUB_CELLS:
                L = K.legs(st, K.STAKE_SOL * 1e9, K.LAT_DECIDING, rule, self.cfg[K.RENT_DECIDING])
                self.trips[f"{rule}|{fail}"] = {"pool": st["pool"].tolist(), "sd": st["sd"].tolist(), "day": day.tolist(), "blk": st["blk"].tolist(), "xs": st["xs"].tolist(), "lam": np.asarray(L[fail], float).tolist()}
        return super()._cell_result(ci, names, ndays)


def p29_verdict(cell: dict, gross_median: float | None) -> dict:
    """PRICING-DECLARE s5, kills 1-9 on all four sub-cells. Fewer than 5 traded days or fewer than 100 trades is NOT_DECIDABLE (never a KILL). The kit's own kill reasons that refer to its
    four canonical blocks / the explore-0814 group do not apply to P29 and are not used."""
    why, nd = [], []
    for rule, fail in K.SUB_CELLS:
        s, tag = (cell.get("stats") or {}).get(f"{K.LAT_DECIDING}|{rule}|rent0|{fail}"), f"{rule}/{fail}"
        if s is None:
            nd.append(f"{tag}: no trips")
            continue
        if s["days"] < K.MIN_DAYS:
            nd.append(f"{tag}: traded days {s['days']} < {K.MIN_DAYS}")
        if s["n"] < K.MIN_N:
            nd.append(f"{tag}: trades {s['n']} < {K.MIN_N}")
        for ok, msg in ((s["mean"] > 0, "1 mean<=0"), (s["ci_lo_trade"] > 0, "2 trade CI90 lo<=0"), (s["ci_lo"] > 0, "3 date-cluster CI90 lo<=0"), (s["ex_top3"] > 0, "4 ex-top3<=0"),
                        (s["ex_best_day"] > 0, "5 ex-best-day<=0")):
            if not ok:
                why.append(f"{tag}: {msg}")
        if s["days"] >= K.MIN_DAYS and not 2 * s["pos_days"] > s["days"]:
            why.append(f"{tag}: 6 not a strict majority of days positive")
    if gross_median is None or not gross_median > 0.01:
        why.append(f"8 median gross edge {gross_median} <= 1% (spot to spot, before fees and tx cost)")
    wis = cell.get("worst_in_slot") or {}
    measured = bool(wis.get("measured"))
    if measured:
        for rule, fail in K.SUB_CELLS:
            if not wis["sub_cells"][f"{rule}|{fail}"]["lower"]["mean_sol"] > 0:
                why.append(f"{rule}/{fail}: 9 worst-in-slot lower mean<=0")
    if nd:
        status = "NOT_DECIDABLE"
    elif why:
        status = "KILL"
    else:
        status = "NO_KILL_YET (placebo and coverage gates still decide)" if measured else "PROVISIONAL-BLOCKED"
    return {"kill_reasons": why, "not_decidable_reasons": nd, "killed": status == "KILL", "worst_in_slot_measured": measured, "status": status}


def paired_test(tag_trips: dict, ctl_trips: dict, pairs: list) -> dict:
    """Paired tagged-minus-control net SOL per trade on the matched pairs, per deciding sub-cell. Controls may be reused (<= 3x): the bootstrap resamples pairs that share a control as ONE unit
    (cluster bootstrap on the control id, 1,000 draws, seed 1; 5th percentile). pairs: [((event pool, event decision slot), (control pool, control decision slot))]."""
    out = {}
    for key, tt in tag_trips.items():
        ct = ctl_trips.get(key)
        if ct is None:
            out[key] = None
            continue
        tmap = {(p, s): l for p, s, l in zip(tt["pool"], tt["sd"], tt["lam"])}
        cmap = {(p, s): l for p, s, l in zip(ct["pool"], ct["sd"], ct["lam"])}
        d, g = [], []
        for (ep, es), (cp, cs) in pairs:
            a, b = tmap.get((zlib.crc32(ep.encode()), es)), cmap.get((zlib.crc32(cp.encode()), cs))
            if a is not None and b is not None:
                d.append((a - b) / 1e9)
                g.append((cp, cs))
        x = np.asarray(d, float)
        out[key] = {"pairs_priced": int(len(x)), "mean_diff_sol": float(x.mean()) if len(x) else None, "ci_lo_cluster": cluster_boot_lo(d, g)}
    return out


def concurrency(trips: dict, stake_sol: float = 0.25) -> dict:
    """Report only: per UTC day the maximum and the 95th percentile of simultaneously open positions (slot intervals [decision, exit decision] of the primary leg) and the wallet need at stake."""
    t = trips.get("total|flat")
    if not t:
        return {}
    byday: dict = collections.defaultdict(list)
    for sd, xs, day in zip(t["sd"], t["xs"], t["day"]):
        byday[day].append((sd, xs))
    out = {}
    for day, iv in byday.items():
        iv.sort()
        starts = np.array([a for a, _ in iv])
        ends = np.array([b for _, b in iv])
        conc = np.array([int(((starts <= s) & (ends > s)).sum()) for s in starts])
        out[datetime.fromtimestamp(day * 86400, tz=timezone.utc).strftime("%Y-%m-%d")] = {"trades": len(iv), "max_concurrent": int(conc.max()), "p95_concurrent": float(np.percentile(conc, 95)),
                                                                                           "wallet_need_sol_at_max": float(conc.max() * stake_sol)}
    return out


def exp011_only(trips: dict, labels: list) -> dict | None:
    """Report only: the kit's stat() (mean, CI90 lower bounds, ex-top-3, ex-best-day, positive days) on the exp011-0909 trips ALONE, on all four deciding sub-cells."""
    if "exp011-0909" not in labels:
        return None
    bi, out = labels.index("exp011-0909"), {}
    for key, t in trips.items():
        blk = np.asarray(t["blk"])
        m = blk == bi
        out[key] = K.stat(np.asarray(t["lam"], float)[m], np.asarray(t["day"])[m], blk[m], labels) if m.any() else None
    return out


def load_fills(keeper: str) -> dict:
    rows = []
    for l in open(keeper):
        r = json.loads(l)
        if r["bt"] and r["bt"] < KEEPER_CUTOFF:
            rows.append(r)
    return fill_map(rows)


def run_blocks(scr, labels, files, vmap) -> None:
    for bi, lb in enumerate(labels):
        flist = [(datetime.strptime(h, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc), v[0]) for h, v in files[lb].items()]
        scr.run_block(bi, lb, K.iter_hours(flist, lb, "backfill", vmap, 1))


HEADLINE_REPORT_ONLY = ("keeper-tagged SL-cascade absorb; the keeper tag is the detector; whether the tag carries information beyond a fast deep dump is UNTESTED (placebo not decidable)")


def git_out(*a: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True).stdout.strip()


def p29_hashes() -> dict:
    return {rel: sha256(REPO / rel) for rel in P29_FILES}


def price_gates(args) -> dict:
    """Every gate of the pricing look, in this order, BEFORE any price is read. A refusal here reads nothing. The lock is taken last and is never released: a crash after it is NOT_DECIDABLE."""
    head = git_out("rev-parse", "HEAD")
    if os.environ.get("P29_PRICE_AUTH") != head:
        raise Refused("price_not_authorised: P29_PRICE_AUTH must equal the exact HEAD sha (reviewer verdict + QP OK on the code first)")
    if git_out("status", "--porcelain"):
        raise Refused("dirty_working_tree: git status --porcelain must be empty for the pricing look")
    if not args.coverage:
        raise Refused("coverage_required: --coverage must be given in price mode")
    if sha256(Path(args.coverage)) != sha256(REPO / COVERAGE_AT_HEAD):
        raise Refused(f"coverage_mismatch: --coverage must be byte-identical to {COVERAGE_AT_HEAD} at HEAD")
    lock = getattr(args, "lock", None) or LOCK_DEFAULT
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
    except FileExistsError:
        raise Refused(f"price_lock_exists: {lock} (one look only; a crash after the lock is NOT_DECIDABLE, not a re-run)")
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps({"head": head, "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "job": os.environ.get("MISCUSI_JOB_ID", "n/a")}) + "\n")
    return {"head": head, "lock": lock}


def placebo_status(oos_pairs: list, oos_unmatched: int, n_oos: int, bal: dict) -> dict:
    share = oos_unmatched / max(1, n_oos)
    reasons = []
    if len(oos_pairs) < 100:
        reasons.append(f"pairs {len(oos_pairs)} < 100")
    if share > 0.20:
        reasons.append(f"unmatched share {share:.3f} > 0.20")
    if not bal["balanced"]:
        reasons.append(f"balance: max |SMD| {bal['max_abs_smd']} > 0.25")
    decidable = not reasons
    return {"decidable": decidable, "reasons": reasons, "unmatched_share": round(share, 4),
            "branch": "DECIDING conjunct (PASS needs the cluster-bootstrap paired CI lower bound > 0 on both fail models)" if decidable else "REPORT-ONLY (fallback)",
            "headline_label": None if decidable else HEADLINE_REPORT_ONLY}


def run(args) -> dict:
    pins = check_pins()
    gate = price_gates(args) if args.price else None
    hashes = p29_hashes()
    labels = [b[0] for b in BLOCKS if (args.blocks is None or b[0] in args.blocks)]
    if args.price:
        labels = [b[0] for b in BLOCKS if b[1] == "OOS"]
    dropped = []
    if args.coverage:
        cov = json.load(open(args.coverage))
        for lb in list(labels):
            if block_role(lb) == "OOS" and cov["blocks"][lb]["median_ratio_rows"] < MIN_COVERAGE:
                dropped.append(lb)
                labels.remove(lb)
    fills = load_fills(args.keeper)
    files = {lb: list_hours(lb) for lb in labels}
    allowed = {lb: set(next(b for b in BLOCKS if b[0] == lb)[3]) for lb in labels}
    tagger = FileTagger(fills, files)
    vmap = K.VMap(K.check_vmap_paths(K.VMAP_DEFAULT))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    res: dict = {"pins": pins, "p29_module_sha256": hashes, "head": git_out("rev-parse", "HEAD"), "price_gate": gate, "mode": "price" if args.price else "count-only",
                 "dropped_blocks_coverage": dropped, "blocks": {}}
    rule = P29KeeperRule(tagger, "tagged", allowed_days=allowed)
    scr = P29Screen(rule, count_only=not args.price, screen_p=bool(args.price))
    for bi, lb in enumerate(labels):
        n0, t0 = len(rule.events), time.time()
        run_blocks_one(scr, bi, lb, files, vmap)
        res["blocks"][lb] = {"hours": len(files[lb]), "events": len(rule.events) - n0, "wall_s": round(time.time() - t0, 1)}
    ev, cands = rule.events, rule.cands
    pairs, un = match_controls(ev, cands)
    per, cdays = collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter)
    for e in ev:
        c = per[(e["block"], e["day"])]
        c["tagged_events"] += 1
        c["after_price_filters"] += bool(e.get("price_pass"))
        c["after_universe"] += bool(e["pass"])
        if not e["pass"]:
            c["skip_" + str(e["reason"])] += 1
    for c in cands:
        d = cdays[(c["block"], c["day"])]
        d["untagged_bursts"] += 1
        d["after_price_filters"] += bool(c.get("price_pass"))
        d["after_universe"] += bool(c["pass"])
    res["per_block_day"] = {f"{b}|{d}": dict(c) for (b, d), c in sorted(per.items())}
    res["untagged_per_block_day"] = {f"{b}|{d}": dict(c) for (b, d), c in sorted(cdays.items())}
    oos = [e for e in ev if e["pass"] and block_role(e["block"]) == "OOS"]
    oos_days = sorted({f"{e['block']}|{e['day']}" for e in oos})
    oos_pairs = [p for p in pairs if block_role(p[0]["block"]) == "OOS"]
    oos_un = [e for e in un if block_role(e["block"]) == "OOS"]
    bal = balance(oos_pairs)
    reuse = collections.Counter((c["pool"], c["slot"]) for _, c in oos_pairs)
    res["matcher_v2"] = {"pairs_all_blocks": len(pairs), "unmatched_all_blocks": len(un), "oos_pairs": len(oos_pairs), "oos_unmatched": len(oos_un), "oos_distinct_controls": len(reuse),
                         "max_control_reuse": max(reuse.values()) if reuse else 0, "balance": bal}
    res["placebo"] = placebo_status(oos_pairs, len(oos_un), len(oos), bal)
    res["rule_counters"] = dict(rule.counters)
    res["kit_counters"] = {k: int(v) for k, v in scr.counters.items() if k.startswith("refused") or k in ("triggers_raw", "overlap_dropped")}
    res["kit_validated_triggers_per_day"] = {scr._d(d): int(n) for d, n in sorted(scr.cnt[0].items())}
    res["oos_events_after_universe"], res["oos_event_days"] = len(oos), len(oos_days)
    stops = []
    if len(oos_days) < 5 or len(oos) < 100:
        stops.append(f"NOT_DECIDABLE: OOS events after universe {len(oos)} (need >= 100) over {len(oos_days)} days (need >= 5)")
    res["stops"] = stops
    json.dump({"pairs": [[[e["pool"], e["slot"]], [c["pool"], c["slot"]]] for e, c in oos_pairs]}, open(out / "controls.json", "w"))
    json.dump(ev, open(out / "events.json", "w"))
    json.dump(cands, open(out / "cands.json", "w"))
    if args.price:
        cells = scr.results()
        res["priced"] = cells
        res["gross_edge_median"] = statistics.median(scr.gross) if scr.gross else None
        res["p29_verdict"] = p29_verdict(cells.get("p29", {}), res["gross_edge_median"])
        res["exp011_only"] = exp011_only(scr.trips, labels)  # report only, beside the pooled result in the headline
        res["exit_slots_both_tp_and_sl_hit"] = {"trips": scr.exit_trips, "both": scr.both_fired}  # report only (kit rule: the earliest print wins)
        res["concurrency_report"] = concurrency(scr.trips)
        res["landing_disagreement"] = {t: {b: {"disagree": cnt.get((b, True), 0), "agree": cnt.get((b, False), 0)} for b in sorted({k[0] for k in cnt})} for t, cnt in scr.disagree.items()}
        rule_c = P29KeeperRule(tagger, "control", control_keys={(c["pool"], c["slot"]) for _, c in oos_pairs}, allowed_days=allowed)
        scr_c = P29Screen(rule_c, count_only=False, screen_p=False)
        for bi, lb in enumerate(labels):
            run_blocks_one(scr_c, bi, lb, files, vmap)
        scr_c.results()
        res["placebo_paired"] = paired_test(scr.trips, scr_c.trips, [((e["pool"], e["decision_slot"]), (c["pool"], c["decision_slot"])) for e, c in oos_pairs])
        res["report_only_legs"] = {}
        for mode in ("stress", "best"):  # report only: never decides, never rescues a kill; a stress sign flip is stated in the headline
            r_m = P29KeeperRule(tagger, "tagged", allowed_days=allowed, entry_mode=mode)
            s_m = P29Screen(r_m, count_only=False, screen_p=False)
            for bi, lb in enumerate(labels):
                run_blocks_one(s_m, bi, lb, files, vmap)
            cm = s_m.results().get("p29", {})
            res["report_only_legs"][mode] = {"stats": {k: v for k, v in (cm.get("stats") or {}).items() if k.startswith(f"{K.LAT_DECIDING}|") and "rent0" in k}, "gross_edge_median": statistics.median(s_m.gross) if s_m.gross else None}
    json.dump(res, open(out / ("result_price.json" if args.price else "counts_pass.json"), "w"), indent=1, default=float)
    return res


def run_blocks_one(scr, bi, lb, files, vmap) -> None:
    flist = [(datetime.strptime(h, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc), v[0]) for h, v in files[lb].items()]
    scr.run_block(bi, lb, K.iter_hours(flist, lb, "backfill", vmap, 1))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keeper", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--blocks", nargs="*")
    ap.add_argument("--coverage")
    ap.add_argument("--price", action="store_true")
    ap.add_argument("--lock", default=LOCK_DEFAULT)
    args = ap.parse_args(argv)
    try:
        run(args)
    except Refused as e:
        print("REFUSED:", e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
