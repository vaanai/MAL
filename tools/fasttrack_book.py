#!/usr/bin/env python3
"""fasttrack_book: one manifest -> lint -> pins -> the whole forward-book registration pack (P24 fast track). PAPER only. No key, no network, no job submit.

Nothing here registers a book, submits a job, reads a tape, a ledger, `bots.json` contents, an outcome or a P&L. It writes a pack that a manager reviews, and quant-proof
reviews the MANIFEST plus this file's sha256 (the template) instead of a hand-written pack.

WHY (docs/research/p24-fast-track/PLAN.md section 1)
  FLOOR's pack (hunt24/floor-1026) was 15 hand-written scripts with hard-coded shas, clocks, deadlines and thresholds. Its late defects included a stale E3 sha posted to the
  notebook, a "CLOCK - 24 h 15 min" deadline that QP OK'd twice, a smoke cadence of ">= 10 status lines per hour" when the code emits 6, a run start 5 min before a 15-min
  start-up, an L3 test run in a venv without its deps, a gate constant left at 2.0/day, and a job limit above the platform's 14 d cap (Q50: 15 d). HV4 lost a QP round to stale
  ruling shas and FILL placeholders. Each is mechanical: computed here from one manifest and the code at the frozen sha, never typed by hand.

COMMANDS
    python -m tools.fasttrack_book lint   MANIFEST.json [--now-iso ISO]                     # static checks; exit 0 = PASS, 1 = FAIL
    python -m tools.fasttrack_book pin    MANIFEST.json --repo DIR [--out pins.json]        # sha256 of every pinned file AT frozen_sha (git show), code constants
    python -m tools.fasttrack_book render MANIFEST.json --repo DIR --out PACKDIR [--now-iso ISO]   # lint + pin + write the pack (refuses a non-empty PACKDIR)
    python -m tools.fasttrack_book verify PACKDIR --repo DIR                                # pack SHA256SUMS + pins recomputed at frozen_sha; run before posting any pin
    python -m tools.fasttrack_book lint-sh FILE...                                          # bash-isms in job scripts (jobs run under sh)

THE PACK (render)
    MANIFEST.json  pins.json  thresholds.json  PINS.sha256  register.sh  smoke.sh  smoke-check.sh  run.sh  blocking-list.txt  jobs.json  REGISTRATION.md  SHA256SUMS
  Every script refuses: a checkout that is not frozen_sha, a dirty checkout, the wrong host, a pinned file whose sha256 differs from PINS.sha256 (`sha256sum -c`), and
  its own time window (register after the deadline; run started too late for its first tick to be at or before the clock).

HOST PROFILES
  `arena_rules_v2` (tools/arena_rules_v2.py via scripts/research/arena-rules-v2-job.sh; FLOOR-shaped rule books) is the one built here. Other hosts (hv4_runner, the H5
  executor, dbc_fwd) get a profile entry with their entry script, env names and cadence constants; the checks and the pack layout stay the same.
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "fasttrack_book_v1"
PLATFORM_MAX_LIMIT_S = 14 * 86_400  # MiScusi job time-limit cap (Q50 sheet said 15 d; FLOOR E4 comment)
HOST_MEM_GB = {"mal-research-0": 64, "mal-fast-0": 1.9}  # fast-0 refuses jobs >= 2 GB (feedback-agent-ops-1018)
DEC031_CAPS = {"feed_gap_pct": 5, "excluded_hour_pct": 10}  # DEC-031 §1.4 and Am.6 item 3: a manifest can never loosen them
FAIL_MODELS = {"flat15", "pressure"}
LABELS = ("price-path", "decision-input")
FORBIDDEN_PATH_PARTS = ("fwdv1", "fwd-v1", "holdout", "forward-1016", "/data/mal/r4b", "laya-opt", "/data/mal/blocks/")
PLACEHOLDER = re.compile(r"\b(FILL|TBD|TODO|XXX)\b")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_PRECONDITIONS = ("p0", "gapcheck", "smoke", "md5", "tests", "qp", "reviewer")  # each a file PRECONDITION-<x>.ok in the pack dir

PROFILES: dict[str, dict[str, Any]] = {
    "arena_rules_v2": {
        "entry": "scripts/research/arena-rules-v2-job.sh",
        "env_prefix": "ARENA_RULES",
        "process_pattern": "arena_rules_v[2].py",
        "rule_registry": ("tools/arena_rules_v2.py", "RULE_MODULES_V2"),
        "cadence": {"tick_ms": ("tools/arena_rules.py", "TICK_MS"), "status_every_ticks": ("tools/arena_rules.py", "STATUS_EVERY_TICKS")},
        "status_marker": '"attempted"',
        "threshold_keys": ("tools/arena_hour_health.py", "THRESHOLD_KEYS"),
        "base_pins": ["tools/arena_feed.py", "tools/arena_paper.py", "tools/arena_score.py", "tools/arena_rules.py", "tools/arena_rules_v2.py",
                      "tools/arena_stack.py", "tools/arena_hour_health.py", "scripts/research/arena-rules-v2-job.sh"],
    },
}

# The pre-registration pre-mortem (PLAN.md section 2.4). Every item needs a written answer in the manifest; "n/a" needs a reason after it.
PREMORTEM = {
    "PM01_cost": "Cost model: deciding stack (send, rent, +26.08/-16 bps, fee tier, own impact at the deciding stake); PumpSwap tape reserves PRE-trade with the virtual reserve V (~17.58 SOL), bonding POST-trade; E1 floor.",
    "PM02_fail_models": "Both fail models priced and deciding: flat 15% and pressure-fail at slope scale 1.",
    "PM03_exclusions": "Exclusion caps (5% feed-gap trades, 10% attempts in excluded hours) and the expected excluded share from the last closed days' gap counts (counts only).",
    "PM04_restart_feed": "Restart and feed handling: job limit vs window end, feed job end vs window end, planned restarts (none, or each listed), positions open at a restart.",
    "PM05_day1_dry_score": "Scorer dry run on day 1: job, input dir glob that matches this bot_id, counts only, exclusion shares printed.",
    "PM06_paper_live_equiv": "Paper -> live: the live executor path (DEC-032 §1 item 5) and the md5 decision-equivalence replay of the frozen decision log through it.",
    "PM07_seals": "Seals touched (DEC-031 §3, ledger D2/D3/D4, EXP-024/025): window hours vs every open seal; paths never opened.",
    "PM08_p0_count": "P0 structure-only count: rate per day, days not previously seen, drop rule, NOT_DECIDABLE gap rule.",
    "PM09_capacity": "Capacity: trades/day x stake x net edge and impact at the deciding and canary stakes (capacity-first).",
    "PM10_memory": "Memory: RSS slope x window + start <= cap; job memory <= host cap.",
    "PM11_lookahead": "Lookahead: tools/causal_harness.py verdict on the screen that selected this rule (report path@sha256), or the stream-mode decide().",
    "PM12_halt_alerts": "Guards and halts: who is alerted on a HALT / STOP and how fast (Q50 A3 HALT unseen 29.6 h).",
}
PREMORTEM_LLM = {"PM13_llm": "LLM book: real-model smoke at the pinned model IDs and sampling, parse-failure bound pinned before the smoke, API $/day x window under the cap."}


class Refused(Exception):
    pass


# ---------------------------------------------------------------- time helpers


def iso_to_s(iso: str) -> int:
    if not isinstance(iso, str) or not iso.endswith("Z"):
        raise ValueError(f"not a UTC ISO instant ending in Z: {iso!r}")
    return int(datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


def s_to_iso(s: int) -> str:
    return datetime.fromtimestamp(s, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get(m: dict, dotted: str, default: Any = None) -> Any:
    cur: Any = m
    for k in dotted.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


# ---------------------------------------------------------------- derived schedule (no hand arithmetic anywhere else)


def schedule(m: dict) -> dict:
    """Every instant and limit the pack uses, computed once from the manifest."""
    clock = iso_to_s(m["clock_iso"])
    clocks = [clock + d * 86_400 for d in m.get("slip_days", [0])]
    lead, startup, margin = int(m["register_lead_s"]), int(m["startup_s"]), int(m.get("start_margin_s", 600))
    H, tail = int(m["horizon_days"]), int(m.get("exit_tail_h", 25))
    out = {"clocks": []}
    for c in clocks:
        end_h = c + H * 86_400
        end_book = end_h + tail * 3_600
        run_start = c - startup - margin
        limit = end_book - run_start + 3_600  # one hour of slack for the job to start
        out["clocks"].append({"clock_iso": s_to_iso(c), "clock_ms": c * 1000, "register_deadline_iso": s_to_iso(c - lead), "register_deadline_s": c - lead,
                              "run_start_iso": s_to_iso(run_start), "run_start_s": run_start, "latest_start_s": c - startup, "end_h_iso": s_to_iso(end_h),
                              "end_book_iso": s_to_iso(end_book), "end_book_ms": end_book * 1000, "run_limit_s": limit})
    return out


# ---------------------------------------------------------------- lint (static)


def lint(m: dict, now_s: int | None = None) -> list[tuple[str, bool, str]]:
    """Return [(check_id, ok, detail)]. Each check names the late defect class it prevents (PLAN.md section 1)."""
    R: list[tuple[str, bool, str]] = []

    def ck(cid: str, ok: bool, detail: str) -> None:
        R.append((cid, bool(ok), detail))

    req = ["schema", "bot_id", "host_profile", "rule_names", "frozen_sha", "clock_iso", "horizon_days", "deciding_stake_sol", "register_lead_s", "startup_s", "host",
           "python", "pack_dir", "paths", "jobs", "smoke", "thresholds", "caps", "cost", "tests", "premortem"]
    missing = [k for k in req if k not in m]
    ck("M00_fields", not missing, f"missing: {missing}" if missing else "all required fields present")
    if missing:
        return R
    ck("M01_schema", m["schema"] == SCHEMA and m["host_profile"] in PROFILES, f"schema={m['schema']} profile={m['host_profile']}")
    ck("M02_frozen_sha", bool(HEX40.match(str(m["frozen_sha"]))), "40 lowercase hex (a full commit id; short shas went stale in FLOOR/HV4 records)")
    ok_id = bool(re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,63}", str(m["bot_id"]))) and "probe" not in m["bot_id"]
    ck("M03_bot_id", ok_id, "lowercase id, no 'probe' (arena tools refuse it)")
    # clock and schedule
    try:
        c = iso_to_s(m["clock_iso"])
        ok = c % 86_400 == 0
        ck("M04_clock", ok, f"clock {m['clock_iso']} is a 00:00Z boundary (DEC-031 Am.3 item 1)" if ok else "clock is not 00:00:00Z")
        slips = m.get("slip_days", [0])
        ck("M05_slips", isinstance(slips, list) and slips[:1] == [0] and len(slips) <= 2 and all(isinstance(x, int) and x >= 0 for x in slips),
           f"slip_days={slips} (the pinned clock first; at most one slip)")
        sch = schedule(m)
        for row in sch["clocks"]:
            ok = row["run_limit_s"] <= PLATFORM_MAX_LIMIT_S
            ck("M06_job_limit", ok, f"clock {row['clock_iso']}: run job needs {row['run_limit_s']} s (start {row['run_start_iso']} -> end {row['end_book_iso']}); cap {PLATFORM_MAX_LIMIT_S}")
        ck("M07_start_lag", int(m["startup_s"]) > 0 and int(m.get("start_margin_s", 600)) >= 300,
           f"run starts at clock - startup_s ({m['startup_s']}) - margin ({m.get('start_margin_s', 600)}): first tick at or before the clock (FLOOR E4 started 5 min before a 15-min start-up)")
        ck("M08_register_lead", 0 < int(m["register_lead_s"]) <= 86_400, f"register deadline = clock - {m['register_lead_s']} s = {sch['clocks'][0]['register_deadline_iso']} (computed, never typed)")
        if now_s is not None:
            dl = sch["clocks"][-1]["register_deadline_s"]
            ck("M09_deadline_open", now_s < dl, f"now {s_to_iso(now_s)} < last register deadline {s_to_iso(dl)}")
    except (ValueError, KeyError, TypeError) as ex:
        ck("M04_clock", False, f"bad clock/schedule fields: {ex}")
    # paths
    P = m["paths"]
    need = ["root", "ledger", "registry", "tape", "smoke_root"]
    bad = [k for k in need if not str(P.get(k, "")).startswith("/")]
    ck("M10_paths_abs", not bad, f"absolute paths required: {bad}" if bad else "all paths absolute")
    allp = [str(v) for v in P.values()] + [str(m["pack_dir"])]
    forb = [p for p in allp if any(f in p for f in FORBIDDEN_PATH_PARTS) or "probe" in p]
    ck("M11_paths_forbidden", not forb, f"sealed/forbidden or 'probe' paths: {forb}" if forb else "no sealed path, no 'probe'")
    sm = str(P.get("smoke_root", "")).rstrip("/")
    real = [str(P.get(k, "")).rstrip("/") for k in ("root", "ledger", "registry")]
    clash = sm == "" or any(r == sm or r.startswith(sm + "/") or sm.startswith(r + "/") for r in real)
    ck("M12_smoke_isolated", not clash, f"smoke root {sm} is apart from the book's root, ledger and registry")
    # thresholds
    T = m["thresholds"]
    tb = [k for k, v in T.items() if not (isinstance(v, dict) and set(v) == {"value", "label"} and v["label"] in LABELS
                                         and isinstance(v["value"], (int, float)) and not isinstance(v["value"], bool) and v["value"] >= 0)]
    ck("M13_thresholds", bool(T) and not tb, f"each threshold {{value>=0, label in {LABELS}}}; bad: {tb}" if tb or not T else f"{len(T)} thresholds, all labelled")
    # caps, cost, fail models
    caps = m["caps"]
    ck("M14_caps", caps == DEC031_CAPS, f"caps {caps} must equal {DEC031_CAPS} (a manifest never loosens them)")
    cost = m["cost"]
    fm = set(cost.get("fail_models", []))
    ck("M15_fail_models", fm == FAIL_MODELS, f"fail_models {sorted(fm)} == {sorted(FAIL_MODELS)}")
    legs = cost.get("latency_legs_s", [])
    ck("M16_legs", 1.9 in legs and 1.3 in legs and cost.get("deciding_leg_s") == 1.9, f"latency legs {legs}, deciding {cost.get('deciding_leg_s')} (DEC-031 §2: 1.9 s decides)")
    if cost.get("venue") == "pumpswap":
        ck("M17_virtual_reserve", cost.get("pumpswap_virtual_reserve") is True and cost.get("reserve_convention") == "pre-trade",
           "PumpSwap: V in pricing and PRE-trade reserves (the V~17.58 SOL error voided earlier P&L)")
    ck("M18_e1", cost.get("e1_floor") is True, "E1 floor carried (DEC-031 §1.4)")
    # memory and jobs
    J = m["jobs"]
    cap_gb = HOST_MEM_GB.get(m["host"])
    mems = J.get("mem_gb", {})
    ok = cap_gb is not None and all(isinstance(v, (int, float)) and 0 < v <= cap_gb for v in mems.values()) and {"register", "smoke", "run"} <= set(mems)
    ck("M19_job_memory", ok, f"host {m['host']} cap {cap_gb} GB; jobs {mems}")
    S = m["smoke"]
    ok = S.get("hours", 0) >= 2 and 0 < S.get("rss_cap_mb", 0) <= 1024 * mems.get("run", 0)
    ck("M20_smoke", ok, f"smoke >= 2 h (Am.6 1.3) and RSS cap {S.get('rss_cap_mb')} MB <= run job memory")
    # tests, pins
    pre = m.get("preconditions", list(DEFAULT_PRECONDITIONS))
    ck("M25_preconditions", set(DEFAULT_PRECONDITIONS) <= set(pre) and all(re.fullmatch(r"[a-z0-9-]+", x) for x in pre),
       f"register.sh refuses until PRECONDITION-<x>.ok exists for each of {sorted(pre)} (the defaults cannot be dropped)")
    ck("M21_tests", isinstance(m["tests"], list) and len(m["tests"]) > 0, "tests listed (collected at frozen_sha by `pin`; an empty glob is a FAIL, FLOOR L3)")
    # LLM
    llm = m.get("llm")
    if llm:
        ok = bool(llm.get("model_ids")) and isinstance(llm.get("sampling"), dict) and isinstance(llm.get("max_parse_fail_pct"), (int, float)) \
            and isinstance(llm.get("usd_per_day_cap"), (int, float)) and not llm.get("stub_smoke", False)
        ck("M22_llm", ok, "model_ids, sampling, max_parse_fail_pct, usd_per_day_cap pinned; real-model smoke (Am.6 1.3(f))")
    # pre-mortem
    items = dict(PREMORTEM, **(PREMORTEM_LLM if llm else {}))
    pm = m["premortem"]
    miss = [k for k in items if not str(pm.get(k, "")).strip()]
    ck("M23_premortem", not miss, f"unanswered: {miss}" if miss else f"{len(items)} items answered")
    # placeholders anywhere in the manifest
    blob = json.dumps(m)
    ph = sorted(set(PLACEHOLDER.findall(blob)))
    ck("M24_placeholders", not ph, f"placeholders in manifest: {ph}" if ph else "no FILL/TBD/TODO")
    return R


# ---------------------------------------------------------------- repo-backed: pins and constants at frozen_sha


def git(repo: str, *args: str, binary: bool = False) -> Any:
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True)
    if r.returncode != 0:
        raise Refused(f"git {' '.join(args)}: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout if binary else r.stdout.decode()


def blob_at(repo: str, sha: str, path: str) -> bytes:
    return git(repo, "show", f"{sha}:{path}", binary=True)


def const_at(repo: str, sha: str, path: str, name: str) -> Any:
    """A module-level `NAME = <literal>` (or a + of literal tuples) from the file at sha, by AST. Never imports the code."""
    tree = ast.parse(blob_at(repo, sha, path).decode())
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) and node.value is not None else []
        for t in targets:
            if isinstance(t, ast.Name) and t.id == name:
                return _literal(node.value, tree)
    raise Refused(f"{path}@{sha[:12]}: no module-level {name}")


def _literal(node: ast.AST, tree: ast.Module) -> Any:
    try:
        return ast.literal_eval(node)
    except ValueError:
        pass
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _literal(node.left, tree) + _literal(node.right, tree)
    if isinstance(node, ast.Name):
        for n in tree.body:
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == node.id for t in n.targets):
                return _literal(n.value, tree)
    raise Refused(f"not a literal: {ast.dump(node)[:80]}")


def pin(m: dict, repo: str) -> dict:
    sha = m["frozen_sha"]
    prof = PROFILES[m["host_profile"]]
    git(repo, "cat-file", "-e", f"{sha}^{{commit}}")
    remotes = [x.strip() for x in git(repo, "branch", "-r", "--contains", sha).splitlines() if x.strip()]
    if not remotes:
        raise Refused(f"frozen_sha {sha} is on no origin branch (DEC-031 §1.2: a commit on origin)")
    on_main = subprocess.run(["git", "-C", repo, "merge-base", "--is-ancestor", sha, "origin/main"]).returncode == 0
    paths = sorted(set(prof["base_pins"]) | set(m.get("pins", [])) | set(m["tests"]))
    pins = {p: hashlib.sha256(blob_at(repo, sha, p)).hexdigest() for p in paths}
    # host profile facts at the frozen sha
    reg_path, reg_name = prof["rule_registry"]
    registry = const_at(repo, sha, reg_path, reg_name)
    unknown = [r for r in m["rule_names"] if r not in registry]
    if unknown:
        raise Refused(f"rules {unknown} not in {reg_path}:{reg_name} at {sha[:12]}")
    cad = {k: const_at(repo, sha, p, n) for k, (p, n) in prof["cadence"].items()}
    per_hour = 3_600_000 // (int(cad["tick_ms"]) * int(cad["status_every_ticks"]))
    tk_path, tk_name = prof["threshold_keys"]
    tkeys = set(const_at(repo, sha, tk_path, tk_name))
    unk = sorted(set(m["thresholds"]) - tkeys)
    if unk:
        raise Refused(f"threshold keys {unk} unknown to {tk_path} at {sha[:12]} (known {sorted(tkeys)})")
    consts = []
    for c in m.get("constants", []):
        got = const_at(repo, sha, c["path"], c["name"])
        want = get(m, c["expect"][1:]) if isinstance(c.get("expect"), str) and c["expect"].startswith("$") else c.get("expect")
        if got != want:
            raise Refused(f"{c['path']}:{c['name']} = {got!r} at {sha[:12]}, manifest says {want!r} (DEC text vs code, FLOOR Am.9 item 9)")
        consts.append({"path": c["path"], "name": c["name"], "value": got})
    return {"schema": SCHEMA, "frozen_sha": sha, "on_origin_main": on_main, "origin_branches": remotes[:5], "pins": pins,
            "cadence": dict(cad, status_lines_per_hour=per_hour), "constants": consts, "template_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


# ---------------------------------------------------------------- sh lint (jobs run under sh)

BASHISMS = [(re.compile(r"PIPESTATUS"), "PIPESTATUS"), (re.compile(r"\[\["), "[["), (re.compile(r"set -o pipefail|pipefail"), "pipefail"),
            (re.compile(r"^\s*function\s", re.M), "function keyword"), (re.compile(r"(^|\s)source\s"), "source"), (re.compile(r"<<<"), "here-string"),
            (re.compile(r"\{\d+\.\.\d+\}"), "brace range"), (re.compile(r"^\s*\w+=\(", re.M), "array"), (re.compile(r"\$'"), "$'...'"), (re.compile(r"&>"), "&>")]


def lint_sh(text: str) -> list[str]:
    body = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    body = re.sub(r"<<'PYEOF'.*?^PYEOF$", "", body, flags=re.S | re.M)  # embedded python is not sh
    return [name for rx, name in BASHISMS if rx.search(body)]


# ---------------------------------------------------------------- render


def canon_json(o: Any) -> str:
    return json.dumps(o, sort_keys=True, indent=1) + "\n"


def _guards(m: dict, pk: str) -> str:
    return f"""set -u; date -u; hostname
