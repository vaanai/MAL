#!/usr/bin/env python3
"""causal_harness: causal-only screen decisions by construction, and a lookahead test for existing batch screens (P24 fast track). OFFLINE. Opens nothing by itself.

Nothing here prices a trade, reads an outcome or says any rule makes money. The caller passes the rows (structure fields only); this module only decides WHEN a decision
function may see WHICH rows, and proves that a screen's decisions do not change when later rows change.

WHY
  The DBC m10 line (2026-10-24) had a lookahead: a screen decision used information that only existed after its decision instant. It was caught by quant-proof after the
  screen, the holdout read and the forward build had been done on it. A generic check at authoring time catches that class in minutes, for any screen, before any QP round.

THREE ENTRY POINTS
  1. `run_stream(decide, rows)` (causal by construction). Rows are ordered by (t_ms, seq). `decide(view, row)` is called once per row, in order. `view` is a CausalView that
     holds only the rows already delivered, projected to `allowed_fields`: the function has no reference to a later row, so it cannot read one. A decision's
     `decision_ms` is the time of the row being processed (never earlier). A function that keeps its own state across calls is still causal: it only ever saw earlier rows.
  2. `lookahead_check(screen, rows)` (for an existing BATCH screen, any implementation: pandas, numpy, DuckDB). `screen(rows) -> decisions`, each a dict with
     `decision_ms`. For each probe instant c (by default the decision instants themselves, sampled with a fixed seed), the screen is re-run
       (a) TRUNCATED: on the rows with t_ms <= c only, and
       (b) PERTURBED: on all rows, with every numeric non-key field of the rows with t_ms > c scaled by a seeded factor,
     and its decisions with decision_ms <= c must be identical (canonical JSON) to the full run's decisions with decision_ms <= c. Any difference means a decision at or
     before c depends on a row after c: LOOKAHEAD. This catches a full-series quantile or z-score, an end-of-window aggregate, a `shift(-1)` / next-row peek, a decision
     stamped earlier than the data it used, and a label column joined in from the future (the perturbation changes it).
  3. `stream_batch_equivalence(decide, screen, rows)`: md5 of the canonical decision list from (1) and from (2)'s full run. Equal md5 = the batch screen is
     decision-equivalent to the causal stream on these rows (the lab's md5 decision-equivalence form).

  `forbidden_fields`: row keys that look like outcomes (pnl, exit, ret_, future, label, ...). `check_fields` refuses rows that carry one unless the caller lists it in
  `allowed_fields` with a written reason; the stream view never shows a field outside `allowed_fields`.

CLI (JSONL rows in, JSON report out; exit 0 = CAUSAL_OK, 3 = LOOKAHEAD, 2 = usage / refused)
    python -m tools.causal_harness check --screen pkg.mod:fn --rows rows.jsonl --out report.json [--probes 64] [--seed 1] [--time-key t_ms]
    python -m tools.causal_harness equiv --screen pkg.mod:fn --decide pkg.mod:fn --rows rows.jsonl --out report.json

  Rows are read with plain json (one object per line). The report carries the md5 of the full decision list, the probes, and the first divergence (decision keys and
  instants only, never a price or a P&L field: `report_fields`).
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python tools/causal_harness.py`
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import hashlib
import importlib
import json
import random
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

SCHEMA = "causal_harness_v1"
TIME_KEY = "t_ms"
SEQ_KEY = "_seq"
FORBIDDEN_SUBSTRINGS = ("pnl", "profit", "exit", "outcome", "future", "label", "ret_", "return", "max_after", "min_after", "fwd_", "next_", "realized", "settle")
DEFAULT_REPORT_FIELDS = ("decision_ms", "key", "pool", "mint", "cell", "side", "reason")


class LookaheadError(RuntimeError):
    """A decision function asked for something it may not see (a field outside `allowed_fields`, or a row it has not been given)."""


class FieldRefused(ValueError):
    """A row carries an outcome-looking field that the caller did not allow."""


# ---------------------------------------------------------------- ordering and canonical form


def order_rows(rows: Iterable[Mapping[str, Any]], time_key: str = TIME_KEY) -> list[dict]:
    """Rows sorted by (time, original position). A stable, total order: rows at the same ms keep the order they came in. Each copy gets `_seq` (its original index)."""
    out = []
    for i, r in enumerate(rows):
        if time_key not in r:
            raise ValueError(f"row {i} has no {time_key!r}")
        t = r[time_key]
        if not isinstance(t, int) or isinstance(t, bool):
            raise ValueError(f"row {i}: {time_key} must be an int (ms), got {type(t).__name__}")
        d = dict(r)
        d[SEQ_KEY] = i
        out.append(d)
    out.sort(key=lambda d: (d[time_key], d[SEQ_KEY]))
    return out


def canon(decisions: Iterable[Mapping[str, Any]]) -> list[str]:
    """Canonical text of a decision list: each decision as sorted-key compact JSON, the list sorted by (decision_ms, text). Order inside one ms does not matter."""
    out = []
    for d in decisions:
        if "decision_ms" not in d:
            raise ValueError("a decision has no decision_ms")
        out.append((int(d["decision_ms"]), json.dumps(d, sort_keys=True, separators=(",", ":"), default=str)))
    out.sort()
    return [s for _, s in out]


def md5_decisions(decisions: Iterable[Mapping[str, Any]]) -> str:
    return hashlib.md5("\n".join(canon(decisions)).encode()).hexdigest()


def check_fields(rows: Sequence[Mapping[str, Any]], allowed_fields: Iterable[str] | None = None,
                 forbidden_substrings: Sequence[str] = FORBIDDEN_SUBSTRINGS) -> list[str]:
    """Refuse rows that carry an outcome-looking field not explicitly allowed. Returns the sorted field set. `allowed_fields` None = every field not forbidden."""
    allow = set(allowed_fields) if allowed_fields is not None else None
    seen: set[str] = set()
    for r in rows:
        seen.update(r.keys())
    seen.discard(SEQ_KEY)
    bad = sorted(k for k in seen if any(s in k.lower() for s in forbidden_substrings) and not (allow is not None and k in allow))
    if bad:
        raise FieldRefused(f"rows carry outcome-looking fields {bad}; drop them, or list each in allowed_fields with a written reason")
    return sorted(seen)


# ---------------------------------------------------------------- 1. stream mode


class CausalView:
    """What a stream decision function is shown: the rows delivered so far (t <= now; at equal t, only rows earlier in the order), projected to the allowed fields.
    Read-only; it has no reference to any later row."""

    __slots__ = ("_rows", "_allowed", "_time_key", "now_ms")

    def __init__(self, allowed: frozenset[str] | None, time_key: str) -> None:
        self._rows: list[dict] = []
        self._allowed = allowed
        self._time_key = time_key
        self.now_ms = None

    def _push(self, row: Mapping[str, Any]) -> dict:
        if self._allowed is None:
            p = {k: v for k, v in row.items() if k != SEQ_KEY}
        else:
            p = {k: row[k] for k in self._allowed if k in row}
            p[self._time_key] = row[self._time_key]
        self._rows.append(p)
        self.now_ms = row[self._time_key]
        return p

    def __len__(self) -> int:
        return len(self._rows)

    def rows(self, since_ms: int | None = None) -> tuple[dict, ...]:
        """Copies of the rows delivered so far, optionally only those with t >= since_ms."""
        tk = self._time_key
        return tuple(dict(r) for r in self._rows if since_ms is None or r[tk] >= since_ms)

    def last(self, n: int = 1) -> tuple[dict, ...]:
        return tuple(dict(r) for r in self._rows[-n:]) if n > 0 else ()

    def where(self, pred: Callable[[Mapping[str, Any]], bool]) -> tuple[dict, ...]:
        return tuple(dict(r) for r in self._rows if pred(r))

    def at(self, t_ms: int) -> tuple[dict, ...]:
        if self.now_ms is None or t_ms > self.now_ms:
            raise LookaheadError(f"asked for t={t_ms} with now={self.now_ms}")
        tk = self._time_key
        return tuple(dict(r) for r in self._rows if r[tk] == t_ms)


@dataclass
class StreamResult:
    decisions: list[dict]
    md5: str
    rows_seen: int


def run_stream(decide: Callable[[CausalView, Mapping[str, Any]], Any], rows: Iterable[Mapping[str, Any]], *, allowed_fields: Iterable[str] | None = None,
               time_key: str = TIME_KEY, forbidden_substrings: Sequence[str] = FORBIDDEN_SUBSTRINGS) -> StreamResult:
    """Feed rows one at a time in (t, seq) order. `decide(view, row)` returns None, a decision dict, or a list of them. The view already includes `row`.
    A returned decision gets decision_ms = the row's time unless it sets a LATER one; an earlier one is a LookaheadError (a decision cannot predate its data)."""
    ordered = order_rows(rows, time_key)
    check_fields(ordered, allowed_fields, forbidden_substrings)
    allow = frozenset(allowed_fields) if allowed_fields is not None else None
    view = CausalView(allow, time_key)
    out: list[dict] = []
    for r in ordered:
        shown = view._push(r)
        got = decide(view, dict(shown))
        if got is None:
            continue
        for d in (got if isinstance(got, list) else [got]):
            d = dict(d)
            dm = d.setdefault("decision_ms", r[time_key])
            if int(dm) < r[time_key]:
                raise LookaheadError(f"decision_ms {dm} is before the row it was decided on (t={r[time_key]})")
            out.append(d)
    return StreamResult(out, md5_decisions(out), len(view))


