#!/usr/bin/env python3
"""H5 shadow, report-only tipauth field: the pre-declared RPC census (acceptance item 7).

Spec: PR #610, quant-proof "Ruling 3" (comment 6121547427, section 3 item 1), with the item-7 text of comment 6121123397 and the B' item 7
of comment 6120949717. Where a definition is ambiguous this tool takes the STRICTER reading; the choices are listed in AMBIGUITIES.

It is a COUNT of existence on the finalized chain. No outcome, fill, P&L, price or tip-tape field is read. No key file and no dot-env file is
read; no API key is used (the endpoint is the public mainnet-beta RPC, 0 Helius credits). The ONLY network use is in `ask` / `reask`, through
an injectable transport (tests use a fake one).

Sets (fixed by `plan`, from the item-4 count tool's JSON output plus, for C only, the shadow dir's pool close records):
  A  every distinct signature in the would-pass triggers' `dropped` lists.
  B  distinct signatures of the ws >= 1, tipauth-0, no-gap closes' `dropped` lists that are not in A, ordered by sha256 of the signature
     (hex, ascending), first 50.
  C  30 controls: `base_unmatched_union` rows marked "both" or "tip" on the unsealed pool close records (window of the item-4 read) of pools
     that appear in A or B, signature not in A or B, same ordering, first 30.

  python3 tools/h5_tipauth_census.py plan   --item4-json F --shadow-dir D --out-dir O
  python3 tools/h5_tipauth_census.py ask    --out-dir O
  python3 tools/h5_tipauth_census.py reask  --out-dir O
  python3 tools/h5_tipauth_census.py report --out-dir O
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

TOOL = "h5_tipauth_census"
SPEC = {"ruling3": "PR #610 comment 6121547427", "round3": "PR #610 comment 6121123397", "round2": "PR #610 comment 6120949717"}

# ---- the ONE place that names the item-4 output keys this tool depends on (PR #613; re-check when it merges) ------------------------------
ITEM4 = {
    "tool": "tool",                                   # top level; must equal "h5_tipauth_item4"
    "census_input": "item7_census_input",             # top level
    "wp_triggers": "would_pass_triggers",             # under census_input: list of rows
    "closes": "tipauth0_ws_ge1_nogap_closes",         # under census_input: list of rows
    "dropped": "dropped",                             # in each row: [[signature, event_index, slot], ...]
    "dropped_n": "dropped_n",                         # in each row
    "pool": "pool",                                   # in each row
    "window": "window",                               # top level
    "origin": "origin_t_ms",                          # under window
    "until": "until_ms",                              # top level
}
ITEM4_TOOL_NAME = "h5_tipauth_item4"
# shadow close-record keys (tools/h5_shadow.py, pool close records)
SHADOW = {"type": "type", "t_ms": "t_ms", "sealed": "sealed", "h5_look2_sealed": "h5_look2_sealed", "pool": "pool",
          "union": "base_unmatched_union"}
ALLOWED_ITEM4_KEYS = frozenset(ITEM4.values())
ALLOWED_SHADOW_KEYS = frozenset(SHADOW.values())

RPC_URL = "https://api.mainnet-beta.solana.com"  # public; fixed in code, no environment override, no key
REQUEST_PACE_S = 0.5  # at most 2 rps
REQUEST_TIMEOUT_S = 20
REASK_MIN_MS = 600_000  # 10 minutes
B_MAX, C_MAX = 50, 30
TRIGGER_ANSWERED_MIN = 0.80
B_DEFINITIVE_MIN = 50
CONTROL_FOUND_MIN = 0.95
CONTROL_ANSWERED_MIN = 20
PLAN_NAME, STATE_NAME, LOG_NAME, REPORT_NAME = "census_plan.json", "census_state.jsonl", "census_requests.jsonl", "census_report.json"

AMBIGUITIES = [
    ("R1", "'a not-found re-asked at least 10 minutes later'",
     "not_found needs TWO null answers whose asked_ms differ by >= 600,000 ms. One null is `pending`. `reask` therefore re-queries `error` rows AND single-null rows "
     "(never found, never twice-null), and refuses unless >= 10 minutes passed since the last ask/reask."),
    ("R2", "'at least 80% of would-pass triggers with a definitive answer for every listed signature'",
     "A trigger counts only if it is fully enumerated (dropped is a list and len(dropped) == dropped_n), has at least one listed signature, and every listed signature "
     "is definitive (found or not_found). A trigger with an empty list, or with len != dropped_n, is NOT answered."),
    ("R3", "'any A or B signature found => no proposal'",
     "Found is false-final. The no-find threshold is true only if no A/B signature is found AND every A and B signature is definitive; otherwise null. Empty A and B: null."),
    ("R4", "'at least 50 definitive answers in B'",
     "Ruling 3 says 'B definitive >= 50, else short'. A B smaller than 50 can never reach 50: the result is null (short), never true and never false."),
    ("R5", "'pools that appear in A or B' (control pools)",
     "Pools of the would-pass triggers, plus pools of the closes whose dropped list holds a SELECTED B signature. Controls exclude A and EVERY B candidate "
     "(all signatures of the tipauth-0 closes), not just the selected 50."),
    ("R6", "'controls found on at least 95% of answered'", "answered = definitive (found + not_found); null (short) below 20 answered; false below 95%."),
    ("R7", "Sealed flags on close records", "Fail closed: any value other than None/False skips the record."),
    ("R8", "Window of the control read", "[window.origin_t_ms, until_ms] of the item-4 JSON; both are required in it, else plan refuses."),
    ("R9", "Task text 'reask re-queries only error rows' vs Ruling 3 'not-found re-asked'", "The union is implemented (R1); a found row is never re-asked."),
]

# ---- helpers ----------------------------------------------------------------------------------------------------------------------------


def isint(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def flag(x: Any) -> bool:
    return not (x is None or x is False)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sig_key(sig: str) -> str:
    return hashlib.sha256(sig.encode("utf-8")).hexdigest()


def dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=True) + "\n"


def write_new(path: Path, text: str) -> str:
    """Write a file that must not exist; return its sha256."""
    data = text.encode("utf-8")
    with open(path, "xb") as fh:
        fh.write(data)
    return sha256_bytes(data)


def sig_list(entries: Any) -> list[str]:
    """Distinct signatures of a `dropped` list ([signature, event_index, slot] entries); malformed entries are ignored here."""
    out: list[str] = []
    if isinstance(entries, list):
        for e in entries:
            if isinstance(e, list) and e and isinstance(e[0], str) and e[0] not in out:
                out.append(e[0])
    return out


def fully_enumerated(row: Any) -> bool:
    d, n = row.get(ITEM4["dropped"]), row.get(ITEM4["dropped_n"])
    return isinstance(d, list) and isint(n) and len(d) == n and len(d) >= 1 and len(sig_list(d)) >= 1


# ---- selection --------------------------------------------------------------------------------------------------------------------------


def select_AB(item4: Any) -> dict:
    top = item4 if isinstance(item4, dict) else {}
    if top.get(ITEM4["tool"]) != ITEM4_TOOL_NAME:
        raise SystemExit("refused: the input is not the item-4 count tool's output")
    ci = top.get(ITEM4["census_input"])
    if not isinstance(ci, dict):
        raise SystemExit("refused: item-4 output has no census input block")
    wps, closes = ci.get(ITEM4["wp_triggers"]), ci.get(ITEM4["closes"])
    if not isinstance(wps, list) or not isinstance(closes, list):
        raise SystemExit("refused: item-4 census input lists are missing")
    A: set[str] = set()
    triggers = []
    wp_pools: set[str] = set()
    for r in wps:
        if not isinstance(r, dict):
            continue
        sigs = sig_list(r.get(ITEM4["dropped"]))
        A.update(sigs)
        triggers.append({"sigs": sorted(sigs), "fully_enumerated": fully_enumerated(r)})
        p = r.get(ITEM4["pool"])
        if isinstance(p, str):
            wp_pools.add(p)
    cand: dict[str, set[str]] = {}  # B candidate signature -> pools of the closes listing it
    for r in closes:
        if not isinstance(r, dict):
            continue
        p = r.get(ITEM4["pool"])
        for s in sig_list(r.get(ITEM4["dropped"])):
            cand.setdefault(s, set())
            if isinstance(p, str):
                cand[s].add(p)
    B = sorted((s for s in cand if s not in A), key=sig_key)[:B_MAX]
    pools = set(wp_pools)
    for s in B:
        pools |= cand[s]
    win = top.get(ITEM4["window"])
    origin = win.get(ITEM4["origin"]) if isinstance(win, dict) else None
    until = top.get(ITEM4["until"])
    if not isint(origin) or not isint(until):
        raise SystemExit("refused: item-4 output has no integer window origin / until_ms")
    return {"A": sorted(A), "B": B, "triggers": triggers, "b_candidates": sorted(cand), "control_pools": sorted(pools), "origin": origin, "until": until}


def read_controls(shadow_dir: Path, pools: set[str], origin: int, until: int, exclude: set[str], hook: Callable | None = None) -> list[str]:
    found: set[str] = set()
    for f in sorted(shadow_dir.iterdir()):
        if not (f.name.startswith("h5-shadow-") and f.name.endswith(".jsonl")):
            continue
        with open(f, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line, object_hook=hook)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or rec.get(SHADOW["type"]) != "pool":
                    continue
                t = rec.get(SHADOW["t_ms"])
                if not isint(t) or t < origin or t > until:
                    continue
                if flag(rec.get(SHADOW["sealed"])) or flag(rec.get(SHADOW["h5_look2_sealed"])):
                    continue
                p = rec.get(SHADOW["pool"])
                if not isinstance(p, str) or p not in pools:
                    continue
                rows = rec.get(SHADOW["union"])
                if not isinstance(rows, list):
                    continue
                for e in rows:
                    if isinstance(e, list) and len(e) >= 4 and isinstance(e[0], str) and e[3] in ("both", "tip") and e[0] not in exclude:
                        found.add(e[0])
    return sorted(found, key=sig_key)[:C_MAX]


def build_plan(item4: Any, shadow_dir: Path, item4_sha: str, shadow_hook: Callable | None = None) -> dict:
    sel = select_AB(item4)
    exclude = set(sel["A"]) | set(sel["b_candidates"])
    C = read_controls(shadow_dir, set(sel["control_pools"]), sel["origin"], sel["until"], exclude, shadow_hook)
    sets = {"A": sel["A"], "B": sel["B"], "C": C}
    return {"tool": TOOL, "spec": SPEC, "item4_sha256": item4_sha, "window": {"origin_t_ms": sel["origin"], "until_ms": sel["until"]},
            "sets": sets, "sizes": {k: len(v) for k, v in sets.items()}, "call_cap": sum(len(v) for v in sets.values()),
            "b_candidates_n": len(sel["b_candidates"]), "control_pools_n": len(sel["control_pools"]),
            "triggers": sel["triggers"], "selection_sha256": sha256_bytes(dumps(sets).encode("utf-8"))}


# ---- RPC ---------------------------------------------------------------------------------------------------------------------------------


def request_payload(sig: str) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "method": "getTransaction",
            "params": [sig, {"commitment": "finalized", "maxSupportedTransactionVersion": 0, "encoding": "json"}]}


def http_transport(url: str, payload: dict, timeout: float) -> Any:
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "User-Agent": "mal-h5-census/1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https URL)
        return json.loads(resp.read().decode("utf-8"))


def interpret(resp: Any) -> dict:
    """found / null / error. An error, a timeout, a malformed answer is NEVER a null."""
    if not isinstance(resp, dict) or "error" in resp or "result" not in resp:
        return {"result": "error", "err": "rpc_error" if isinstance(resp, dict) and "error" in resp else "malformed"}
    r = resp["result"]
    if r is None:
        return {"result": "null"}
    if isinstance(r, dict) and isint(r.get("slot")):
        return {"result": "found", "slot": r["slot"]}
    return {"result": "error", "err": "malformed_result"}


def ask_one(sig: str, transport: Callable, url: str = RPC_URL) -> dict:
    try:
        return interpret(transport(url, request_payload(sig), REQUEST_TIMEOUT_S))
    except Exception as e:  # noqa: BLE001  (timeouts, HTTP errors, bad JSON)
        return {"result": "error", "err": type(e).__name__}


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def append_jsonl(path: Path, row: dict) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def load_plan(out_dir: Path) -> dict:
    p = out_dir / PLAN_NAME
    if not p.exists():
        raise SystemExit("refused: no plan")
    plan = json.loads(p.read_text(encoding="utf-8"))
    if sha256_bytes(dumps(plan["sets"]).encode("utf-8")) != plan.get("selection_sha256"):
        raise SystemExit("refused: plan selection hash mismatch")
    return plan


def all_sigs(plan: dict) -> list[tuple[str, str]]:
    return [(s, k) for k in ("A", "B", "C") for s in plan["sets"][k]]


def run_queries(out_dir: Path, sigs: list[str], cap: int, transport: Callable, sleep: Callable, now_ms: Callable, round_name: str) -> int:
    if len(sigs) > cap:
        raise SystemExit("refused: more queries than the call cap")
    n = 0
    for i, s in enumerate(sigs):
        if i:
            sleep(REQUEST_PACE_S)
        t = now_ms()
        ans = ask_one(s, transport)
        n += 1
        append_jsonl(out_dir / LOG_NAME, {"asked_ms": t, "sig": s, "round": round_name, "result": ans["result"]})
        append_jsonl(out_dir / STATE_NAME, {"asked_ms": t, "sig": s, "round": round_name, **ans})
    return n


def cmd_ask(out_dir: Path, transport: Callable = http_transport, sleep: Callable = time.sleep, now_ms: Callable = lambda: int(time.time() * 1000)) -> int:
    plan = load_plan(out_dir)
    if read_jsonl(out_dir / STATE_NAME):
        raise SystemExit("refused: ask already ran (use reask)")
    sigs = [s for s, _ in all_sigs(plan)]
    return run_queries(out_dir, sigs, plan["call_cap"], transport, sleep, now_ms, "ask")


def classify(answers: list[dict]) -> str:
    """found | not_found | error | pending."""
    if any(a["result"] == "found" for a in answers):
        return "found"
    nulls = sorted(a["asked_ms"] for a in answers if a["result"] == "null")
    if len(nulls) >= 2 and nulls[-1] - nulls[0] >= REASK_MIN_MS:
        return "not_found"
    if nulls:
        return "pending"
    return "error"


def states_by_sig(rows: list[dict]) -> dict[str, list[dict]]:
    d: dict[str, list[dict]] = {}
    for r in rows:
        d.setdefault(r["sig"], []).append(r)
    return d


def cmd_reask(out_dir: Path, transport: Callable = http_transport, sleep: Callable = time.sleep, now_ms: Callable = lambda: int(time.time() * 1000)) -> int:
    plan = load_plan(out_dir)
    rows = read_jsonl(out_dir / STATE_NAME)
    if not rows:
        raise SystemExit("refused: ask has not run")
    if now_ms() - max(r["asked_ms"] for r in rows) < REASK_MIN_MS:
        raise SystemExit("refused: less than 10 minutes since the last ask/reask")
    by = states_by_sig(rows)
    todo = [s for s, _ in all_sigs(plan) if classify(by.get(s, [])) in ("error", "pending")]
    return run_queries(out_dir, todo, plan["call_cap"], transport, sleep, now_ms, "reask")


# ---- report ------------------------------------------------------------------------------------------------------------------------------


def thr(met: bool | None, **kw: Any) -> dict:
    return {"met": met, **kw}


def report(plan: dict, rows: list[dict]) -> dict:
    by = states_by_sig(rows)
    cls = {s: classify(by.get(s, [])) for s, _ in all_sigs(plan)}
    A, B, C = plan["sets"]["A"], plan["sets"]["B"], plan["sets"]["C"]
    definitive = lambda s: cls[s] in ("found", "not_found")  # noqa: E731
    counts = {k: {c: sum(1 for s in plan["sets"][k] if cls[s] == c) for c in ("found", "not_found", "error", "pending")} for k in ("A", "B", "C")}
    found_ab = sorted(s for s in A + B if cls[s] == "found")
    if found_ab:
        t1 = thr(False, found=found_ab, note="no proposal on this definition")
    elif not (A + B) or not all(definitive(s) for s in A + B):
        t1 = thr(None, label="short", note="empty class or an A/B signature not yet definitive")
    else:
        t1 = thr(True)
    trig = plan["triggers"]
    answered = sum(1 for t in trig if t["fully_enumerated"] and all(definitive(s) for s in t["sigs"]))
    share = None if not trig else round(answered / len(trig), 6)
    t2 = thr(None if not trig else answered / len(trig) >= TRIGGER_ANSWERED_MIN, n=len(trig), answered=answered, share=share, min=TRIGGER_ANSWERED_MIN,
             **({"label": "short"} if not trig else {}))
    bdef = sum(1 for s in B if definitive(s))
    t3 = thr(True if bdef >= B_DEFINITIVE_MIN else None, definitive=bdef, size=len(B), min=B_DEFINITIVE_MIN, **({} if bdef >= B_DEFINITIVE_MIN else {"label": "short"}))
    cans = sum(1 for s in C if definitive(s))
    cf = counts["C"]["found"]
    if cans < CONTROL_ANSWERED_MIN:
        t4 = thr(None, answered=cans, found=cf, label="short", min_answered=CONTROL_ANSWERED_MIN)
    else:
        t4 = thr(cf / cans >= CONTROL_FOUND_MIN, answered=cans, found=cf, share=round(cf / cans, 6), min=CONTROL_FOUND_MIN)
    thresholds = {"no_AB_signature_found": t1, "answered_triggers_ge_80pct": t2, "B_definitive_ge_50": t3, "controls_found_ge_95pct_of_answered": t4}
    mets = [t["met"] for t in thresholds.values()]
    clear = False if any(m is False for m in mets) else (None if any(m is None for m in mets) else True)
    return {"tool": TOOL, "spec": SPEC, "selection_sha256": plan["selection_sha256"], "item4_sha256": plan["item4_sha256"], "sizes": plan["sizes"],
            "counts": counts, "thresholds": thresholds, "census_rules_clear": clear, "ambiguities": [list(a) for a in AMBIGUITIES],
            "not_done": ["clean checkout", "C6", "trigger print (item 8)", "decision-to-send ms", "any outcome read"]}


def table(rep: dict) -> str:
    lines = [f"census_rules_clear={rep['census_rules_clear']} (NOT a proposal verdict)", f"sizes={rep['sizes']}"]
    for k, v in sorted(rep["thresholds"].items()):
        lines.append(f"  {k}: met={v['met']}" + (f" ({v['label']})" if v.get("label") else ""))
    return "\n".join(lines) + "\n"


def cmd_report(out_dir: Path) -> dict:
    plan = load_plan(out_dir)
    return report(plan, read_jsonl(out_dir / STATE_NAME))


# ---- CLI ---------------------------------------------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None, transport: Callable = http_transport, sleep: Callable = time.sleep) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["plan", "ask", "reask", "report"])
    ap.add_argument("--item4-json")
    ap.add_argument("--shadow-dir")
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if a.cmd == "plan":
        if not a.item4_json or not a.shadow_dir:
            raise SystemExit("plan needs --item4-json and --shadow-dir")
        if (out / PLAN_NAME).exists():
            raise SystemExit("refused: a plan already exists")
        raw = Path(a.item4_json).read_bytes()
        plan = build_plan(json.loads(raw), Path(a.shadow_dir), sha256_bytes(raw))
        print(f"{PLAN_NAME} sha256 {write_new(out / PLAN_NAME, dumps(plan))}")
        print(f"sizes {plan['sizes']} call_cap {plan['call_cap']}")
    elif a.cmd in ("ask", "reask"):
        n = (cmd_ask if a.cmd == "ask" else cmd_reask)(out, transport, sleep)
        print(f"{n} calls")
        for name in (STATE_NAME, LOG_NAME):
            print(f"{name} sha256 {sha256_bytes((out / name).read_bytes())}")
    else:
        rep = cmd_report(out)
        txt = dumps(rep)
        (out / REPORT_NAME).write_text(txt, encoding="utf-8")
        sys.stdout.write(table(rep))
        print(f"{REPORT_NAME} sha256 {sha256_bytes(txt.encode('utf-8'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
