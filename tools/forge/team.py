"""FORGE Part B: the self-improving Haiku strategy team loop (islands, ANALYST / PROPOSER / CRITIC, VAL protocol, SHORTLIST).

Paper research only: no wallet, no trade, no send. The API key is read by tools/haiku_caller.py from the environment per call; the job
wrapper scripts/ops/forge-team.sh loads it from the root-only env file. This module never reads or prints it.

Evaluator interface (Part A, tools/forge/evaluate.py, named by --evaluator):
  evaluate(spec, split) -> metrics dict   split is "TRAIN" or "VAL"
  fitness(metrics) -> float               -inf allowed
  FEATURES (optional)  list of names, or dict name -> one-line description (also looked up as FEATURE_NAMES, or in tools.forge.events)
  analyst_data() (optional) -> (feats, fwd) TRAIN-only column dicts for tools/forge/analyst_stats.py
Metric keys read (with aliases): n, n_days, mean, median, total, ex_top3_total, positive_days, best_day_share, ci90_low,
trades_per_day, max_concurrent, exit_reasons.

Layout of --out (all JSONL except where noted):
  evals.jsonl      one row per evaluated spec on TRAIN (spec, hash, metrics, fitness, island, round, source, failure)
  rejects.jsonl    specs rejected at parse/validation, duplicates, evaluator errors
  calls.jsonl      one row per model call (role, island, round, cost, parse counts)
  val.jsonl        one row per VAL read (kind val) and per placebo read (kind placebo)
  migrations.jsonl, lessons_log.jsonl, SHORTLIST.jsonl (unless --shortlist), lessons.md (text), state.json (checkpoint, JSON),
  ledgers/island-<i>.jsonl (HaikuCaller ledgers, the hard budget)
Resume: re-running with the same --out rebuilds archive, VAL-read set, ownership and counters from the logs and continues at the
saved round of each island. --hours counts from the start of this process.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import threading
import time
from collections import deque
from pathlib import Path

from tools.forge import specs as S
from tools.forge.analyst_stats import analyst_stats
from tools import haiku_caller as hc

PROMPTS = Path(__file__).parent / "prompts"
LESSONS_CAP = 3000
ANALYST_EVERY = 20
CRITIC_EVERY = 10
MIGRATE_EVERY = 30
MIGRANTS = 2
VAL_TOP = 10
VAL_EVERY_S = 2 * 3600
PLACEBO_DRAWS = 5
FAIL_KEEP = 15
SPECS_PER_ROUND = 6
MAX_API_ERRORS = 5
FAMILY_VAL_CAP = 3        # VAL reads per family_key in total
DEAD_MIN_READS = 2        # a family is dead at >= 2 VAL reads, all failing the SHORTLIST rule
FAMILY_TRAIN_CAP = 3      # archive members per family_key kept in island_top (parents / critic / migrants)
DEAD_PROMPT_MAX = 40
LIVE_VENUES = (1,)       # the live DSL lane executor buys PumpSwap only (PR #1280): other venues are not evaluated or VAL-read

SEEDS = [  # (venue, horizon, style): island briefs, cycled when islands > 8
    ("PumpSwap (venue 1)", "scalp, hold under 10 minutes", "momentum"),
    ("PumpSwap (venue 1)", "scalp, hold under 10 minutes", "dip / mean reversion"),
    ("PumpSwap (venue 1)", "swing, hold 1 to 6 hours", "momentum"),
    ("PumpSwap (venue 1)", "swing, hold 1 to 6 hours", "dip / mean reversion"),
    ("PumpSwap (venue 1)", "scalp, hold under 10 minutes", "flow (buy share, net flow, large prints)"),
    ("PumpSwap (venue 1)", "swing, hold 1 to 6 hours", "flow (buy share, net flow, large prints)"),
    ("PumpSwap (venue 1)", "24 hour hold", "momentum"),
    ("PumpSwap (venue 1)", "24 hour hold", "dip / mean reversion"),
]


def island_brief(i: int) -> str:
    v, h, s = SEEDS[i % len(SEEDS)]
    return f"island {i}: venue focus {v}; horizon {h}; style {s}. Stay near this seed but explore."


# ---------------------------------------------------------------- metrics helpers

def family_key(spec: dict) -> str:
    """Venue plus the sorted set of (feature, op) of the entry conditions. Thresholds, exits, stake and cooldown are ignored."""
    v = spec.get("venue", "any")
    conds = sorted({(str(c.get("f")), str(c.get("op"))) for c in spec.get("entry") or []})
    return f"v{v}|" + ",".join(f"{f}{op}" for f, op in conds)


def _family_parts(key: str):
    v, _, rest = key.partition("|")
    return v, frozenset(x for x in rest.split(",") if x)


def jnum(x):
    """JSON-safe number: non-finite -> None."""
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def clean_json(o):
    if isinstance(o, dict):
        return {str(k): clean_json(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean_json(v) for v in o]
    if hasattr(o, "item") and not isinstance(o, (str, bytes)):
        try:
            return clean_json(o.item())
        except Exception:
            return str(o)
    return jnum(o)


ALIASES = {
    "mean": ("mean", "mean_net_sol", "mean_sol"),
    "ex_top3": ("ex_top3_total", "ex_top3"),
    "pos_days": ("positive_days", "pos_days"),
    "n": ("n",), "n_days": ("n_days",), "ci": ("ci90_low", "ci90_lo"), "total": ("total",),
    "best_day": ("best_day_share",), "tpd": ("trades_per_day",),
}


def mget(m: dict, key: str, default=0.0):
    for a in ALIASES.get(key, (key,)):
        if a in m and m[a] is not None:
            v = m[a]
            if isinstance(v, str) and "/" in v:  # "k/n"
                v = v.split("/")[0]
            try:
                return float(v)
            except Exception:
                return default
    return default


def default_fitness(m: dict) -> float:
    """Documented FORGE fitness (used only by the fake evaluator): CI90_low * sqrt(trades_per_day); -inf if n<40, n_days<6 or
    ex_top3 <= 0; x0.5 if the best day is over 50 % of the total."""
    if mget(m, "n") < 40 or mget(m, "n_days") < 6 or mget(m, "ex_top3") <= 0:
        return float("-inf")
    f = mget(m, "ci") * math.sqrt(max(mget(m, "tpd"), 0.0))
    if mget(m, "best_day") > 0.5:
        f *= 0.5
    return f


def failure_reason(m: dict, fit: float):
    if fit is not None and math.isfinite(fit) and fit > 0:
        return None
    n, nd = mget(m, "n"), mget(m, "n_days")
    if n < 40:
        return f"n={int(n)} < 40 trades"
    if nd < 6:
        return f"only {int(nd)} days < 6"
    if mget(m, "ex_top3") <= 0:
        return "ex-top-3 total <= 0 (edge is a few trades)"
    if fit is None or not math.isfinite(fit):
        return "fitness -inf"
    return f"CI90 low {mget(m, 'ci'):.4f} <= 0 (no edge after fees)"


def brief_metrics(m: dict) -> dict:
    return {"n": mget(m, "n"), "days": mget(m, "n_days"), "mean": round(mget(m, "mean"), 5), "ci90_low": round(mget(m, "ci"), 5),
            "ex_top3": round(mget(m, "ex_top3"), 4), "pos_days": mget(m, "pos_days"), "best_day_share": round(mget(m, "best_day"), 3),
            "tpd": round(mget(m, "tpd"), 2)}


# ---------------------------------------------------------------- models

class RealModel:
    """One island's HaikuCaller (own ledger, hard budget). System = [cached fixed part, role part]."""

    def __init__(self, ledger: Path, budget: float):
        self.caller = hc.HaikuCaller(ledger, budget)
        self.budget = budget

    def ask(self, role, fixed, role_prompt, user, max_tokens, temperature=None):
        system = [{"type": "text", "text": fixed, "cache_control": {"type": "ephemeral"}}, {"type": "text", "text": role_prompt}]
        r = self.caller.call(messages=[{"role": "user", "content": user}], max_tokens=max_tokens, system=system, temperature=temperature)
        return r.text, r.cost_usd

    def spent(self) -> float:
        with self.caller.ledger.locked(exclusive=False):
            return self.caller.ledger.spent()