F={m['frozen_sha']}; PACK={pk}
[ "$(git rev-parse HEAD)" = "$F" ] || {{ echo "REFUSED: checkout is not frozen_sha $F"; exit 1; }}
[ -z "$(git status --porcelain)" ] || {{ echo "REFUSED: dirty checkout"; exit 1; }}
[ "$(hostname)" = {m['host']} ] || {{ echo "REFUSED: host is not {m['host']}"; exit 1; }}
sha256sum -c --quiet "$PACK/PINS.sha256" || {{ echo "REFUSED: a pinned file differs from PINS.sha256 (stale pin)"; exit 1; }}
"""


def _env(m: dict, clock_ms: str, root: str, ledger: str, registry: str) -> str:
    p = PROFILES[m["host_profile"]]["env_prefix"]
    P = m["paths"]
    return (f'export {p}_REPO="$(pwd)" {p}_FROZEN_SHA="$F" {p}_CLOCK_START_MS="{clock_ms}" {p}="{" ".join(m["rule_names"])}" '
            f'{p}_FEED_DELAY_SLOTS={int(m.get("feed_delay_slots", 7))} {p}_ROOT="{root}" {p}_LEDGER="{ledger}" {p}_REGISTRY="{registry}" '
            f'{p}_TAPE="{P["tape"]}" PYTHON="{m["python"]}"')


def _clock_case(sch: dict) -> str:
    arms = []
    for r in sch["clocks"]:
        arms.append(f'  {r["clock_iso"]}) CLOCK_MS={r["clock_ms"]}; DEADLINE_S={r["register_deadline_s"]}; RUN_START_S={r["run_start_s"]}; '
                    f'LATEST_START_S={r["latest_start_s"]}; END_MS={r["end_book_ms"]}; LIMIT_S={r["run_limit_s"]};;')
    return 'CLOCK_ISO=${CLOCK_ISO:-' + sch["clocks"][0]["clock_iso"] + '}\ncase "$CLOCK_ISO" in\n' + "\n".join(arms) + \
        '\n  *) echo "REFUSED: CLOCK_ISO must be the pinned clock or its one slip"; exit 1;;\nesac\nNOW=$(date +%s)\n'


def render(m: dict, pins: dict, out: Path) -> dict[str, str]:
    if out.exists() and any(out.iterdir()):
        raise Refused(f"{out} is not empty (a pack is written once; render a new dir)")
    prof = PROFILES[m["host_profile"]]
    sch = schedule(m)
    pk = m["pack_dir"]
    P = m["paths"]
    entry = prof["entry"]
    pfx = prof["env_prefix"]
    files: dict[str, str] = {}
    files["MANIFEST.json"] = canon_json(m)
    files["pins.json"] = canon_json(pins)
    files["thresholds.json"] = json.dumps(m["thresholds"], sort_keys=True, separators=(",", ":"))
    files["PINS.sha256"] = "".join(f"{h}  {p}\n" for p, h in sorted(pins["pins"].items()))
    thr_sha = hashlib.sha256(files["thresholds.json"].encode()).hexdigest()
    per_hour = pins["cadence"]["status_lines_per_hour"]
    S = m["smoke"]
    smoke_min_status = max(1, int(per_hour * S["hours"] * 0.8))
    bot = m["bot_id"]
    pat = prof["process_pattern"]

    files["register.sh"] = f"""# register.sh: write {bot}'s registry line before the clock (DEC-031 §1.2, Am.3 item 1). Generated by tools/fasttrack_book.py; do not edit (re-render).
