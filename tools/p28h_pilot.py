#!/usr/bin/env python3
"""P28-H API pilot on the stage0c exploration mints (merged DESIGN, section 3 P0-API and 4.3 kill rules).

Subcommands (all paths default under /data/mal/hunt-1008/push-1016/hunt30/p28h/stage0d):
  baserate   exploration base rate per block from tokens.parquet (block, graduated flag only; run in the duckdb venv)
  score      2 calls per analysis-set card via tools.haiku_caller (hard stop, ledger, replay cache); never labels
  analyze    pre-registered tests with labels from stage0c status.jsonl; prints KILL/PASS per rule

The API key is read by tools.haiku_caller from ANTHROPIC_API_KEY at call time; this module never opens a key file.
Labels (grp) are read only by `analyze`, after every score row is on disk.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit

D = "/data/mal/hunt-1008/push-1016/hunt30/p28h/stage0d"
S0C = "/data/mal/hunt-1008/push-1016/hunt13/llm-traders/stage0c"
TOKENS = "/data/mal/hunt-shared/tokens.parquet"
BLOCKS = ("explore-0814", "exp011-0909", "fresh-0903", "fast-pool-0918", "oracle-insample-0922")
FIELDS = ("name", "symbol", "description", "twitter", "telegram", "website")
BUDGET_USD = 5.0
MODEL = "claude-haiku-5-5"
MAX_TOKENS = 300
VETO_SHARES = (0.10, 0.20, 0.30, 0.50)
DRAWS, SEED = 1000, 1


def jl(path):
    return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]


def h(prefix: str, mint: str) -> str:
    return hashlib.sha256((prefix + mint).encode()).hexdigest()


def is_test(mint: str) -> bool:
    return int(h("P28H-SPLIT", mint)[:8], 16) % 2 == 1


# ------------------------------------------------------------------ baserate
def cmd_baserate(a) -> int:
    import duckdb
    sys.path.insert(0, ".")
    from tools import meta_stage0 as ms
    q = f"""SELECT block, count(*) AS n, sum(CASE WHEN grad_ms IS NOT NULL THEN 1 ELSE 0 END) AS g
            FROM read_parquet('{TOKENS}')
            WHERE has_create IS TRUE AND is_mayhem_mode IS NOT TRUE AND {ms._ms_clause('create_ms')}
            GROUP BY block ORDER BY block"""
    rows = duckdb.connect().execute(q).fetchall()
    out = {b: {"n": int(n), "g": int(g), "pi": g / n} for b, n, g in rows}
    tn, tg = sum(v["n"] for v in out.values()), sum(v["g"] for v in out.values())
    five = [b for b in BLOCKS if b in out]
    n5, g5 = sum(out[b]["n"] for b in five), sum(out[b]["g"] for b in five)
    res = {"per_block": out, "five_blocks": {"n": n5, "g": g5, "pi": g5 / n5 if n5 else None},
           "all_blocks": {"n": tn, "g": tg}, "population": "has_create, not mayhem, stage0 segments"}
    Path(a.dir, "baserate.json").write_text(json.dumps(res, indent=1, sort_keys=True))
    print(json.dumps(res, indent=1, sort_keys=True))
    return 0


# ------------------------------------------------------------------ score
def split_prompt(text: str):
    system = text.split("SYSTEM\n", 1)[1].split("\nUSER", 1)[0].strip()
    return system


def user_text(card: dict, reverse: bool) -> str:
    order = list(FIELDS)[::-1] if reverse else list(FIELDS)
    lines = "\n".join(f"{k}: {card[k]}" for k in order)
    return f"Card:\n```\nid: {card['id']}\n{lines}\n```\nReturn the JSON object for this card."


def parse_obj(text: str):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        o = json.loads(t)
    except ValueError:
        return None
    if not isinstance(o, dict):
        return None
    s = o.get("score")
    if isinstance(s, bool) or not isinstance(s, (int, float)) or not 0 <= s <= 100:
        return None
    return o


def analysis_cards(d: Path):
    from tools import p28h_cards as pc
    rows = jl(d / "fetched.jsonl")
    dbg = set(json.loads((d / "debug_map.json").read_text()).values())
    ok = [r for r in rows if r.get("status") == "ok" and r.get("host_class") == "ipfs_cid"
          and r.get("s0c_json_ok") and r.get("name") is not None and r["mint"] not in dbg]
    ok.sort(key=lambda r: h("P28H-PILOT", r["mint"]))
    return [(r["mint"], {"id": f"p{i:04d}", **pc.make_card(r)}) for i, r in enumerate(ok, 1)]


def cmd_score(a) -> int:
    from tools.haiku_caller import HaikuCaller
    d = Path(a.dir)
    system = split_prompt((d / "prompt.md").read_text())
    cards = analysis_cards(d)
    caller = HaikuCaller(d / "pilot_ledger.jsonl", BUDGET_USD, save_dir=d / "pilot_store", model=MODEL, replay=True)
    done = set()
    sp = d / "scores.jsonl"
    if sp.exists():
        done = {(r["id"], r["call"]) for r in jl(sp)}
    n_new = fail = 0
    with open(sp, "a") as fh:
        for mint, card in cards:
            for call in (1, 2):
                if (card["id"], call) in done:
                    continue
                res = caller.call(system=system, max_tokens=MAX_TOKENS,
                                  messages=[{"role": "user", "content": user_text(card, call == 2)}])
                o = parse_obj(res.text)
                row = {"id": card["id"], "mint": mint, "call": call, "ok": o is not None,
                       "score": o["score"] if o else None, "injection_flag": bool(o and o.get("injection_flag")),
                       "usage": res.usage, "cost_usd": res.cost_usd, "replayed": res.replayed}
                fh.write(json.dumps(row) + "\n")
                fh.flush()
                n_new += 1
                fail += o is None
    from tools.haiku_caller import ledger_summary
    print(json.dumps({"cards": len(cards), "new_calls": n_new, "parse_fail": fail,
                      "ledger": ledger_summary(d / "pilot_ledger.jsonl")}, sort_keys=True))
    return 0


# ------------------------------------------------------------------ analysis helpers
def logistic_fit(X, y, lam=1.0, iters=50):
    import numpy as np
    Xb = np.hstack([np.ones((len(X), 1)), X])
    w = np.zeros(Xb.shape[1])
    R = lam * np.eye(Xb.shape[1])
    R[0, 0] = 0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(Xb @ w, -30, 30)))
        g = Xb.T @ (p - y) + R @ w
        Hm = (Xb * (p * (1 - p))[:, None]).T @ Xb + R + 1e-9 * np.eye(Xb.shape[1])
        step = np.linalg.solve(Hm, g)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return w


def logistic_pred(w, X):
    import numpy as np
    return 1 / (1 + np.exp(-np.clip(np.hstack([np.ones((len(X), 1)), X]) @ w, -30, 30)))


def rank(x):
    import numpy as np
    order = np.argsort(x, kind="mergesort")
    r = np.empty(len(x))
    r[order] = np.arange(len(x))
    for v in np.unique(x):  # average ties
        m = x == v
        if m.sum() > 1:
            r[m] = r[m].mean()
    return r


def spearman(a, b):
    import numpy as np
    return float(np.corrcoef(rank(a), rank(b))[0, 1])


def auc(s, y):
    r = rank(s)
    n1, n0 = y.sum(), (1 - y).sum()
    return float((r[y == 1].sum() - n1 * (n1 - 1) / 2) / (n1 * n0 + 1e-12))  # rank-sum form, 0-based ranks


def kept_rate(score, y, w, v):
    """Weighted label rate among kept coins after vetoing the lowest-scoring weighted share v."""
    import numpy as np
    o = np.argsort(score, kind="mergesort")
    cw = np.cumsum(w[o])
    keep = np.zeros(len(score), bool)
    keep[o[cw > v * cw[-1] + 1e-12]] = True
    return float((w[keep] * y[keep]).sum() / (w[keep].sum() + 1e-12))


def weights(y, pi):
    import numpy as np
    nG, nN = max(y.sum(), 1), max((1 - y).sum(), 1)
    return np.where(y == 1, pi / nG, (1 - pi) / nN)


def wlogloss(p, y, w):
    import numpy as np
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-(w * (y * np.log(p) + (1 - y) * np.log(1 - p))).sum() / w.sum())


def b3_feats(name, symbol):
    n = name or ""
    nonal = sum(not c.isalnum() and not c.isspace() for c in n) / max(len(n), 1)
    return [len(n), nonal, float(n.isupper()), float((symbol or "").lower() == n.lower()), float(any(c.isdigit() for c in n))]


def cmd_analyze(a) -> int:
    import numpy as np
    d = Path(a.dir)
    import duckdb
    base = json.loads((d / "baserate.json").read_text())
    pi_all = base["five_blocks"]["pi"]
    pi_b = {b: base["per_block"][b]["pi"] for b in BLOCKS}
    s0c = {r["mint"]: r for r in jl(Path(S0C) / "status.jsonl")}
    uris = {r["mint"]: r.get("uri") or "" for r in jl(Path(S0C) / "uris.jsonl")}
    fetched = {r["mint"]: r for r in jl(d / "fetched.jsonl")}
    sc = defaultdict(dict)
    for r in jl(d / "scores.jsonl"):
        sc[r["mint"]][r["call"]] = r
    # B4/B5: exploration creates only, same block (tokens.parquet create columns; no outcome column is selected)
    con = duckdb.connect()
    need = set(sc)
    dup, prior = {}, {}
    for b in BLOCKS:  # one block at a time to bound memory
        cur = con.execute(f"SELECT mint, create_ms, creator, lower(name), lower(symbol) FROM read_parquet('{TOKENS}') "
                          f"WHERE has_create IS TRUE AND create_ms IS NOT NULL AND block = ? ORDER BY create_ms", [b])
        names, syms, creators = defaultdict(list), defaultdict(list), Counter()
        while True:
            chunk = cur.fetchmany(50000)
            if not chunk:
                break
            for m, t, c, nm, sy in chunk:
                lo = t - 86400000
                if m in need:
                    dn = len(names[nm]) - bisect.bisect_left(names[nm], lo) if nm else 0
                    ds = len(syms[sy]) - bisect.bisect_left(syms[sy], lo) if sy else 0
                    dup[m] = dn + ds
                    prior[m] = creators[c] if c else 0
                if nm:
                    names[nm].append(t)
                if sy:
                    syms[sy].append(t)
                if c:
                    creators[c] += 1
    rows, dropped = [], 0
    for m, cs in sc.items():
        if len(cs) < 2 or not all(c["ok"] for c in cs.values()):
            dropped += 1
            continue
        f, st = fetched[m], s0c[m]
        host = (urlsplit(uris.get(m, "")).hostname or "none").lower()
        rows.append(dict(mint=m, block=st["block"], y=1 if st["grp"] == "G" else 0,
                         s=(cs[1]["score"] + cs[2]["score"]) / 2, s1=cs[1]["score"], s2=cs[2]["score"],
                         desc=len(f.get("description") or ""),
                         soc=sum(bool(f.get(k)) for k in ("twitter", "telegram", "website")),
                         b3=b3_feats(f.get("name"), f.get("symbol")), dup=dup.get(m, 0), prior=prior.get(m, 0),
                         host=host, test=is_test(m)))
    tr = [r for r in rows if not r["test"]]
    te = [r for r in rows if r["test"]]
    hosts = [k for k, c in Counter(r["host"] for r in tr).items() if c >= 20]

    def feat(r, kind):
        if kind == "B1":
            return [math.log1p(r["desc"])]
        if kind == "B2":
            return [r["soc"]]
        if kind == "B3":
            return r["b3"][:1] + r["b3"][1:]
        if kind == "B4":
            return [math.log1p(r["dup"])]
        if kind == "B5":
            return [math.log1p(r["prior"])]
        if kind == "B6":
            return [float(r["host"] == k) for k in hosts]
        if kind == "B7":
            return sum((feat(r, k) for k in ("B1", "B2", "B3", "B4", "B5", "B6")), [])

    def mat(rs, kind):
        return np.array([feat(r, kind) for r in rs], float)

    ytr = np.array([r["y"] for r in tr], float)
    yte = np.array([r["y"] for r in te], float)
    models, preds = {}, {}
    for k in ("B1", "B2", "B3", "B4", "B5", "B6", "B7"):
        Xtr = mat(tr, k)
        mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9
        models[k] = (mu, sd, logistic_fit((Xtr - mu) / sd, ytr))
        preds[k] = logistic_pred(models[k][2], (mat(te, k) - mu) / sd)
    H = np.array([r["s"] for r in te], float)
    Xb7 = mat(tr, "B7")
    mu7, sd7 = models["B7"][0], models["B7"][1]
    Z_tr = np.hstack([(Xb7 - mu7) / sd7, np.array([[r["s"]] for r in tr]) / 100.0])
    Z_te = np.hstack([(mat(te, "B7") - mu7) / sd7, (H / 100.0)[:, None]])
    w_hb7 = logistic_fit(Z_tr, ytr)
    p_hb7 = logistic_pred(w_hb7, Z_te)
    p_b7 = preds["B7"]
    blk = np.array([r["block"] for r in te])
    hasd = np.array([r["desc"] > 0 for r in te])
    rng = np.random.default_rng(SEED)
    idxG, idxN = np.where(yte == 1)[0], np.where(yte == 0)[0]
    draws = []
    for _ in range(DRAWS):
        draws.append(np.concatenate([rng.choice(idxG, len(idxG)), rng.choice(idxN, len(idxN))]))

    def lift(score_a, score_b, idx, pi, v):
        yy = yte[idx]
        w = weights(yy, pi)
        return kept_rate(score_a[idx], yy, w, v) - kept_rate(score_b[idx], yy, w, v)

    out = {"n_scored": len(rows), "dropped_parse_or_missing": dropped, "n_train": len(tr), "n_test": len(te),
           "test_G": int(yte.sum()), "pi_five_blocks": pi_all, "hosts_in_B6": hosts}
    allidx = np.arange(len(te))
    rep = {}
    for k in ("B1", "B2", "B3", "B4", "B5", "B6", "B7"):
        rep[k] = {f"v{int(v*100)}": kept_rate(preds[k], yte, weights(yte, pi_all), v) for v in VETO_SHARES}
    rep["Haiku"] = {f"v{int(v*100)}": kept_rate(H, yte, weights(yte, pi_all), v) for v in VETO_SHARES}
    out["kept_rate_weighted"] = rep
    out["no_veto_rate"] = pi_all
    out["auc_test"] = {"Haiku": auc(H, yte), "B7": auc(p_b7, yte), "B1": auc(preds["B1"], yte)}
    out["auc_diff_haiku_minus_b7"] = out["auc_test"]["Haiku"] - out["auc_test"]["B7"]
    # rule 3: matched veto share vs B7, bootstrap lower 90%
    r3 = {}
    for v in VETO_SHARES:
        pt = lift(H, p_b7, allidx, pi_all, v)
        ds = np.array([lift(H, p_b7, ix, pi_all, v) for ix in draws])
        r3[f"v{int(v*100)}"] = {"diff": pt, "lo90": float(np.percentile(ds, 5)), "pass": bool(np.percentile(ds, 5) > 0)}
    out["rule3_vs_b7"] = r3
    wl_b7 = wlogloss(p_b7, yte, weights(yte, pi_all))
    wl_h = wlogloss(p_hb7, yte, weights(yte, pi_all))
    gains = []
    for ix in draws:
        gains.append(wlogloss(p_b7[ix], yte[ix], weights(yte[ix], pi_all)) - wlogloss(p_hb7[ix], yte[ix], weights(yte[ix], pi_all)))
    out["rule3_logloss"] = {"gain": wl_b7 - wl_h, "lo90": float(np.percentile(gains, 5)),
                            "pass": bool(np.percentile(gains, 5) > 0)}
    # per-block sign: mean over the four shares of the kept-rate diff, blocks with n >= 50 test coins
    pb = {}
    for b in BLOCKS:
        ix = np.where(blk == b)[0]
        if len(ix) < 50 or yte[ix].sum() == 0 or (1 - yte[ix]).sum() == 0:
            pb[b] = {"n": int(len(ix)), "counted": False}
            continue
        diffs = [lift(H, p_b7, ix, pi_b[b], v) for v in VETO_SHARES]
        pb[b] = {"n": int(len(ix)), "counted": True, "mean_diff": float(np.mean(diffs)),
                 "auc_diff": auc(H[ix], yte[ix]) - auc(p_b7[ix], yte[ix])}
    cnt = [v for v in pb.values() if v["counted"]]
    neg = sum(1 for v in cnt if v["mean_diff"] < 0)
    out["per_block"] = pb
    out["rule3_block_majority_negative"] = bool(cnt and neg * 2 > len(cnt))
    n_pass = sum(1 for v in r3.values() if v["pass"])
    out["RULE3"] = "PASS" if (n_pass >= 3 and out["rule3_logloss"]["pass"] and not out["rule3_block_majority_negative"]) else "KILL"
    out["rule3_shares_passed"] = n_pass
    # rule 2: same-bucket rate on TEST (bucket = floor(score / 20), five buckets)
    same = np.mean([min(int(r["s1"] // 20), 4) == min(int(r["s2"] // 20), 4) for r in te])
    out["RULE2_same_bucket_rate"] = float(same)
    out["RULE2"] = "PASS" if same >= 0.85 else "KILL"
    # rule 4
    rho7 = spearman(H, p_b7)
    rho1 = spearman(H, np.array([r["desc"] for r in te], float))
    # B6 held fixed: veto within each B6 stratum (host with >= 40 test coins), pooled kept rate
    strata = [k for k in set(r["host"] for r in te) if sum(1 for r in te if r["host"] == k) >= 40]
    hostte = np.array([r["host"] for r in te])

    def fixed(ix, v):
        kept = []
        for sname in strata + ["__other__"]:
            m = (hostte[ix] == sname) if sname != "__other__" else ~np.isin(hostte[ix], strata)
            jx = ix[m]
            if len(jx) < 5:
                continue
            kept.append(jx)
        res = {}
        for nm, sc_ in (("H", H), ("B7", p_b7)):
            num = den = 0.0
            for jx in kept:
                w = weights(yte[jx], pi_all)
                o = np.argsort(sc_[jx], kind="mergesort")
                cw = np.cumsum(w[o])
                keepm = np.zeros(len(jx), bool)
                keepm[o[cw > v * cw[-1] + 1e-12]] = True
                num += (w[keepm] * yte[jx][keepm]).sum()
                den += w[keepm].sum()
            res[nm] = num / (den + 1e-12)
        return res["H"] - res["B7"]

    fx_pt = float(np.mean([fixed(allidx, v) for v in VETO_SHARES]))
    fx_ds = np.array([np.mean([fixed(ix, v) for v in VETO_SHARES]) for ix in draws[:300]])
    fx_lo = float(np.percentile(fx_ds, 5))
    out["rule4"] = {"spearman_haiku_b7": rho7, "spearman_haiku_b1": rho1, "b6_fixed_mean_diff": fx_pt,
                    "b6_fixed_lo90_300draws": fx_lo, "strata": strata}
    out["RULE4"] = "KILL" if (rho7 > 0.9 or rho1 > 0.8 or fx_lo <= 0) else "PASS"
    # rule 5: lift by description presence (informational)
    r5 = {}
    for nm, m in (("desc_present", hasd), ("desc_empty", ~hasd)):
        ix = np.where(m)[0]
        if len(ix) >= 30 and yte[ix].sum() > 0 and (1 - yte[ix]).sum() > 0:
            r5[nm] = {"n": int(len(ix)), "mean_diff_vs_b7": float(np.mean([lift(H, p_b7, ix, pi_all, v) for v in VETO_SHARES])),
                      "auc_diff": auc(H[ix], yte[ix]) - auc(p_b7[ix], yte[ix])}
    out["rule5_informational"] = r5
    (d / "pilot_results.json").write_text(json.dumps(out, indent=1, sort_keys=True))
    print(json.dumps(out, indent=1, sort_keys=True))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("baserate", "score", "analyze"))
    ap.add_argument("--dir", default=D)
    a = ap.parse_args(argv)
    return {"baserate": cmd_baserate, "score": cmd_score, "analyze": cmd_analyze}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
