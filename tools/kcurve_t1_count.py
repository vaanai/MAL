#!/usr/bin/env python3
"""kcurve-boost T1: structure-only count of synthetic-completer concentration on released October hours (domain D0).

PAPER ONLY. No price, no outcome, no RPC, no Helius. Exploration counts on released hours: they can only KILL or license
the next steps of docs/research/kcurve-boost/PREREG.md section 9 ("released hours never confirm").

Authority: quant-proof ruling /data/mal/hunt-1008/ops/manager18/QP-RULING-1030-kcurve-prereg.md (sha256 41dc2fe3...),
Q6 conditions T1-a .. T1-k; PREREG section 1.3. Every input goes through tools.mal_catalog_october role `oct-structure-d0`:
`check_read` (grant) -> `check_paths` (inside check_read) -> `guarded_rows` / `mask_row` on EVERY parsed line before any
other use. No other reader. The module's own `_lines` reads the hour files.

    python -m tools.kcurve_t1_count --out DIR --prereg-sha256 HEX --declare-notebook NOTEBOOK_ID

T1-c source precheck (printed first): post_complete_buy rows per hour per dataset. forward-1002 (job #382 at 2bd45f1)
predates the post_complete_buy decoder (c080cbb7, #467) and has no events/ stream; forward-1002ev (job #433 at
153f1a02) writes post_complete_buy rows to events/. The deciding source is forward-1002ev, window
[2026-10-09T00, 2026-10-16T01) (its DATASET_START; never a walk-2 hour). forward-1002's hours
[2026-10-08T16, 2026-10-09T00) are counted in the precheck only.

Definitions (fixed before the job is submitted):
  completion   a `complete` row in migrations/ of the window, slot >= 454,596,459 (pump redeploy 2026-10-08T16:20:17Z).
  completer    the `trader` of the pump_bonding `trade` row whose signature equals the complete row's signature
               (the D0 mask keeps `trader` on trade rows only).
  unclassified a completion whose tx rows are missing (QP T1-d, #1055 R1): no such trade row, OR its complete-row
               hour has no readable events/ or trades/ file (missing or refused by the guard). Counted per UTC day; a
               day above 1% unclassified, or with a missing or refused migrations/ hour, is NOT_DECIDABLE and its
               completions leave the pooled counts. Missing or refused hours are printed per kind.
  synthetic    a classified completion with a `post_complete_buy` row of the same mint whose signature equals the
               complete signature or the mint's migrate signature (EXP-024 Am.4 B1 semantics). The tape can only mark a completion
               synthetic, never settle non-synthetic (EXP-024 Am.4 B2); disclosed in the output.
  organic      (report-only) a create row of the mint read only inside the registered hours (creates/ of forward-1002
               [2026-10-08T16, 2026-10-09T00) and of forward-1002ev's window) with complete event_ts minus create event_ts >= 60 s and a different signature.
  V0 band      (report-only) V0 in [17.5e9, 17.7e9]: `virtual_quote_reserves` on an s0-type row signed by the mint's
               migrate tx (the mask keeps it only there, `_S0_FIELDS`).
  slot gap     migrate slot minus complete slot (D0 slot timing).

Verdict (T1-h), pooled over the decidable days (not the whole window):
  NOT_DECIDABLE  the precheck finds no post_complete_buy row in the deciding source, a read is refused, no decidable day,
                 or fewer than 30 synthetic completions.
  KILL           the top-5 completers hold >= 80% of synthetic completions (a held seat).
  PASS           otherwise (licenses PREREG section 9 only; never a profit claim).
  Report-only:   the synthetic share over organic completions with V0 in band (not blind: EXP-024 Am.4 item 1); `quote_in`
                 "not measured under the D0 mask".
Exit codes: 0 KILL or PASS written; 3 NOT_DECIDABLE written; 2 refused: arguments or order before any read, or a guard
refusal (OctoberRefused) at any point, e.g. two migrate signatures for one mint.

T1-f/T1-g: outputs are aggregates only (per-UTC-day counts, distinct completers, top-5 share, slot-gap histogram);
no per-pool or per-mint table is written, and nothing is joined to any Q50, s1, HV4, arena, shadow, canary, A3-guard,
BOOST or H5 record, or to any outcome. No later tx of any completer is read.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import mal_catalog as mc  # noqa: E402
from tools import mal_catalog_october as oc  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
PREREG = REPO_ROOT / "docs" / "research" / "kcurve-boost" / "PREREG.md"
ROLE = oc.ROLE_OCT_D0
DATASET = "forward-1002ev"
START, END = "2026-10-09T00", "2026-10-16T01"
PRECHECK = (("forward-1002", "2026-10-08T16", "2026-10-09T00"), ("forward-1002ev", START, END))
CREATES_EARLY = ("forward-1002", "2026-10-08T16", "2026-10-09T00")  # registered T1-b hours only
REDEPLOY_SLOT = 454_596_459
V_LO, V_HI = 17_500_000_000, 17_700_000_000
ORGANIC_S = 60
TOP_N = 5
KILL_SHARE = 0.80
MIN_SYNTHETIC = 30
MAX_UNCLASSIFIED_DAY = 0.01
BONDING = oc.BONDING_VENUE
# The columns this read names (all on the D0 allowlist; check_read refuses otherwise).
COLUMNS = ("type", "venue", "mint", "trader", "slot", "signature", "event_index", "event_ts", "v0", "synthetic_class",
           "post_complete_buy", "slot_gap", "n_completes", "t_create", "t_complete", "quote_mint")
FORBIDDEN_OUT = ("probe", "shadow", "canary", "/data/mal/blocks", "/data/mal/arena", "meta-capture", "sealed")


class Refused(Exception):
    pass


def hours(start: str, end: str) -> list[str]:
    s = datetime.strptime(start, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    e = datetime.strptime(end, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    out = []
    while s < e:
        out.append(s.strftime("%Y-%m-%dT%H"))
        s += timedelta(hours=1)
    return out


def hour_paths(dataset: str, kind: str, start: str, end: str) -> list[Path]:
    """Build the candidate hour-file paths from the hour range FIRST (T1-b), then keep the ones that exist (a stat, no
    directory listing). Never a walk-2 hour: the range end is clipped to oc.FORWARD_END_EXCLUSIVE."""
    end = min(end, oc.FORWARD_END_EXCLUSIVE)
    start = max(start, oc.DATASET_START[dataset])
    out = []
    for h in hours(start, end):
        for suf in (".jsonl.zst", ".jsonl"):
            p = oc.BLOCKS_ROOT / dataset / kind / f"{kind}-{h}{suf}"
            if p.exists():
                out.append(p)
                break
    return out


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def granted(blocks, dataset: str, start: str, end: str, paths: list[Path], log) -> list[Path]:
    """check_read per file (T1-b: a file that fails is skipped before it is opened, and the reason is logged)."""
    ok_paths = []
    for p in paths:
        ok, reasons = oc.check_read(blocks, ROLE, dataset, start, end, COLUMNS, paths=[p])
        if ok:
            ok_paths.append(p)
        else:
            log(f"SKIP_REFUSED {p.parent.name}/{p.name}: {'; '.join(reasons)}")
    return ok_paths


def hour_of(p: Path) -> str:
    return p.name.split("-", 1)[1].split(".")[0]


def rows_of(paths: Iterable[Path], migrate_sigs: dict | None, skipped: dict) -> Iterable[dict[str, Any]]:
    for p in paths:
        raw = (json.loads(line) for line in oc._lines(p) if line.strip())
        yield from oc.guarded_rows(ROLE, raw, migrate_sigs=migrate_sigs, skipped=skipped)


def utc_day(ts: Any) -> str | None:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d") if isinstance(ts, (int, float)) else None


def run(out: Path, prereg_sha: str, notebook: str, log=print) -> int:
    # ---- order and arguments (T1-j), before any read
    if not notebook or not re.fullmatch(r"n_[A-Za-z0-9_-]+", notebook.strip()):
        raise Refused("T1-j: --declare-notebook must be the DECLARE notebook id (n_...); T1 runs only after it")
    if not PREREG.exists() or sha256_file(PREREG) != prereg_sha.lower():
        raise Refused(f"T1-j: {PREREG} is missing or its sha256 is not {prereg_sha}")
    so = str(out.resolve())
    if any(w in so for w in FORBIDDEN_OUT):
        raise Refused(f"--out {so} contains a forbidden path part")
    log(json.dumps({"t1": "kcurve-boost T1", "resolved_gates": oc.resolved_gates(), "role": ROLE, "dataset": DATASET,
                    "window": [START, END], "prereg_sha256": prereg_sha, "declare_notebook": notebook}))
    blocks = mc.parse_ledger((REPO_ROOT / "docs" / "HOLDOUT_LEDGER.md").read_text(encoding="utf-8"))
    skipped: dict[str, int] = {}

    # ---- T1-c source precheck: post_complete_buy rows per hour per dataset, printed first. Counts only; no slot floor is
    # applied here (a post_complete_buy row cannot predate the 2026-10-08T16:20:17Z redeploy).
    pre: dict[str, dict[str, int]] = {}
    inputs: dict[str, str] = {}
    for ds, s, e in PRECHECK:
        per_hour: dict[str, int] = collections.Counter()
        for kind in ("events", "migrations"):
            paths = granted(blocks, ds, s, e, hour_paths(ds, kind, s, e), log)
            for p in paths:
                inputs[f"{ds}/{kind}/{p.name}"] = sha256_file(p)
                hr = p.name.split("-", 1)[1].split(".")[0]
                n = sum(1 for r in rows_of([p], None, skipped) if r.get("type") == "post_complete_buy")
                per_hour[hr] += n
        pre[ds] = dict(sorted(per_hour.items()))
        log(json.dumps({"precheck": ds, "window": [s, e], "post_complete_buy_rows_total": sum(per_hour.values()),
                        "hours_with_rows": sum(1 for v in per_hour.values() if v), "per_hour": pre[ds]}))
    if sum(pre.get(DATASET, {}).values()) == 0:
        return finish(out, {"verdict": "NOT_DECIDABLE", "why": f"no post_complete_buy row in {DATASET} (precheck)"},
                      pre, inputs, skipped, log)

    # ---- migrations/: complete rows, migrate signatures
    mig_paths = granted(blocks, DATASET, START, END, hour_paths(DATASET, "migrations", START, END), log)
    mig_rows: list[dict] = []
    comp: dict[str, dict] = {}  # mint -> complete identity (kept in memory only; never written)
    for p in mig_paths:
        for r in rows_of([p], None, skipped):
            mig_rows.append(r)
            if r.get("type") == "complete" and isinstance(r.get("slot"), int) and r["slot"] >= REDEPLOY_SLOT:
                comp.setdefault(r["mint"], {"sig": r.get("signature"), "slot": r["slot"], "ts": r.get("event_ts"), "hour": hour_of(p)})
    msigs = oc.migrate_sigs_from(mig_rows)
    mig_slot = {r["mint"]: r["slot"] for r in mig_rows if r.get("type") in oc._MIGRATE_SIG_TYPES and r.get("mint") in msigs
                and r.get("signature") == msigs[r["mint"]]}
    v0: dict[str, float] = {}
    for r in rows_of(mig_paths, msigs, skipped):  # second pass with the s0 rule: V only on the migrate-tx row
        if "virtual_quote_reserves" in r and r.get("mint") in msigs:
            v0.setdefault(r["mint"], float(r["virtual_quote_reserves"]))

    # ---- events/: post_complete_buy (synthetic class), s0 V
    ev_paths = granted(blocks, DATASET, START, END, hour_paths(DATASET, "events", START, END), log)
    pcb: set[tuple] = set()  # (mint, signature); mint None when the row carries none
    for r in rows_of(ev_paths, msigs, skipped):
        if r.get("type") == "post_complete_buy" and r.get("signature"):
            pcb.add((r.get("mint"), r["signature"]))
        if "virtual_quote_reserves" in r and r.get("mint") in msigs:
            v0.setdefault(r["mint"], float(r["virtual_quote_reserves"]))

    # ---- creates/ (report-only organic)
    created: dict[str, tuple] = {}
    for ds, s, e in (CREATES_EARLY, (DATASET, START, END)):
        cr_paths = granted(blocks, ds, s, e, hour_paths(ds, "creates", s, e), log)
        for p in cr_paths:
            inputs[f"{ds}/creates/{p.name}"] = sha256_file(p)
        for r in rows_of(cr_paths, None, skipped):
            if r.get("type") == "create" and r.get("mint"):
                created.setdefault(r["mint"], (r.get("event_ts"), r.get("signature")))

    # ---- trades/: the completing trade row's trader (identities only through the mask)
    want = {c["sig"]: m for m, c in comp.items() if c["sig"]}
    completer: dict[str, str] = {}
    quote: dict[str, Any] = {}
    tr_paths = granted(blocks, DATASET, START, END, hour_paths(DATASET, "trades", START, END), log)
    for p in tr_paths + mig_paths + ev_paths:
        inputs[f"{DATASET}/{p.parent.name}/{p.name}"] = sha256_file(p)
    expected = set(hours(START, END))
    readable = {"migrations": {hour_of(p) for p in mig_paths}, "events": {hour_of(p) for p in ev_paths},
                "trades": {hour_of(p) for p in tr_paths}}
    missing = {k: sorted(expected - v) for k, v in readable.items()}
    log(json.dumps({"hours_missing_or_refused": missing}))
    bad_mig_days = {h[:10] for h in missing["migrations"]}
    for r in rows_of(tr_paths, None, skipped):
        sig = r.get("signature")
        if sig in want and r.get("type") == "trade" and r.get("venue") == BONDING and r.get("trader"):
            m = want[sig]
            if r.get("mint") in (None, m):
                completer.setdefault(m, r["trader"])
                quote.setdefault(m, r.get("quote_mint"))

    # ---- aggregates
    days: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    syn_by_day: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)  # day -> completer counts
    gap = collections.Counter()
    for m, c in comp.items():
        d = utc_day(c["ts"]) or "unknown"
        D = days[d]
        D["completions"] += 1
        if m in mig_slot:
            gap[min(max(mig_slot[m] - c["slot"], -1), 10)] += 1
        if c["hour"] not in readable["events"] or c["hour"] not in readable["trades"]:
            D["unclassified"] += 1
            D["unclassified_hour_missing"] += 1
            continue
        if m not in completer:
            D["unclassified"] += 1
            D["unclassified_no_trade_row"] += 1
            continue
        D["classified"] += 1
        sigs = {c["sig"]} | ({msigs[m]} if m in msigs else set())
        syn = any((m, s) in pcb or (None, s) in pcb for s in sigs)
        cr = created.get(m)
        org = bool(cr and cr[0] is not None and c["ts"] is not None and c["ts"] - cr[0] >= ORGANIC_S and cr[1] != c["sig"])
        vb = m in v0 and V_LO <= v0[m] <= V_HI
        D["synthetic"] += syn
        D["create_found"] += bool(cr)
        D["organic"] += org
        D["organic_v0_band"] += org and vb
        D["organic_v0_band_synthetic"] += org and vb and syn
        D["v0_unknown"] += m not in v0
        D["wsol"] += quote.get(m) == "So11111111111111111111111111111111111111112"
        if syn:
            syn_by_day[d][completer[m]] += 1
    decidable = sorted(d for d, D in days.items() if D["completions"] and D["unclassified"] / D["completions"] <= MAX_UNCLASSIFIED_DAY
                       and d not in bad_mig_days)
    pooled = collections.Counter()
    for d in decidable:
        pooled.update(syn_by_day[d])
    n_syn = sum(pooled.values())
    top = sum(v for _, v in pooled.most_common(TOP_N))
    share = top / n_syn if n_syn else None
    org_band = sum(days[d]["organic_v0_band"] for d in decidable)
    org_band_syn = sum(days[d]["organic_v0_band_synthetic"] for d in decidable)
    res = {"decidable_days": decidable, "not_decidable_days": sorted(set(days) - set(decidable)),
           "synthetic_completions": n_syn, "distinct_completers": len(pooled), "top5_count": top, "top5_share": share,
           "report_only": {"synthetic_share_over_organic_v0_band": (org_band_syn / org_band) if org_band else None,
                           "organic_v0_band_completions": org_band, "quote_in": "not measured under the D0 mask",
                           "synthetic_share_not_blind": "EXP-024 Am.4 item 1 records 0.197, 0.316, 0.368 (#1055 R3)",
                           "tape_marks_synthetic_only": "EXP-024 Am.4 B2: a partial decode loss is not flagged",
                           "mayhem": "not excludable under the D0 mask (create rows lose is_mayhem_mode); only the V0 band narrows the denominator",
                           "window_end": "a completion in hour 2026-10-16T00 whose migrate row falls in walk-2 hour T01 has no migrate signature here"},
           "hours_missing_or_refused": missing,
           "per_day": {d: dict(days[d]) for d in sorted(days)}, "slot_gap_hist": {str(k): v for k, v in sorted(gap.items())}}
    if not decidable:
        res.update(verdict="NOT_DECIDABLE", why="no decidable UTC day (every day has > 1% unclassified or a missing or refused migrations/ hour)")
    elif n_syn < MIN_SYNTHETIC:
        res.update(verdict="NOT_DECIDABLE", why=f"{n_syn} synthetic completions < {MIN_SYNTHETIC}")
    else:
        res.update(verdict="KILL" if share >= KILL_SHARE else "PASS",
                   why=f"top-{TOP_N} completers hold {share:.4f} of {n_syn} synthetic completions (kill at >= {KILL_SHARE})")
    return finish(out, res, pre, inputs, skipped, log)


def finish(out: Path, res: dict, pre: dict, inputs: dict, skipped: dict, log) -> int:
    out.mkdir(parents=True, exist_ok=True)
    doc = {"schema": "kcurve_t1_count_v1", "stamp": "EXPLORATION, released D0 hours: counts only; never gate evidence; no book has cleared the promotion gate",
           **res, "precheck": pre, "inputs_sha256": dict(sorted(inputs.items())), "guard_skips": skipped}
    (out / "t1_result.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    log(json.dumps({"verdict": res["verdict"], "why": res.get("why"), "inputs": len(inputs), "guard_skips": skipped}))
    return 3 if res["verdict"] == "NOT_DECIDABLE" else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--prereg-sha256", required=True)
    ap.add_argument("--declare-notebook", required=True)
    a = ap.parse_args(argv)
    try:
        return run(a.out, a.prereg_sha256, a.declare_notebook)
    except (Refused, oc.OctoberRefused) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