# ---------------------------------------------------------------- 2. lookahead check for batch screens


def _perturb(rows: list[dict], cutoff: int, rng: random.Random, time_key: str, keep: frozenset[str]) -> list[dict]:
    out = []
    for r in rows:
        if r[time_key] <= cutoff:
            out.append(r)
            continue
        d = dict(r)
        for k, v in r.items():
            if k in keep or k == time_key or k == SEQ_KEY or isinstance(v, bool):
                continue
            if isinstance(v, int):
                d[k] = int(round(v * rng.uniform(0.5, 1.5))) + rng.choice((-1, 1))
            elif isinstance(v, float):
                d[k] = v * rng.uniform(0.5, 1.5) + rng.choice((-1e-3, 1e-3))
        out.append(d)
    return out


def _strip_seq(rows: list[dict]) -> list[dict]:
    return [{k: v for k, v in r.items() if k != SEQ_KEY} for r in rows]


def _upto(decisions: Iterable[Mapping[str, Any]], c: int) -> list[str]:
    return canon(d for d in decisions if int(d["decision_ms"]) <= c)


@dataclass
class Probe:
    cutoff_ms: int
    mode: str  # truncate | perturb
    ok: bool
    n_upto: int
    only_full: list[str] = field(default_factory=list)  # decisions in the full run at <= c that the probe run lost or changed
    only_probe: list[str] = field(default_factory=list)


