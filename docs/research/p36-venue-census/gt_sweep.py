"""GeckoTerminal public API sweep: per dex, pools sorted by h24 volume, up to 10 pages; stop when page tail h24 vol < 200 USD."""
import json, sys, time, urllib.request
OUT = sys.argv[1]
DEXES = sys.argv[2].split(",")
res = {}
def get(url):
    for i in range(4):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "mal-p36-census"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            err = str(e)
            time.sleep(10 * (i + 1))
    return {"error": err}
calls = 0
for dex in DEXES:
    pools = []
    for page in range(1, 11):
        d = get(f"https://api.geckoterminal.com/api/v2/networks/solana/dexes/{dex}/pools?sort=h24_volume_usd_desc&page={page}")
        calls += 1
        time.sleep(2.3)
        if "data" not in d:
            pools.append({"error": d.get("error") or d.get("errors")}); break
        for p in d["data"]:
            a = p["attributes"]
            pools.append({"address": a["address"], "name": a["name"], "created": a["pool_created_at"],
                          "vol24": float(a["volume_usd"]["h24"] or 0), "tx24": a["transactions"]["h24"],
                          "reserve": float(a["reserve_in_usd"] or 0), "fdv": a.get("fdv_usd"),
                          "base": p["relationships"]["base_token"]["data"]["id"], "quote": p["relationships"]["quote_token"]["data"]["id"]})
        if len(d["data"]) < 20 or (pools and pools[-1].get("vol24", 0) < 200):
            break
    res[dex] = pools
    print(dex, len(pools), round(sum(p.get("vol24", 0) for p in pools)), flush=True)
json.dump({"fetched_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "calls": calls, "dexes": res}, open(OUT, "w"))