class FakeModel:
    """Deterministic stand-in for tests and --dry. Costs `cost` per call and raises BudgetExceeded like HaikuCaller."""

    def __init__(self, budget, features, seed=0, cost=0.01, bad_every=5):
        self.budget, self.features, self.cost, self._spent = budget, list(features), cost, 0.0
        self.rng = random.Random(seed)
        self.calls, self.bad_every = 0, bad_every
        self.lock = threading.Lock()

    def _spec(self):
        r = self.rng
        k = r.randint(1, 4)
        return {"entry": [{"f": r.choice(self.features), "op": r.choice(S.OPS), "v": round(r.uniform(-1, 3), 2)} for _ in range(k)],
                "venue": 1, "cooldown_s": r.choice([0, 30, 300]), "one_per_pool": r.random() < 0.7,
                "exit": {"tp": r.choice([None, 0.3, 1.0]), "sl": r.choice([None, 0.2, 0.5]), "trail": r.choice([None, 0.3]),
                         "max_hold_s": r.choice([300, 3600, 86400])}, "stake_sol": 0.1}

    def ask(self, role, fixed, role_prompt, user, max_tokens, temperature=None):
        with self.lock:
            if self._spent + self.cost > self.budget + 1e-12:
                raise hc.BudgetExceeded("fake budget")
            self._spent += self.cost
            self.calls += 1
            n = self.calls
            if role == "proposer":
                self.last_user = user
            if role == "proposer":
                items = [{"spec": self._spec(), "rationale": "fake"} for _ in range(SPECS_PER_ROUND)]
                if n % self.bad_every == 0:  # garbage variety, to exercise the parser
                    items[0]["spec"]["entry"][0]["f"] = "no_such_feature"
                    items[1]["spec"]["exit"]["tp"] = 99
                    items[2] = {"spec": "nonsense"}
                    return "Here you go:\n```json\n" + json.dumps({"specs": items}) + "\n```\nHope it helps", self.cost
                if n % self.bad_every == 1:
                    return "I cannot do that.", self.cost
                return json.dumps({"specs": items}), self.cost
            if role == "analyst":
                return json.dumps({"hypotheses": [f"fake hypothesis {n}"]}), self.cost
            long = "rule: avoid extreme thresholds. " * (200 if n % 2 else 3)
            return json.dumps({"lessons": long + f" call{n}", "simplify": [{"spec": self._spec(), "rationale": "simpler"}]}), self.cost

    def spent(self) -> float:
        return self._spent


