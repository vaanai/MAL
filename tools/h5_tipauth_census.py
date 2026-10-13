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

PEEKING RULE (quant-proof, PR #615 comment 6121750787, answer 5, items 1 to 6, verbatim; Job #817 instants are T0 1791797643640, as --until-ms:
+6 h 1791819243640 (interim), +24 h 1791884043640, +36 h 1791927243640, +48 h 1791970443640, then every 12 h):
  1. `plan` takes only the saved JSON of a scheduled item-4 read. One out-dir per read, named by its `until_ms`. One plan per read, never re-made.
  2. The +6 h interim census may run, once, `plan` and `ask` and `reask`, only after this tool is merged with QP OK on the merged head and before
     the +24 h instant. If the merge is later than that, it is skipped. It is optional.
  3. An interim census can only count against the proposal. A found A or B signature at interim is final ("no proposal on this definition"). Its nulls,
     its controls and its shares satisfy nothing, are not quoted, and do not carry over: the deciding census asks every signature again. The tool
     refuses to print true at interim (`interim` in the plan, `census_rules_clear` never true).
  4. The deciding census is the one planned on the deciding read's JSON (first scheduled instant from +24 h at which the item-4 duration line is
     met). `ask` starts within 6 h of that instant. Then `reask` until it makes 0 calls or 3 rounds have run (REASK_MAX). The report after that
     decides. Nobody chooses the round.
  5. Deciding census null (short): extends to the next scheduled instant, new read, new plan, new out-dir. False by a found: final. False by
     controls: ruling 4 (the nulls of that census are void, a found is not; the next scheduled read gets a new census).
  6. Every plan, state, request log and report is kept, its sha256 goes in the notebook, none is deleted. No census runs between scheduled reads.

THE EXACT SENTENCES THE PROPOSAL MAY USE (quant-proof, same comment, answer 4; "<...>" were HTML-escaped in the comment body):
  When `census_rules_clear` is true on the deciding census, with 0 control misses:
    "Census on the +<h> h read (item-4 sha256 <x>, census report sha256 <y>): <nA> signatures of prints dropped on <k> would-pass triggers, and <nB>
    further dropped-print signatures (the first 50 by sha256 of <b_candidates_n> listed, at most 20 listed per close, <p> closes partial), were each
    asked of the public mainnet-beta RPC at finalized commitment twice, at least 10 minutes apart. None was returned. <cf> of <ca> control signatures
    from the same pools and window, which the tip tape holds, were returned. This is consistent with the dropped prints not being on the finalized
    chain. It is not proof of absence and it is not evidence of profit."
  With m >= 1 control missed, replace the control sentence by:
    "<cf> of <ca> controls were returned; <m> that the tip tape holds was not. The endpoint fails to return some transactions that are on chain, so a
    not-returned answer for a dropped print is weaker than the count suggests."
  With `ab_older_than_oldest_found_control_n` = x > 0, append:
    "<x> of the asked signatures are older than the oldest returned control; for those the answer is not checked by a control."
  If more than one census ran in this restart, each census's control counts are stated.
  NOT ALLOWED: "phantom" or "not on chain" as a fact, "verified", "all dropped prints", any percentage, any interim count.

  python3 tools/h5_tipauth_census.py plan   --item4-json F --shadow-dir D --out-dir O
  python3 tools/h5_tipauth_census.py ask    --out-dir O
  python3 tools/h5_tipauth_census.py reask  --out-dir O
  python3 tools/h5_tipauth_census.py report --out-dir O
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

TOOL = "h5_tipauth_census"
SPEC = {"ruling3": "PR #610 comment 6121547427", "round3": "PR #610 comment 6121123397", "round2": "PR #610 comment 6120949717"}

