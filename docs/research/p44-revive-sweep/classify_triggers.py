#!/usr/bin/env python3
"""P44: tag every non-duplicate idea-library row that states a revive_trigger with a coarse class.
Manual overrides (MANUAL) first, then keyword rules; DEPENDS = the trigger is another line, a new rule or an unclear condition for the rows this sweep looked at by hand (MANUAL).
The keyword tags are a reading aid, not a verdict; the SWEEP.md main table governs. Usage: classify_triggers.py IDEAS_JSONL OUT_TSV"""
import json, re, sys

NONE = ("not in source", "none stated in source", "none planned", "")
MANUAL = {
    # measurable now on allowed data, measured in P44
    "lib-c-M1": "NOW", "library-capacity-3": "NOW", "lib-c-R2.SB-H5": "NOW", "lib-a-A3": "NOW", "boost-custom-v2-1": "NOW", "boost-universe-1": "NOW",
    "boost-universe-2": "NOW", "l3-boost-offband-1": "NOW", "l3-boost-custompair-1": "NOW", "mech-buyer-scout-1": "NOW",
    "web-cross-venue-6": "NOW", "census-heaven-boop-moonit": "NOW", "hunt23-w3-pump-sink-1": "NOW",
    "p25-pump-order-keeper-absorb": "MET-EARLIER",
    # measurable now in principle, not run here
    "w4s-short-cex-listing-watch": "NOW-NOT-RUN", "boost-custom-v2-3": "NOW-NOT-RUN", "lib-a-A16": "CAPABILITY", "w4s-short-hl-funding-cost": "NOW-NOT-RUN", "p37-listing-hold-open": "NOW-NOT-RUN",
    # measurable later (date, seal end, or a tracker that must be rebuilt)
    "lib-c-P1": "LATER", "cap-impact-4": "LATER", "lib-b-E6": "LATER", "boost-universe-4": "LATER", "lib-c-J2": "LATER",
    "lib-a-A19": "LATER", "dbc-strand-damm-continue": "LATER", "dbc-early-momentum": "LATER", "p37-dbc-stall-migrate": "LATER",
    "p25-redeploy-holiday": "LATER", "hunt23-w3-qsink-1": "LATER", "lib-c-L1": "LATER", "l3-boost-h5q50-1": "LATER",
    "dbc-milestone": "LATER", "dbc-d5-post-graduation-hold": "LATER", "census-damm2-grad": "LATER",
}
RULES = [
    ("NONE", r"^(none|n/a|not applicable|not a strategy)|none from the fee alone|^none\b|not expected to reopen|dead on mechanism"),
    ("EXTERNAL", r"official|announce|docs|published|public explorer|cex|kalshi|owner|policy|desk list|archive|venue listing|perp|lending market|hyperliquid|virtuals|zora|sunpump|four\.meme|flap|pons|boop|drift|jupiter|treasury|buyback announcement"),
    ("PROCEDURAL", r"declare|gate|ci90|ci lower|ex-top-3|positive mean|mean net|beats|auc|out-of-sample|frozen|registration|amendment|\bdec\b|dec-|qp|quant-proof|screen|re-test|retest|kill|pre-registered|leave-one-day-out|verify|reproduce|paired window|lift|median|predictor|feature|filter|model|label|exit|sign"),
    ("CAPABILITY", r"feed|sub-second|sub-minute|shred|latency|print-level|tape so|on the tape|tape covers|executor|sell path|engine|landing|fee tier|cheaper fee|quote-conversion|idl"),
]


def main(src, out):
    rows = [json.loads(l) for l in open(src) if l.strip()]
    n = 0
    with open(out, "w") as f:
        f.write("id\tstatus\tclass\trevive_trigger\n")
        for r in rows:
            t = (r.get("revive_trigger") or "").strip()
            c = MANUAL.get(r["id"])
            if r.get("duplicate_of") or (t.lower() in NONE and c is None):
                continue
            if c is None:
                tl = t.lower()
                c = next((k for k, rx in RULES if re.search(rx, tl)), "DEPENDS")
            f.write(f"{r['id']}\t{r['status']}\t{c}\t{t.replace(chr(9), ' ').replace(chr(10), ' ')}\n")
            n += 1
    print(n, "rows written")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