class FakeEvaluator:
    """Deterministic fake for tests and --dry: metrics depend on the spec hash and the split only."""
    FEATURES = {f"f{i}": f"fake feature {i}" for i in range(12)}

    def __init__(self):
        self.calls = {"TRAIN": 0, "VAL": 0}
        self.lock = threading.Lock()

    def evaluate(self, spec, split):
        with self.lock:
            self.calls[split] += 1
        h = int(S.spec_hash(spec), 16)
        r = random.Random(h ^ (0 if split == "TRAIN" else 0x5A5A))
        n = r.randint(10, 200) if split == "TRAIN" else r.randint(5, 60)
        nd = r.randint(3, 15) if split == "TRAIN" else r.randint(1, 5)
        mean = r.uniform(-0.02, 0.03)
        m = {"n": n, "n_days": nd, "mean": mean, "median": mean / 2, "total": mean * n, "ex_top3_total": mean * n - r.uniform(0, 0.05),
             "positive_days": r.randint(0, nd), "best_day_share": r.uniform(0.1, 0.7), "ci90_low": mean - 0.01,
             "trades_per_day": n / nd, "max_concurrent": 3, "exit_reasons": {"tp": 1, "sl": 2, "time": n - 3}}
        return m

    def fitness(self, m):
        return default_fitness(m)

    def analyst_data(self):
        import numpy as np
        rng = np.random.default_rng(1)
        feats = {k: rng.normal(size=2000) for k in self.FEATURES}
        fwd = {h: 0.01 * feats["f0"] + rng.normal(scale=0.1, size=2000) for h in ("1m", "5m", "30m", "2h", "24h")}
        return feats, fwd


def resolve_features(ev):
    fd = getattr(ev, "feature_dictionary", None)
    if fd is not None:
        try:
            return dict(fd())
        except Exception as e:  # event table missing -> fall back to the static dictionary
            print(f"feature_dictionary failed ({type(e).__name__}); using static FEATURES", flush=True)
    for src in (ev, getattr(ev, "events", None)):
        if src is None:
            continue
        for name in ("FEATURES", "FEATURE_NAMES"):
            f = getattr(src, name, None)
            if f:
                return dict(f) if isinstance(f, dict) else {k: "" for k in f}
    try:
        import importlib
        mod = importlib.import_module("tools.forge.events")
        for name in ("FEATURES", "FEATURE_NAMES"):
            f = getattr(mod, name, None)
            if f:
                return dict(f) if isinstance(f, dict) else {k: "" for k in f}
    except Exception:
        pass
    raise SystemExit("REFUSED: evaluator exposes no FEATURES / FEATURE_NAMES (needed for the feature dictionary and validation)")


def placebo_specs(spec: dict, features: list, seed: str, k: int = PLACEBO_DRAWS) -> list:
    """Same exits, venue, cooldown; entry conditions randomly permuted: thresholds are shuffled across the used features; if that
    changes nothing (one condition, or identical result) each condition gets a random other feature with the same op and value."""
    rng = random.Random(seed)
    out = []
    for _ in range(k):
        ent = [dict(c) for c in spec["entry"]]
        fs = [c["f"] for c in ent]
        shuffled = fs[:]
        rng.shuffle(shuffled)
        if len(ent) < 2 or shuffled == fs:
            shuffled = [rng.choice([f for f in features if f != c["f"]] or features) for c in ent]
        for c, f in zip(ent, shuffled):
            c["f"] = f
        p = json.loads(json.dumps(spec))
        p["entry"] = ent
        out.append(p)
    return out


# ---------------------------------------------------------------- team