# ---- the ONE place that names the item-4 output keys this tool depends on (PR #613 head 71f7522b5ac14df33a0f6b29f8a2279483a1c340; re-check at merge) ------------------------------
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
    "since": "since_ms",                              # top level (T0 of the read; interim = until - since < 24 h)
    "partial": "partial",                             # in each close row (NOT emitted on would-pass trigger rows at 71f7522)
    "variant": "variant",                             # in each would-pass trigger row
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
REASK_MAX = 3
DAY_MS = 86_400_000
CALL_ROUNDS = 1 + REASK_MAX  # total_call_cap = 4 * call_cap
TRIGGER_ANSWERED_MIN = 0.80
B_DEFINITIVE_MIN = 50
CONTROL_FOUND_MIN = 0.95
CONTROL_ANSWERED_MIN = 20
PLAN_NAME, STATE_NAME, LOG_NAME, REPORT_NAME = "census_plan.json", "census_state.jsonl", "census_requests.jsonl", "census_report.json"

AMBIGUITIES = [
    ("R1", "'a not-found re-asked at least 10 minutes later'",
     "not_found needs TWO null answers whose asked_ms differ by >= 600,000 ms. One null is `pending`. `reask` therefore re-queries `error` rows AND single-null rows "
     "(never found, never twice-null), and refuses unless >= 10 minutes passed since the last ask/reask."),
    ("R2", "RULED, REPLACED by change 1: 'at least 80% of would-pass triggers with a definitive answer for every listed signature'",
     "A trigger counts only if it is fully enumerated (dropped is a list, len(dropped) == dropped_n, len >= 1, EVERY entry well formed: [str, None-or-int, int]) "
     "and every listed signature is definitive. A malformed entry makes the trigger not fully enumerated and fails `no_malformed_dropped_entry`."),
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

RULED = {"R1": "accepted", "R2": "replaced by change 1", "R3": "accepted", "R4": "accepted as coded", "R5": "accepted", "R6": "accepted", "R7": "accepted",
         "R8": "accepted", "R9": "same as R1 (not separately ruled)"}  # quant-proof PR #615 comment 6121750787


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


def entry_ok(e: Any) -> bool:
    return isinstance(e, list) and len(e) == 3 and isinstance(e[0], str) and (e[1] is None or isint(e[1])) and isint(e[2])


def sig_list(entries: Any) -> list[str]:
    """Distinct signatures of a `dropped` list ([signature, event_index, slot] entries); malformed entries are ignored here (and counted elsewhere)."""
    out: list[str] = []
    if isinstance(entries, list):
        for e in entries:
            if entry_ok(e) and e[0] not in out:
                out.append(e[0])
    return out


def fully_enumerated(row: Any) -> bool:
    d, n = row.get(ITEM4["dropped"]), row.get(ITEM4["dropped_n"])
    return isinstance(d, list) and isint(n) and len(d) == n and len(d) >= 1 and all(entry_ok(e) for e in d)


def malformed_n(d: Any) -> int:
    return sum(1 for e in d if not entry_ok(e)) if isinstance(d, list) else 0


def is_partial(r: Any) -> bool:
    d, n = r.get(ITEM4["dropped"]), r.get(ITEM4["dropped_n"])
    return r.get(ITEM4["partial"]) is not False or not (isint(n) and isinstance(d, list) and len(d) == n)


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
    slot_min: dict[str, int] = {}
    malformed = 0

    def note_slots(d: Any) -> None:
        if isinstance(d, list):
            for e in d:
                if entry_ok(e):
                    slot_min[e[0]] = min(slot_min.get(e[0], e[2]), e[2])

    wp_pools: set[str] = set()
    for r in wps:
        if not isinstance(r, dict):
            continue
        sigs = sig_list(r.get(ITEM4["dropped"]))
        A.update(sigs)
        malformed += malformed_n(r.get(ITEM4["dropped"]))
        note_slots(r.get(ITEM4["dropped"]))
        triggers.append({"sigs": sorted(sigs), "fully_enumerated": fully_enumerated(r), "variant": r.get(ITEM4["variant"])})
        p = r.get(ITEM4["pool"])
        if isinstance(p, str):
            wp_pools.add(p)
    cand: dict[str, set[str]] = {}  # B candidate signature -> pools of the closes listing it
    b_closes = b_partial = 0
    for r in closes:
        if not isinstance(r, dict):
            continue
        b_closes += 1
        b_partial += 1 if is_partial(r) else 0
        malformed += malformed_n(r.get(ITEM4["dropped"]))
        note_slots(r.get(ITEM4["dropped"]))
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
    since = top.get(ITEM4["since"])
    if not isint(origin) or not isint(until):
        raise SystemExit("refused: item-4 output has no integer window origin / until_ms")
    if not isint(since):
        raise SystemExit("refused: item-4 output has no integer since_ms")
    return {"A": sorted(A), "B": B, "triggers": triggers, "b_candidates": sorted(cand), "control_pools": sorted(pools), "origin": origin, "until": until,
            "since": since, "slots": {s: slot_min[s] for s in sorted(set(A) | set(B)) if s in slot_min}, "malformed": malformed,
            "b_closes": b_closes, "b_partial": b_partial}


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


def selection_hash(plan: dict) -> str:
    """sha256 over the sets, triggers, slots, window, item4_sha256, malformed_entries_n and interim (change 8)."""
    body = {k: plan[k] for k in ("sets", "triggers", "slots", "window", "item4_sha256", "malformed_entries_n", "interim")}
    return sha256_bytes(dumps(body).encode("utf-8"))


def build_plan(item4: Any, shadow_dir: Path, item4_sha: str, shadow_hook: Callable | None = None) -> dict:
    sel = select_AB(item4)
    exclude = set(sel["A"]) | set(sel["b_candidates"])
    C = read_controls(shadow_dir, set(sel["control_pools"]), sel["origin"], sel["until"], exclude, shadow_hook)
    sets = {"A": sel["A"], "B": sel["B"], "C": C}
    cap = sum(len(v) for v in sets.values())
    plan = {"tool": TOOL, "spec": SPEC, "item4_sha256": item4_sha,
            "window": {"origin_t_ms": sel["origin"], "until_ms": sel["until"], "since_ms": sel["since"]},
            "interim": (sel["until"] - sel["since"]) < DAY_MS,
            "sets": sets, "sizes": {k: len(v) for k, v in sets.items()}, "call_cap": cap, "total_call_cap": CALL_ROUNDS * cap,
            "b_candidates_n": len(sel["b_candidates"]), "b_closes_n": sel["b_closes"], "b_partial_closes_n": sel["b_partial"],
            "control_pools_n": len(sel["control_pools"]), "malformed_entries_n": sel["malformed"], "slots": sel["slots"], "triggers": sel["triggers"]}
    plan["selection_sha256"] = selection_hash(plan)
    return plan


# ---- RPC ---------------------------------------------------------------------------------------------------------------------------------


def request_payload(sig: str) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "method": "getTransaction",
            "params": [sig, {"commitment": "finalized", "maxSupportedTransactionVersion": 0, "encoding": "json"}]}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a: Any, **k: Any) -> None:  # a redirect is an error (HTTPError), never followed
        return None