# MiScusi: machine {m['host']}, git_ref {m['frozen_sha']}, mem {m['jobs']['mem_gb']['register']} GB, limit 10 min. Run: CLOCK_ISO=<clock> sh {pk}/register.sh
{_guards(m, pk)}{_clock_case(sch)}[ "$NOW" -lt "$DEADLINE_S" ] || {{ echo "REFUSED: at or after the register deadline $(date -u -d @$DEADLINE_S +%FT%TZ); the clock slips once or the book is not registered"; exit 1; }}
for f in {" ".join("PRECONDITION-" + x + ".ok" for x in m.get("preconditions", DEFAULT_PRECONDITIONS))}; do [ -s "$PACK/$f" ] || {{ echo "REFUSED: $PACK/$f missing or empty (each holds the job id / notebook id / verdict line it stands for)"; exit 1; }}; done
REG={P['registry']}
if [ -e "$REG" ] && grep -q '"{bot}"' "$REG"; then echo "REFUSED: {bot} already registered"; exit 1; fi
{_env(m, '$CLOCK_MS', P['root'], P['ledger'], P['registry'])}
{pfx}_MODE=register sh {entry}; RC=$?
echo "register_rc=$RC clock=$CLOCK_ISO thresholds_sha256={thr_sha} H_days={m['horizon_days']}"
exit "$RC"
"""

    files["run.sh"] = f"""# run.sh: the book {bot}, ONE process for the whole window. Generated by tools/fasttrack_book.py; do not edit (re-render).