class Team:
    def __init__(self, out: Path, evaluator, model_factory, features: dict, islands=8, budget_per_island=20.0, hours=24.0,
                 val_every_s=VAL_EVERY_S, shortlist_path=None, max_rounds=None, eval_workers=4, final_val=True, log=print):
        self.out, self.ev, self.features = Path(out), evaluator, features
        self.fnames = list(features)
        self.n_islands, self.budget, self.hours = islands, budget_per_island, hours
        self.val_every_s, self.max_rounds, self.final_val, self.log = val_every_s, max_rounds, final_val, log
        self.out.mkdir(parents=True, exist_ok=True)
        self.shortlist_path = Path(shortlist_path) if shortlist_path else self.out / "SHORTLIST.jsonl"
        self.lock = threading.RLock()
        self.isl_stats = {}
        self.file_lock = threading.Lock()
        self.eval_sem = threading.Semaphore(max(1, eval_workers))
        self.stop = threading.Event()
        self.t0 = time.time()
        self.archive: dict = {}          # hash -> entry
        self.val_read: dict = {}         # hash -> val metrics (VAL read once per spec)
        self.val_placebo_reads = 0
        self.fam_val_n: dict = {}        # family_key -> VAL reads started (incl. in flight)
        self.fam_val_pass: dict = {}     # family_key -> list of bool (SHORTLIST verdict of each completed VAL read)
        self.shortlist_n = 0
        self.failures = [deque(maxlen=FAIL_KEEP) for _ in range(islands)]
        self.counts = {"evaluated": 0, "dups": 0, "rejected": 0, "dead_family": 0, "repaired": 0, "json_failed": 0, "eval_errors": 0, "calls": 0}
        self.fixed = (PROMPTS / "fixed.md").read_text().replace("{{FEATURES}}", "\n".join(
            f"- {k}: {v}" if v else f"- {k}" for k, v in features.items()))
        self.lessons = ""
        self.last_val_ts = 0.0
        self.analyst_text = None
        self.models = [model_factory(i, self.out / "ledgers" / f"island-{i}.jsonl", budget_per_island) for i in range(islands)]
        self.islands = [Island(self, i) for i in range(islands)]
        self._resume()

    # -- io
    def jlog(self, name, row):
        row = clean_json({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **row})
        line = json.dumps(row, sort_keys=True)
        with self.file_lock:
            with open(self.out / name, "a") as fh:
                fh.write(line + "\n")
                fh.flush()

    def _rows(self, name):
        p = self.out / name
        if not p.exists():
            return []
        rows = []
        for line in p.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except Exception:
                pass  # a torn last line from a kill
        return rows

    def checkpoint(self):
        with self.lock:
            st = {"version": 1, "last_val_ts": self.last_val_ts, "val_placebo_reads": self.val_placebo_reads, "analyst_text": self.analyst_text,
                  "islands": [{"id": i.id, "round": i.round, "done": i.done, "hypotheses": i.hypotheses} for i in self.islands]}
            tmp = self.out / "state.json.tmp"
            tmp.write_text(json.dumps(clean_json(st)))
            os.replace(tmp, self.out / "state.json")

    def _resume(self):
        lp = self.out / "lessons.md"
        if lp.exists():
            self.lessons = lp.read_text()[:LESSONS_CAP]
        for r in self._rows("evals.jsonl"):
            h = r["hash"]
            if h in self.archive:
                continue
            fit = r.get("fitness")
            if r.get("metrics") and not r.get("failure"):
                try:
                    fit = float(self.ev.fitness(r["metrics"]))  # re-scored on resume, so a changed fitness function applies to the whole archive
                except Exception:
                    pass
            self.archive[h] = {"hash": h, "spec": r["spec"], "metrics": r.get("metrics") or {}, "fitness": float("-inf") if fit is None else fit,
                               "island": r["island"], "round": r["round"], "owners": {r["island"]}}
            self.counts["evaluated"] += 1
            if r.get("failure") and 0 <= r["island"] < self.n_islands:
                self.failures[r["island"]].append({"spec": r["spec"], "reason": r["failure"]})
        for r in self._rows("migrations.jsonl"):
            if r["hash"] in self.archive and 0 <= r["to"] < self.n_islands:
                self.archive[r["hash"]]["owners"].add(r["to"])
        for r in self._rows("val.jsonl"):
            if r.get("kind") == "val":
                self.val_read[r["hash"]] = r.get("metrics") or {}
                if r.get("spec") and r.get("metrics"):
                    fk = family_key(r["spec"])
                    self.fam_val_n[fk] = self.fam_val_n.get(fk, 0) + 1
                    self.fam_val_pass.setdefault(fk, []).append(self.is_shortlist(r["metrics"]))
            elif r.get("kind") == "placebo":
                self.val_placebo_reads += 1
        if self.shortlist_path.exists():
            self.shortlist_n = sum(1 for _ in open(self.shortlist_path))
        sp = self.out / "state.json"
        if sp.exists():
            st = json.loads(sp.read_text())
            self.last_val_ts = st.get("last_val_ts", 0.0)
            at = st.get("analyst_text")
            # a failed stats run (e.g. EMFILE before ulimit) must not be cached across resumes
            self.analyst_text = None if (at or "").startswith("(TRAIN statistics failed") else at
            for s in st.get("islands", []):
                if s["id"] < self.n_islands:
                    isl = self.islands[s["id"]]
                    isl.round, isl.hypotheses = s["round"], s.get("hypotheses", "")
                    isl.done = "budget" if s.get("done") == "budget" else None  # time/stop/max_rounds are per process; a spent budget stays spent
        if self.archive:
            self.log(f"RESUME: {len(self.archive)} specs, {len(self.val_read)} VAL reads, rounds={[i.round for i in self.islands]}")

    # -- dead families
    def dead_families(self):
        """Set of family keys with >= DEAD_MIN_READS completed VAL reads, none of which passes the SHORTLIST rule. Derived from val.jsonl on resume."""
        with self.lock:
            return {k for k, v in self.fam_val_pass.items() if len(v) >= DEAD_MIN_READS and not any(v)}

    def is_dead(self, spec):
        """True if the spec's family is dead, or has the same venue and a (feature, op) set that is a superset of a dead family's."""
        v, ops = _family_parts(family_key(spec))
        for k in self.dead_families():
            dv, dops = _family_parts(k)
            if dv == v and dops <= ops:
                return True
        return False

    # -- evaluation
    def evaluate_train(self, spec, island, rnd, source, rationale=""):
        """-> ('new'|'dup'|'error', entry). Dedupes by canonical hash across all islands."""
        h = S.spec_hash(spec)
        if spec.get("venue") not in LIVE_VENUES:
            with self.lock:
                self.counts["venue_not_live"] = self.counts.get("venue_not_live", 0) + 1
                self.failures[island].append({"spec": spec, "reason": "venue must be 1 (PumpSwap): only PumpSwap trades live; not evaluated"})
            self.jlog("rejects.jsonl", {"island": island, "round": rnd, "reason": "venue_not_live", "hash": h})
            return "rejected", None
        if self.is_dead(spec):
            with self.lock:
                self.counts["dead_family"] += 1
                self.failures[island].append({"spec": spec, "reason": "dead family (failed VAL), not evaluated"})
            self.jlog("rejects.jsonl", {"island": island, "round": rnd, "reason": "dead_family", "hash": h, "family": family_key(spec)})
            return "rejected", None
        with self.lock:
            if h in self.archive:
                self.counts["dups"] += 1
                return "dup", self.archive[h]
            self.archive[h] = None  # reserve so a concurrent island does not evaluate the same spec
        try:
            with self.eval_sem:
                te = time.time()
                m = clean_json(self.ev.evaluate(spec, "TRAIN"))
                fit = float(self.ev.fitness(m))
                with self.lock:
                    st = self.isl_stats.setdefault(island, {"eval_n": 0, "eval_s": 0.0, "round": 0, "round_s": 0.0})
                    st["eval_n"] += 1
                    st["eval_s"] += time.time() - te
        except Exception as e:  # evaluator bug on one spec must not kill the run
            with self.lock:
                self.archive.pop(h, None)
                self.counts["eval_errors"] += 1
            self.jlog("rejects.jsonl", {"island": island, "round": rnd, "reason": f"evaluator error {type(e).__name__}: {str(e)[:120]}", "hash": h})
            return "error", None
        if math.isnan(fit):
            fit = float("-inf")
        reason = failure_reason(m, fit)
        ent = {"hash": h, "spec": spec, "metrics": m, "fitness": fit, "island": island, "round": rnd, "owners": {island}}
        with self.lock:
            self.archive[h] = ent
            self.counts["evaluated"] += 1
            if reason:
                self.failures[island].append({"spec": spec, "reason": reason})
        self.jlog("evals.jsonl", {"kind": "eval", "hash": h, "island": island, "round": rnd, "source": source, "rationale": rationale,
                                  "spec": spec, "metrics": m, "fitness": fit, "fitness_neginf": not math.isfinite(fit), "failure": reason})
        return "new", ent

    def island_top(self, i, k=10):
        with self.lock:
            es = [e for e in self.archive.values() if e and i in e["owners"] and math.isfinite(e["fitness"])]
        out, per = [], {}
        for e in sorted(es, key=lambda e: -e["fitness"]):
            fk = family_key(e["spec"])
            if per.get(fk, 0) >= FAMILY_TRAIN_CAP:
                continue  # novelty pressure: at most FAMILY_TRAIN_CAP members per family_key
            per[fk] = per.get(fk, 0) + 1
            out.append(e)
            if len(out) >= k:
                break
        return out

    def global_top_fitness(self):
        with self.lock:
            fs = [e["fitness"] for e in self.archive.values() if e and math.isfinite(e["fitness"])]
        return max(fs) if fs else None

    def migrate(self, i):
        to = (i + 1) % self.n_islands
        if to == i:
            return
        for e in self.island_top(i, MIGRANTS):
            with self.lock:
                e["owners"].add(to)
            self.jlog("migrations.jsonl", {"hash": e["hash"], "from": i, "to": to})

    def add_lessons(self, text, island, rnd):
        text = (text or "").strip()
        if not text:
            return
        if len(text) > LESSONS_CAP:
            cut = text[:LESSONS_CAP]
            text = cut.rsplit("\n", 1)[0] if "\n" in cut[LESSONS_CAP // 2:] else cut
        with self.lock:
            self.lessons = text
            (self.out / "lessons.md").write_text(text)
        self.jlog("lessons_log.jsonl", {"island": island, "round": rnd, "chars": len(text)})

    def analyst_block(self):
        with self.lock:
            if self.analyst_text is not None:
                return self.analyst_text
        fn = getattr(self.ev, "analyst_data", None)
        if fn is None:
            txt = "(no TRAIN statistics available from the evaluator)"
        else:
            try:
                feats, fwd = fn()
                txt, _ = analyst_stats(feats, fwd)
            except Exception as e:
                txt = f"(TRAIN statistics failed: {type(e).__name__})"
        with self.lock:
            self.analyst_text = txt
        return txt

    # -- VAL protocol
    def val_pass(self):
        """Evaluate the global top VAL_TOP (by TRAIN fitness) not yet VAL-read, once each. Returns number read."""
        with self.lock:
            cands = [e for e in self.archive.values() if e and math.isfinite(e["fitness"]) and e["fitness"] > 0 and e["hash"] not in self.val_read and e["spec"].get("venue") in LIVE_VENUES]  # VAL reads only for TRAIN-positive specs
            dead = self.dead_families()
            picked = []
            for e in sorted(cands, key=lambda e: -e["fitness"]):
                fk = family_key(e["spec"])
                if fk in dead or self.fam_val_n.get(fk, 0) >= FAMILY_VAL_CAP:
                    continue
                self.fam_val_n[fk] = self.fam_val_n.get(fk, 0) + 1  # counted at selection, so the cap holds inside one pass
                picked.append(e)
                if len(picked) >= VAL_TOP:
                    break
            cands = picked
            self.last_val_ts = time.time()
            for e in cands:
                self.val_read[e["hash"]] = {}  # mark before reading: never a second read, even if this pass dies midway
        for e in cands:
            try:
                vm = clean_json(self.ev.evaluate(e["spec"], "VAL"))
            except Exception as ex:
                self.jlog("rejects.jsonl", {"reason": f"VAL evaluator error {type(ex).__name__}", "hash": e["hash"]})
                continue
            with self.lock:
                self.val_read[e["hash"]] = vm
                self.fam_val_pass.setdefault(family_key(e["spec"]), []).append(self.is_shortlist(vm))
            self.jlog("val.jsonl", {"kind": "val", "hash": e["hash"], "spec": e["spec"], "metrics": vm, "train_fitness": e["fitness"],
                                    "val_reads_total": len(self.val_read)})
            if self.is_shortlist(vm):
                self.write_shortlist(e, vm)
        self.checkpoint()
        return len(cands)

    @staticmethod
    def is_shortlist(vm):
        return mget(vm, "n") >= 15 and mget(vm, "mean") > 0 and mget(vm, "ex_top3") > 0 and mget(vm, "pos_days") >= 3

    def write_shortlist(self, e, vm):
        pls = []
        for p in placebo_specs(e["spec"], self.fnames, e["hash"]):
            try:
                pm = clean_json(self.ev.evaluate(p, "VAL"))
            except Exception:
                continue
            with self.lock:
                self.val_placebo_reads += 1
            self.jlog("val.jsonl", {"kind": "placebo", "of": e["hash"], "spec": p, "metrics": pm})
            pls.append(pm)
        pmeans = [mget(p, "mean") for p in pls if mget(p, "n") > 0]
        row = {"hash": e["hash"], "spec": e["spec"], "island": e["island"], "train": e["metrics"], "train_fitness": e["fitness"], "val": vm,
               "placebo": {"draws": len(pls), "means": pmeans, "max_mean": max(pmeans) if pmeans else None,
                           "mean_of_means": sum(pmeans) / len(pmeans) if pmeans else None,
                           "beats_placebo": bool(pmeans) and mget(vm, "mean") > max(pmeans)},
               "note": "VAL read once; placebo = same exits, entry features permuted; placebo is reported, not part of the shortlist rule"}
        self.shortlist_path.parent.mkdir(parents=True, exist_ok=True)
        with self.file_lock:
            with open(self.shortlist_path, "a") as fh:
                fh.write(json.dumps(clean_json({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **row}), sort_keys=True) + "\n")
        with self.lock:
            self.shortlist_n += 1

    # -- progress / run
    def spend(self):
        return [m.spent() for m in self.models]

    def progress(self, final=False):
        sp = sum(self.spend())
        el = time.time() - self.t0
        pct = max(el / (self.hours * 3600) if self.hours else 0, sp / (self.budget * self.n_islands) if self.budget else 1)
        best = self.global_top_fitness()
        note = (f"spend ${sp:.3f}/${self.budget * self.n_islands:.2f} specs={self.counts['evaluated']} best_fit={best} "
                f"val_reads={len(self.val_read)} shortlist={self.shortlist_n}")
        with self.lock:
            per = " | ".join(f"i{k}: r{v['round']} {v['round_s']:.0f}s/round eval {v['eval_s'] / max(v['eval_n'], 1):.1f}s" for k, v in sorted(self.isl_stats.items()))
        if per:
            note += " | " + per
        p = os.environ.get("MISCUSI_PROGRESS")
        if p:
            try:
                Path(p).write_text(json.dumps({"pct": 100 if final else min(99, round(100 * pct, 1)), "note": note}))
            except OSError:
                pass
        return note

    def run(self):
        total = self.budget * self.n_islands
        self.log(f"FORGE team: {self.n_islands} islands x ${self.budget:.2f} = ${total:.2f} total budget, {self.hours} h, out={self.out}")
        deadline = self.t0 + self.hours * 3600
        if not self.last_val_ts:
            self.last_val_ts = time.time()
        threads = [threading.Thread(target=i.run, args=(deadline,), daemon=True) for i in self.islands if not i.done]
        for t in threads:
            t.start()
        last_status = time.time()
        while any(t.is_alive() for t in threads):
            time.sleep(0.05)
            if time.time() >= deadline:
                self.stop.set()
            if self.val_every_s is not None and time.time() - self.last_val_ts >= self.val_every_s:
                self.val_pass()
            if time.time() - last_status >= 60:
                last_status = time.time()
                self.log(self.progress())
        for t in threads:
            t.join()
        if self.final_val:
            self.val_pass()
        self.checkpoint()
        note = self.progress(final=True)
        self.log(f"FORGE done: {note}; stops={[i.done for i in self.islands]}; counts={self.counts}; VAL placebo reads={self.val_placebo_reads}")
        return 0


class Island:
    def __init__(self, team: Team, i: int):
        self.t, self.id = team, i
        self.round, self.done, self.hypotheses = 0, None, ""
        self.brief = island_brief(i)
        self.errors = 0

    def _prompt(self, name):
        return (PROMPTS / name).read_text().replace("{{ISLAND_BRIEF}}", self.brief).replace("{{LESSONS_CAP}}", str(LESSONS_CAP))

    def _ask(self, role, user, max_tokens, temperature=None):
        """-> text or None (budget stop / api failure recorded in self.done)."""
        t = self.t
        try:
            text, cost = t.models[self.id].ask(role, t.fixed, self._prompt(f"{role}.md"), user, max_tokens, temperature)
        except hc.BudgetExceeded:
            self.done = "budget"
            return None
        except (hc.ApiError, hc.RetriesExhausted, hc.HaikuCallerError) as e:
            self.errors += 1
            t.jlog("calls.jsonl", {"role": role, "island": self.id, "round": self.round, "error": type(e).__name__})
            if self.errors >= MAX_API_ERRORS:
                self.done = "api_errors"
            else:
                time.sleep(min(5.0 * self.errors, 30.0) if not os.environ.get("FORGE_NO_SLEEP") else 0)
            return None
        self.errors = 0
        with t.lock:
            t.counts["calls"] += 1
        return text, cost

    def _consume(self, role, text, cost, key="specs", source=None):
        t = self.t
        items, st = S.parse_specs(text, t.fnames, key)
        with t.lock:
            t.counts["rejected"] += st["rejected"]
            t.counts["repaired"] += st["repaired"]
            t.counts["json_failed"] += st["json_failed"]
        row = {"role": role, "island": self.id, "round": self.round, "cost_usd": cost, "parse": {k: v for k, v in st.items() if k != "reasons"}}
        if st["json_failed"]:
            row["raw_head"] = text[:300]
        t.jlog("calls.jsonl", row)
        for r in st["reasons"]:
            t.jlog("rejects.jsonl", {"island": self.id, "round": self.round, "reason": r})
            with t.lock:
                t.failures[self.id].append({"spec": None, "reason": f"invalid spec: {r}"})
        for it in items:
            if t.stop.is_set():
                break
            t.evaluate_train(it["spec"], self.id, self.round, source or role, it["rationale"])

    def step(self):
        t, r = self.t, self.round
        if r % ANALYST_EVERY == 0:
            res = self._ask("analyst", f"TRAIN statistics:\n{t.analyst_block()}", 1500)
            if res is None and self.done:
                return
            if res:
                obj = S.extract_json(res[0])
                hy = obj.get("hypotheses") if isinstance(obj, dict) else None
                if isinstance(hy, list):
                    self.hypotheses = "\n".join(f"- {str(h)[:300]}" for h in hy[:8])
                t.jlog("calls.jsonl", {"role": "analyst", "island": self.id, "round": r, "cost_usd": res[1], "hypotheses": bool(self.hypotheses)})
        top = [{"spec": e["spec"], "fitness": e["fitness"], **brief_metrics(e["metrics"])} for e in t.island_top(self.id, 10)]
        with t.lock:
            fails = list(t.failures[self.id])
            lessons = t.lessons
        dead = sorted(t.dead_families())[:DEAD_PROMPT_MAX]
        dead_block = ("DEAD FAMILIES (failed out-of-sample, do not propose these or supersets):\n" + "\n".join(f"- {k}" for k in dead) + "\n\n") if dead else ""
        user = (f"Round {r}.\n{dead_block}LESSONS (shared):\n{lessons or '(none yet)'}\n\nANALYST HYPOTHESES:\n{self.hypotheses or '(none yet)'}\n\n"
                f"ISLAND TOP {len(top)} (TRAIN):\n{json.dumps(clean_json(top))}\n\nLAST {len(fails)} FAILURES:\n{json.dumps(clean_json(fails))}\n\n"
                f"Return {SPECS_PER_ROUND} strategies as specified.")
        res = self._ask("proposer", user, 3000)
        if res is None:
            return
        self._consume("proposer", res[0], res[1])
        if (r + 1) % CRITIC_EVERY == 0 and not self.done:
            top = self.t.island_top(self.id, 10)
            u = (f"CURRENT LESSONS:\n{t.lessons or '(none)'}\n\nTOP STRATEGIES (TRAIN):\n"
                 f"{json.dumps(clean_json([{'spec': e['spec'], **brief_metrics(e['metrics'])} for e in top]))}")
            res = self._ask("critic", u, 2000)
            if res:
                obj = S.extract_json(res[0])
                if isinstance(obj, dict) and isinstance(obj.get("lessons"), str):
                    t.add_lessons(obj["lessons"], self.id, r)
                self._consume("critic", res[0], res[1], key="simplify", source="critic")
        if (r + 1) % MIGRATE_EVERY == 0:
            t.migrate(self.id)

    def run(self, deadline):
        t = self.t
        while not self.done:
            if t.stop.is_set() or time.time() >= deadline:
                self.done = "stopped"
                break
            if t.max_rounds is not None and self.round >= t.max_rounds:
                self.done = "max_rounds"
                break
            tr = time.time()
            self.step()
            with t.lock:
                st = t.isl_stats.setdefault(self.id, {"eval_n": 0, "eval_s": 0.0, "round": 0, "round_s": 0.0})
                st["round"], st["round_s"] = self.round + 1, time.time() - tr
                ev_s = st["eval_s"] / max(st["eval_n"], 1)
            t.jlog("rounds.jsonl", {"island": self.id, "round": self.round, "round_s": round(time.time() - tr, 2), "mean_eval_s": round(ev_s, 2)})
            if self.done in ("budget", "api_errors"):
                break
            self.round += 1
            t.checkpoint()
            t.progress()
        t.checkpoint()


def main(argv=None):
    ap = argparse.ArgumentParser(description="FORGE team loop (paper research; no trading)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--islands", type=int, default=8)
    ap.add_argument("--budget-per-island", type=float, default=20.0, help="USD hard stop per island (HaikuCaller ledger)")
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--val-every-s", type=float, default=VAL_EVERY_S)
    ap.add_argument("--shortlist", default=None, help="SHORTLIST.jsonl path (default <out>/SHORTLIST.jsonl)")
    ap.add_argument("--evaluator", default="tools.forge.evaluate")
    ap.add_argument("--max-rounds", type=int, default=None)
    ap.add_argument("--eval-workers", type=int, default=4)
    ap.add_argument("--no-final-val", action="store_true")
    ap.add_argument("--dry", action="store_true", help="fake model and fake evaluator; no API call, no data read")
    a = ap.parse_args(argv)
    if a.islands < 1 or a.budget_per_island < 0:
        raise SystemExit("REFUSED: islands >= 1 and budget >= 0")
    if a.dry:
        ev = FakeEvaluator()
        feats = dict(ev.FEATURES)

        def factory(i, ledger, budget):
            return FakeModel(budget, feats, seed=i)
    else:
        import importlib
        ev = importlib.import_module(a.evaluator)
        feats = resolve_features(ev)

        def factory(i, ledger, budget):
            return RealModel(ledger, budget)
    team = Team(Path(a.out), ev, factory, feats, islands=a.islands, budget_per_island=a.budget_per_island, hours=a.hours,
                val_every_s=a.val_every_s, shortlist_path=a.shortlist, max_rounds=a.max_rounds, eval_workers=a.eval_workers,
                final_val=not a.no_final_val, log=lambda s: print(s, flush=True))
    return team.run()


if __name__ == "__main__":
    sys.exit(main())