@dataclass
class CheckResult:
    verdict: str  # CAUSAL_OK | LOOKAHEAD | NO_DECISIONS
    md5_full: str
    n_rows: int
    n_decisions: int
    probes: list[Probe]
    first_failure: Probe | None

    def as_dict(self, report_fields: Sequence[str] = DEFAULT_REPORT_FIELDS) -> dict:
        def slim(texts: list[str]) -> list[dict]:
            return [{k: v for k, v in json.loads(t).items() if k in report_fields} for t in texts[:5]]
        ff = None
        if self.first_failure is not None:
            p = self.first_failure
            ff = {"cutoff_ms": p.cutoff_ms, "mode": p.mode, "only_full": slim(p.only_full), "only_probe": slim(p.only_probe),
                  "n_only_full": len(p.only_full), "n_only_probe": len(p.only_probe)}
        return {"schema": SCHEMA, "verdict": self.verdict, "md5_full": self.md5_full, "n_rows": self.n_rows, "n_decisions": self.n_decisions,
                "probes": [{"cutoff_ms": p.cutoff_ms, "mode": p.mode, "ok": p.ok, "n_upto": p.n_upto} for p in self.probes], "first_failure": ff}


def choose_cutoffs(decisions: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]], n: int, seed: int, time_key: str = TIME_KEY) -> list[int]:
    """Probe instants: every distinct decision instant if there are at most n, else a seeded sample of n of them; plus the instant just before each sampled one
    (so a decision stamped exactly at a row is probed with that row present and absent). Never the last row's time (truncation there is a no-op)."""
    times = sorted({int(d["decision_ms"]) for d in decisions})
    if len(times) > n:
        times = sorted(random.Random(seed).sample(times, n))
    last = max(r[time_key] for r in rows) if rows else 0
    cs = set()
    for t in times:
        if t < last:
            cs.add(t)
        if t - 1 < last:
            cs.add(t - 1)
    return sorted(cs)