# MiScusi: machine {m['host']}, git_ref {m['frozen_sha']}, mem {m['jobs']['mem_gb']['run']} GB, resumable true, time limit = LIMIT_S below (<= {PLATFORM_MAX_LIMIT_S} s).
# Submit (scheduled or chained with after) so it starts at RUN_START (clock - startup_s - margin): its first tick is at or before the clock.
{_guards(m, pk)}{_clock_case(sch)}[ "$NOW" -le "$LATEST_START_S" ] || {{ echo "REFUSED: started after clock - startup_s; the first tick would be after the clock (start lag)"; exit 1; }}
grep -q '"{bot}"' {P['registry']} || {{ echo "REFUSED: {bot} not registered"; exit 1; }}
pgrep -f '{pat}' >/dev/null && {{ echo "REFUSED: a host process is alive (one process per frozen_sha)"; exit 1; }}
while [ "$(date +%s)" -lt "$RUN_START_S" ]; do sleep 5; done
echo "run: clock=$CLOCK_ISO end_ms=$END_MS limit_s=$LIMIT_S"
{_env(m, '$CLOCK_MS', P['root'], P['ledger'], P['registry'])} {pfx}_END_MS="$END_MS"
{pfx}_MODE=run exec sh {entry}
"""

    files["smoke.sh"] = f"""# smoke.sh: DEC-031 Am.6 item 1.3 live smoke of {bot} at frozen_sha on a SCRATCH root/registry for {S['hours']} h. Never counted, never an input to a read.