def http_transport(url: str, payload: dict, timeout: float) -> Any:
    opener = urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "User-Agent": "mal-h5-census/1"})
    with opener.open(req, timeout=timeout) as resp:  # noqa: S310 (fixed https URL)
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


def read_jsonl_ex(path: Path) -> tuple[list[dict], int]:
    """Rows and the number of unparsable lines. One torn line (a crash mid-append) is tolerated and counted; two or more refuse."""
    if not path.exists():
        return [], 0
    rows: list[dict] = []
    torn = 0
    for x in path.read_text(encoding="utf-8").splitlines():
        if not x.strip():
            continue
        try:
            v = json.loads(x)
            if not isinstance(v, dict):
                raise ValueError("not an object")
            rows.append(v)
        except ValueError:
            torn += 1
            if torn > 1:
                raise SystemExit(f"refused: more than one unparsable line in {path.name}")
    return rows, torn


def read_jsonl(path: Path) -> list[dict]:
    return read_jsonl_ex(path)[0]


def append_jsonl(path: Path, row: dict) -> None:
    lead = ""
    if path.exists() and path.stat().st_size > 0:
        with open(path, "rb") as rf:
            rf.seek(-1, os.SEEK_END)
            if rf.read(1) != b"\n":
                lead = "\n"  # a torn last line: seal it so the new row starts on its own line
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(lead + json.dumps(row, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def load_plan(out_dir: Path) -> dict:
    p = out_dir / PLAN_NAME
    if not p.exists():
        raise SystemExit("refused: no plan")
    plan = json.loads(p.read_text(encoding="utf-8"))
    try:
        ok = selection_hash(plan) == plan.get("selection_sha256")
        ok = ok and plan["call_cap"] == sum(len(plan["sets"][k]) for k in ("A", "B", "C")) and plan["total_call_cap"] == CALL_ROUNDS * plan["call_cap"]
    except (KeyError, TypeError):
        ok = False
    if not ok:
        raise SystemExit("refused: plan selection hash mismatch")
    return plan


def all_sigs(plan: dict) -> list[tuple[str, str]]:
    return [(s, k) for k in ("A", "B", "C") for s in plan["sets"][k]]


def run_queries(out_dir: Path, sigs: list[str], cap: int, transport: Callable, sleep: Callable, now_ms: Callable, round_name: str) -> int:
    if len(sigs) > cap:
        raise SystemExit("refused: more queries than the call cap")
    if len(read_jsonl(out_dir / STATE_NAME)) + len(sigs) > CALL_ROUNDS * cap:
        raise SystemExit("refused: more queries than the total call cap")
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
    if plan["call_cap"] == 0:
        raise SystemExit("refused: empty plan")
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
    done = len({r.get("round") for r in rows if str(r.get("round")).startswith("reask")})
    if done >= REASK_MAX:
        raise SystemExit("refused: 3 reask rounds already ran")
    by = states_by_sig(rows)
    todo = [s for s, _ in all_sigs(plan) if classify(by.get(s, [])) in ("error", "pending")]
    return run_queries(out_dir, todo, plan["call_cap"], transport, sleep, now_ms, f"reask{done + 1}")


# ---- report ------------------------------------------------------------------------------------------------------------------------------


def thr(met: bool | None, **kw: Any) -> dict:
    return {"met": met, **kw}


def report(plan: dict, rows: list[dict], plan_sha: str | None = None, state_sha: str | None = None, torn: int = 0) -> dict:
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
    pv = [t for t in trig if t.get("variant") == "pv"]
    ans = lambda ts: sum(1 for t in ts if t["fully_enumerated"] and all(definitive(s) for s in t["sigs"]))  # noqa: E731
    sh = lambda ts: None if not ts else ans(ts) / len(ts)  # noqa: E731
    s_all, s_pv = sh(trig), sh(pv)
    if any(x is not None and x < TRIGGER_ANSWERED_MIN for x in (s_all, s_pv)):
        m2: bool | None = False
    else:
        m2 = None if (s_all is None or s_pv is None) else True
    t2 = thr(m2, n=len(trig), answered=ans(trig), share=None if s_all is None else round(s_all, 6), pv_n=len(pv), pv_answered=ans(pv),
             pv_share=None if s_pv is None else round(s_pv, 6), min=TRIGGER_ANSWERED_MIN, **({"label": "short"} if m2 is None else {}))
    bdef = sum(1 for s in B if definitive(s))
    t3 = thr(True if bdef >= B_DEFINITIVE_MIN else None, definitive=bdef, size=len(B), min=B_DEFINITIVE_MIN, **({} if bdef >= B_DEFINITIVE_MIN else {"label": "short"}))
    cans = sum(1 for s in C if definitive(s))
    cf = counts["C"]["found"]
    if cans < CONTROL_ANSWERED_MIN:
        t4 = thr(None, answered=cans, found=cf, label="short", min_answered=CONTROL_ANSWERED_MIN)
    else:
        t4 = thr(cf / cans >= CONTROL_FOUND_MIN, answered=cans, found=cf, share=round(cf / cans, 6), min=CONTROL_FOUND_MIN)
    mal = plan["malformed_entries_n"]
    t5 = thr(mal == 0, n=mal)
    thresholds = {"no_AB_signature_found": t1, "answered_triggers_ge_80pct": t2, "B_definitive_ge_50": t3, "controls_found_ge_95pct_of_answered": t4,
                  "no_malformed_dropped_entry": t5}
    mets = [t["met"] for t in thresholds.values()]
    clear = False if any(m is False for m in mets) else (None if any(m is None for m in mets) else True)
    interim = bool(plan["interim"])
    if interim and clear is True:
        clear = None  # an interim census can only count against the proposal
    # age and provenance (report only)
    slots = plan["slots"]
    ab_slots = [slots[s] for s in A + B if s in slots]
    cslots = [min(a["slot"] for a in by[s] if a["result"] == "found") for s in C if cls[s] == "found"]
    older = None if not cslots else sum(1 for s in A + B if s in slots and slots[s] < min(cslots))
    asked = [r["asked_ms"] for r in rows]
    rounds = {r.get("round") for r in rows if str(r.get("round")).startswith("reask")}
    return {"tool": TOOL, "spec": SPEC, "selection_sha256": plan["selection_sha256"], "item4_sha256": plan["item4_sha256"], "sizes": plan["sizes"],
            "interim": interim, "window": plan["window"], "counts": counts, "thresholds": thresholds, "census_rules_clear": clear,
            "b_candidates_n": plan["b_candidates_n"], "b_closes_n": plan["b_closes_n"], "b_partial_closes_n": plan["b_partial_closes_n"],
            "ab_slot_min": min(ab_slots) if ab_slots else None, "ab_slot_max": max(ab_slots) if ab_slots else None,
            "control_found_slot_min": min(cslots) if cslots else None, "control_found_slot_max": max(cslots) if cslots else None,
            "ab_older_than_oldest_found_control_n": older, "plan_sha256": plan_sha, "state_sha256": state_sha, "state_rows_n": len(rows),
            "state_torn_lines_n": torn, "reask_rounds_n": len(rounds), "first_asked_ms": min(asked) if asked else None,
            "last_asked_ms": max(asked) if asked else None, "total_call_cap": plan["total_call_cap"],
            "ambiguities": [list(a) + [RULED.get(a[0])] for a in AMBIGUITIES],
            "not_done": ["clean checkout", "C6", "trigger print (item 8)", "decision-to-send ms", "any outcome read", "tx_err of a found answer"]}


def table(rep: dict) -> str:
    lines = []
    if rep["interim"]:
        lines.append("INTERIM, not the deciding read: only a found A or B signature counts")
    lines += [f"census_rules_clear={rep['census_rules_clear']} (NOT a proposal verdict)", f"sizes={rep['sizes']}"]
    for k, v in sorted(rep["thresholds"].items()):
        lines.append(f"  {k}: met={v['met']}" + (f" ({v['label']})" if v.get("label") else ""))
    t2 = rep["thresholds"]["answered_triggers_ge_80pct"]
    lines.append(f"  triggers: all {t2['answered']}/{t2['n']}, pv {t2['pv_answered']}/{t2['pv_n']}")
    return "\n".join(lines) + "\n"


def cmd_report(out_dir: Path) -> tuple[dict, str]:
    """Return (report, its text). Writes census_report.json with exclusive create; refuses to overwrite."""
    plan = load_plan(out_dir)
    if (out_dir / REPORT_NAME).exists():
        raise SystemExit("refused: a report already exists")
    rows, torn = read_jsonl_ex(out_dir / STATE_NAME)
    state_sha = sha256_bytes((out_dir / STATE_NAME).read_bytes()) if (out_dir / STATE_NAME).exists() else None
    rep = report(plan, rows, sha256_bytes((out_dir / PLAN_NAME).read_bytes()), state_sha, torn)
    txt = dumps(rep)
    write_new(out_dir / REPORT_NAME, txt)
    return rep, txt


# ---- CLI ---------------------------------------------------------------------------------------------------------------------------------


def file_sha(p: Path) -> str:
    return sha256_bytes(p.read_bytes()) if p.exists() else "absent"


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
        print(f"sizes {plan['sizes']} call_cap {plan['call_cap']} total_call_cap {plan['total_call_cap']} interim {plan['interim']}")
    elif a.cmd in ("ask", "reask"):
        n = (cmd_ask if a.cmd == "ask" else cmd_reask)(out, transport, sleep)
        print(f"{n} calls")
        for name in (STATE_NAME, LOG_NAME):
            print(f"{name} sha256 {file_sha(out / name)}")
    else:
        rep, txt = cmd_report(out)
        sys.stdout.write(table(rep))
        print(f"{REPORT_NAME} sha256 {sha256_bytes(txt.encode('utf-8'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