def lookahead_check(screen: Callable[[list[dict]], Iterable[Mapping[str, Any]]], rows: Iterable[Mapping[str, Any]], *, probes: int = 64, seed: int = 1,
                    time_key: str = TIME_KEY, perturb: bool = True, keep_fields: Iterable[str] = (), cutoffs: Sequence[int] | None = None,
                    stop_at_first: bool = False) -> CheckResult:
    """Run `screen` on all rows, then on truncated and perturbed copies at each probe instant; decisions at or before the instant must not change.
    `keep_fields`: fields the perturbation leaves alone (ids, mints, pools: strings are never perturbed anyway; list int ids here, e.g. slot)."""
    ordered = order_rows(rows, time_key)
    base = _strip_seq(ordered)
    full = [dict(d) for d in screen([dict(r) for r in base])]
    md5_full = md5_decisions(full)
    if not full:
        return CheckResult("NO_DECISIONS", md5_full, len(base), 0, [], None)
    cs = list(cutoffs) if cutoffs is not None else choose_cutoffs(full, ordered, probes, seed, time_key)
    keep = frozenset(keep_fields)
    rng = random.Random(seed)
    out: list[Probe] = []
    first: Probe | None = None
    for c in cs:
        want = _upto(full, c)
        runs = [("truncate", [dict(r) for r in base if r[time_key] <= c])]
        if perturb:
            runs.append(("perturb", _strip_seq(_perturb(ordered, c, rng, time_key, keep))))
        for mode, rs in runs:
            got = _upto([dict(d) for d in screen(rs)], c)
            ok = got == want
            p = Probe(c, mode, ok, len(want))
            if not ok:
                gs, ws = set(got), set(want)
                p.only_full = sorted(ws - gs)
                p.only_probe = sorted(gs - ws)
                if first is None:
                    first = p
            out.append(p)
            if first is not None and stop_at_first:
                return CheckResult("LOOKAHEAD", md5_full, len(base), len(full), out, first)
    return CheckResult("LOOKAHEAD" if first is not None else "CAUSAL_OK", md5_full, len(base), len(full), out, first)


# ---------------------------------------------------------------- 3. stream / batch equivalence


def stream_batch_equivalence(decide: Callable, screen: Callable, rows: Iterable[Mapping[str, Any]], *, allowed_fields: Iterable[str] | None = None,
                             time_key: str = TIME_KEY) -> dict:
    rows = list(rows)
    s = run_stream(decide, rows, allowed_fields=allowed_fields, time_key=time_key)
    b = [dict(d) for d in screen(_strip_seq(order_rows(rows, time_key)))]
    mb = md5_decisions(b)
    cs, cb = set(canon(s.decisions)), set(canon(b))
    return {"schema": SCHEMA, "equivalent": s.md5 == mb, "md5_stream": s.md5, "md5_batch": mb, "n_stream": len(s.decisions), "n_batch": len(b),
            "n_only_stream": len(cs - cb), "n_only_batch": len(cb - cs)}


# ---------------------------------------------------------------- CLI


def _load(spec: str) -> Callable:
    mod, _, fn = spec.partition(":")
    if not mod or not fn:
        raise SystemExit(f"bad callable spec {spec!r}: want pkg.module:function")
    return getattr(importlib.import_module(mod), fn)


def _read_jsonl(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="lookahead check of a batch screen")
    c.add_argument("--screen", required=True)
    c.add_argument("--rows", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--probes", type=int, default=64)
    c.add_argument("--seed", type=int, default=1)
    c.add_argument("--time-key", default=TIME_KEY)
    c.add_argument("--keep-field", action="append", default=[])
    c.add_argument("--no-perturb", action="store_true")
    e = sub.add_parser("equiv", help="md5 equivalence of a stream decide() and a batch screen()")
    e.add_argument("--screen", required=True)
    e.add_argument("--decide", required=True)
    e.add_argument("--rows", required=True)
    e.add_argument("--out", required=True)
    e.add_argument("--time-key", default=TIME_KEY)
    a = ap.parse_args(argv)
    try:
        rows = _read_jsonl(a.rows)
        check_fields(rows)
        if a.cmd == "check":
            res = lookahead_check(_load(a.screen), rows, probes=a.probes, seed=a.seed, time_key=a.time_key, perturb=not a.no_perturb, keep_fields=a.keep_field)
            rep = res.as_dict()
            code = 3 if res.verdict == "LOOKAHEAD" else 0
        else:
            rep = stream_batch_equivalence(_load(a.decide), _load(a.screen), rows, time_key=a.time_key)
            code = 0 if rep["equivalent"] else 3
    except (FieldRefused, ValueError, LookaheadError) as ex:
        print(f"causal_harness: REFUSED: {ex}", file=sys.stderr)
        return 2
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, sort_keys=True)
    print(json.dumps({k: rep[k] for k in rep if k in ("verdict", "equivalent", "md5_full", "md5_stream", "md5_batch", "n_decisions")}, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