# Generated by tools/fasttrack_book.py. MiScusi: machine {m['host']}, git_ref {m['frozen_sha']}, mem {m['jobs']['mem_gb']['smoke']} GB, limit {S['hours'] * 60 + 60} min.
# Post blocking-list.txt to the notebook BEFORE submitting. Decision and ledger rows of the smoke are never opened.
{_guards(m, pk)}SMK={P['smoke_root']}
[ ! -e "$SMK/rules" ] && [ ! -e "$SMK/bots.json" ] || {{ echo "REFUSED: smoke root not fresh"; exit 1; }}
pgrep -f '{pat}' >/dev/null && {{ echo "REFUSED: a host process is alive"; exit 1; }}
mkdir -p "$SMK" || exit 1
NOW=$(date +%s); CLOCK_MS=$(( (NOW / 60 + 6) * 60 * 1000 )); END_MS=$(( CLOCK_MS + {S['hours']} * 3600000 + 600000 ))
{_env(m, '$CLOCK_MS', '$SMK/rules', '$SMK/ledger', '$SMK/bots.json')}
{pfx}_MODE=register sh {entry} > "$SMK/register.out" 2>&1 || {{ echo "smoke register FAILED"; cat "$SMK/register.out"; exit 1; }}
{pfx}_MODE=run {pfx}_END_MS="$END_MS" sh {entry} > "$SMK/run.out" 2> "$SMK/run.err" &
PID=$!
while kill -0 "$PID" 2>/dev/null; do
  R=$(ps -o rss= -p "$PID" 2>/dev/null | tr -d ' '); [ -n "$R" ] && echo "{{\\"t_ms\\":$(( $(date +%s) * 1000 )),\\"rss_mb\\":$(( R / 1024 ))}}" >> "$SMK/rss.jsonl"
  sleep 60
done
wait "$PID"; RC=$?
echo "smoke run_rc=$RC at $(date -u +%FT%TZ)"
sh "$PACK/smoke-check.sh" "$SMK"; exit $?
"""

    files["smoke-check.sh"] = f"""# smoke-check.sh SMK: the blocking list of blocking-list.txt, counts and flags only (no ledger / decision row opened). Generated; do not edit.
set -u; SMK=$1; PY={m['python']}; FAIL=0
A=$(grep -c '"bot_id":"{bot}"' "$SMK/register.out"); echo "(a) register lines: $A (need 1)"; [ "$A" -eq 1 ] || FAIL=1
B=$(grep -c '{prof['status_marker']}' "$SMK/run.out"); echo "(b) status lines: $B (need >= {smoke_min_status}: {per_hour}/h from code constants x {S['hours']} h x 0.8)"; [ "$B" -ge {smoke_min_status} ] || FAIL=1
C=$(cat "$SMK/run.out" "$SMK/run.err" | grep -c Traceback); echo "(c) tracebacks: $C (need 0)"; [ "$C" -eq 0 ] || FAIL=1
"$PY" -I - "$SMK/rss.jsonl" {S['rss_cap_mb']} {int(m['horizon_days']) * 24 + int(m.get('exit_tail_h', 25))} <<'PYEOF' || FAIL=1
import json, sys
rows = [json.loads(x) for x in open(sys.argv[1]) if x.strip()]; cap = float(sys.argv[2]); hours = float(sys.argv[3])
pts = [(r["t_ms"] / 3.6e6, r["rss_mb"]) for r in rows]
pts = [p for p in pts if pts and p[0] >= pts[0][0] + 0.5]
if len(pts) < 6:
    print("(d) INCOMPLETE: < 6 RSS samples after start + 30 min"); sys.exit(1)
n = len(pts); mt = sum(p[0] for p in pts) / n; mr = sum(p[1] for p in pts) / n
s = sum((p[0] - mt) * (p[1] - mr) for p in pts) / sum((p[0] - mt) ** 2 for p in pts)
proj = pts[0][1] + max(s, 0.0) * hours
print("(d) R0=%.0f MB slope=%.3f MB/h projected at %.0f h = %.0f MB (cap %.0f)" % (pts[0][1], s, hours, proj, cap))
sys.exit(0 if proj <= cap else 1)
PYEOF
echo "(e) md5 decision-equivalence: see PRECONDITION-md5.ok (a pinned replay, run separately)"
[ "$FAIL" -eq 0 ] && echo "SMOKE: PASS" || echo "SMOKE: FAIL"
exit "$FAIL"
"""

    llm = m.get("llm")
    bl = [f"Blocking list for the smoke of {bot} at {m['frozen_sha']} (DEC-031 Am.6 item 1.3), written before the smoke starts:",
          "(a) the process starts and registers: exactly 1 registry line in the scratch registry;",
          f"(b) status lines >= {smoke_min_status} over {S['hours']} h (the host emits {per_hour} per hour: TICK_MS x STATUS_EVERY_TICKS at the frozen sha; 0.8 slack);",
          "(c) no Traceback in run.out/run.err; exit 0 at END;",
          f"(d) RSS: R0 + max(OLS slope from start+30 min, 0) x {int(m['horizon_days']) * 24 + int(m.get('exit_tail_h', 25))} h <= {S['rss_cap_mb']} MB;",
          "(e) md5 decision-equivalence on the pinned replay where a port exists (PRECONDITION-md5.ok)."]
    if llm:
        bl.append(f"(f) LLM: real model {llm['model_ids']} with sampling {json.dumps(llm['sampling'], sort_keys=True)}; parse failures <= {llm['max_parse_fail_pct']}% of calls.")
    bl.append("Report-only (never blocking): scan latency, feed-sample coverage, call rates. Counts, timings and flags only.")
    files["blocking-list.txt"] = "\n".join(bl) + "\n"

    c0 = sch["clocks"][0]
    jobs = [
        {"name": f"{bot} smoke", "machine": m["host"], "git_ref": m["frozen_sha"], "command": f"sh {pk}/smoke.sh", "mem_gb": m["jobs"]["mem_gb"]["smoke"],
         "cpus": m["jobs"].get("cpus", 1), "time_limit_min": S["hours"] * 60 + 60, "resumable": False, "role": "ops"},
        {"name": f"{bot} register", "machine": m["host"], "git_ref": m["frozen_sha"], "command": f"CLOCK_ISO={c0['clock_iso']} sh {pk}/register.sh",
         "mem_gb": m["jobs"]["mem_gb"]["register"], "cpus": 1, "time_limit_min": 10, "resumable": False, "role": "ops",
         "not_after_iso": c0["register_deadline_iso"]},
        {"name": f"{bot} run", "machine": m["host"], "git_ref": m["frozen_sha"], "command": f"CLOCK_ISO={c0['clock_iso']} sh {pk}/run.sh",
         "mem_gb": m["jobs"]["mem_gb"]["run"], "cpus": m["jobs"].get("cpus", 1), "time_limit_min": -(-c0["run_limit_s"] // 60), "resumable": True,
         "role": "ops", "after": ["<register job id>"], "start_at_iso": c0["run_start_iso"]},
    ]
    files["jobs.json"] = canon_json({"schema": SCHEMA, "bot_id": bot, "jobs": jobs})

    rows = [f"| `{p}` | `{h}` |" for p, h in sorted(pins["pins"].items())]
    clocks = [f"| {r['clock_iso']} | {r['register_deadline_iso']} | {r['run_start_iso']} | {r['end_h_iso']} | {r['end_book_iso']} | {r['run_limit_s']} |" for r in sch["clocks"]]
    thr = [f"| `{k}` | {v['value']} | {v['label']} |" for k, v in sorted(m["thresholds"].items())]
    pm_items = dict(PREMORTEM, **(PREMORTEM_LLM if llm else {}))
    pm = [f"| {k} | {pm_items[k]} | {m['premortem'][k]} |" for k in pm_items]
    files["REGISTRATION.md"] = "\n".join([
        f"# Registration record: `{bot}` (generated by tools/fasttrack_book.py; template sha256 `{pins['template_sha256']}`)",
        "",
        "Records only. Nothing here says the book makes money. No book has cleared the promotion gate. The gate, the deciding cell, the caps and every seal are unchanged.",
        "",
        "| Field | Value |", "| --- | --- |",
        f"| bot_id | `{bot}` |", f"| rules | {', '.join(m['rule_names'])} |", f"| host profile | `{m['host_profile']}` on {m['host']} |",
        f"| frozen_sha | `{m['frozen_sha']}` (on origin/main: {pins['on_origin_main']}) |", f"| deciding stake | {m['deciding_stake_sol']} SOL, 1.9 s leg, flat 15% and pressure |",
        f"| horizon | {m['horizon_days']} d, exit tail {m.get('exit_tail_h', 25)} h |", f"| caps | {m['caps']['feed_gap_pct']}% feed-gap trades; {m['caps']['excluded_hour_pct']}% attempts in excluded hours |",
        f"| thresholds.json sha256 | `{thr_sha}` |", f"| DEC refs | {', '.join(m.get('dec_refs', []))} |",
        "", "## Clocks (computed)", "", "| clock | register deadline | run start | END_H | book end | run job limit s |", "| --- | --- | --- | --- | --- | --- |", *clocks,
        "", "## Pins at frozen_sha (`git show <sha>:<path> | sha256sum`)", "", "| path | sha256 |", "| --- | --- |", *rows,
        "", f"Cadence from code: {pins['cadence']}.", f"Constants checked against the manifest: {pins['constants']}.",
        "", "## Hour-health thresholds (Am.6 item 2.3)", "", "| key | value | label |", "| --- | --- | --- |", *thr,
        "", "## Smoke blocking list", "", "```", files["blocking-list.txt"].rstrip(), "```",
        "", "## Pre-mortem", "", "| item | question | answer |", "| --- | --- | --- |", *pm,
        "", "## Runtime facts (filled from job and notebook output only, after each step)", "",
        "- RUNTIME smoke job id, verdict line, notebook id of the blocking list", "- RUNTIME PRECONDITION-*.ok files and their sha256",
        "- RUNTIME register job id, printed registry line, notebook copy id", "- RUNTIME run job id and start instant", ""])
    files["SHA256SUMS"] = "".join(f"{hashlib.sha256(files[n].encode()).hexdigest()}  {n}\n" for n in sorted(files))
    bad = {n: lint_sh(t) for n, t in files.items() if n.endswith(".sh") and lint_sh(t)}
    if bad:
        raise Refused(f"generated scripts carry bash-isms: {bad}")
    out.mkdir(parents=True, exist_ok=True)
    for n, t in files.items():
        (out / n).write_text(t)
    return files


def verify(pack: Path, repo: str) -> list[tuple[str, bool, str]]:
    R = []
    sums = {}
    for ln in (pack / "SHA256SUMS").read_text().splitlines():
        h, n = ln.split("  ", 1)
        sums[n] = h
    for n, h in sorted(sums.items()):
        got = hashlib.sha256((pack / n).read_bytes()).hexdigest()
        R.append((f"V1_pack:{n}", got == h, got[:16]))
    m = json.loads((pack / "MANIFEST.json").read_text())
    old = json.loads((pack / "pins.json").read_text())
    try:
        now = pin(m, repo)
        diff = sorted(p for p in set(old["pins"]) | set(now["pins"]) if old["pins"].get(p) != now["pins"].get(p))
        R.append(("V2_pins_at_frozen_sha", not diff, f"differ: {diff}" if diff else f"{len(now['pins'])} pins match"))
    except Refused as ex:
        R.append(("V2_pins_at_frozen_sha", False, str(ex)))
    return R


def _print(res: list[tuple[str, bool, str]]) -> bool:
    ok = all(r[1] for r in res)
    for cid, good, d in res:
        print(f"{'PASS' if good else 'FAIL'} {cid}: {d}")
    print("LINT: PASS" if ok else "LINT: FAIL")
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("lint"); a1.add_argument("manifest"); a1.add_argument("--now-iso")
    a2 = sub.add_parser("pin"); a2.add_argument("manifest"); a2.add_argument("--repo", required=True); a2.add_argument("--out")
    a3 = sub.add_parser("render"); a3.add_argument("manifest"); a3.add_argument("--repo", required=True); a3.add_argument("--out", required=True); a3.add_argument("--now-iso")
    a4 = sub.add_parser("verify"); a4.add_argument("pack"); a4.add_argument("--repo", required=True)
    a5 = sub.add_parser("lint-sh"); a5.add_argument("files", nargs="+")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "lint-sh":
            bad = {f: lint_sh(Path(f).read_text()) for f in a.files}
            for f, b in bad.items():
                print(f"{'FAIL' if b else 'PASS'} {f}: {b}")
            return 1 if any(bad.values()) else 0
        if a.cmd == "verify":
            return 0 if _print(verify(Path(a.pack), a.repo)) else 1
        m = json.loads(Path(a.manifest).read_text())
        now = iso_to_s(a.now_iso) if getattr(a, "now_iso", None) else None
        if a.cmd == "lint":
            return 0 if _print(lint(m, now)) else 1
        if a.cmd == "pin":
            p = pin(m, a.repo)
            txt = canon_json(p)
            Path(a.out).write_text(txt) if a.out else sys.stdout.write(txt)
            return 0
        res = lint(m, now if now is not None else int(datetime.now(timezone.utc).timestamp()))
        if not _print(res):
            return 1
        files = render(m, pin(m, a.repo), Path(a.out))
        print(f"RENDERED {len(files)} files to {a.out}")
        return 0
    except Refused as ex:
        print(f"REFUSED: {ex}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
