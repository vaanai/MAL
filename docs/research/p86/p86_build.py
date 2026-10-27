"""P86 step 1: build the hourly candidate set and the 10 causal features (NO label). Usage: p86_build.py PSDIR OUT.parquet COUNTS.txt
Canonical pools only (V band). Decision t_d = H:02:00Z. Bucket hb = (minute-2)//60 holds exactly the 60 min before t_d = (hb+1)*3600+120."""
import glob, json, sys
import duckdb
import pandas as pd

PS, OUT, COUNTS = sys.argv[1:4]
PH = {"BUpWiWy8Tqed4jy86dr7ZpoaE747DpQsrGnwmtpMwfBr", "oi1ewFJP7HikgdbjhnUboCzuWXHanAe4Wtvy8UFEnd6"}
VD = "/data/mal/pumpswap-virtual/"
LO, HI = 17_500_000_000, 17_700_000_000
v = {}
for f in ("pool_v_0814.json", "pool_v.json", "pool_v_0909.json"):
    for p, x in json.load(open(VD + f))["v"].items():
        if x is not None and p not in v:
            v[p] = x
vdf = pd.DataFrame({"pool": list(v), "v": list(v.values())})
vdf = vdf[(vdf.v >= LO) & (vdf.v <= HI)].reset_index(drop=True)
con = duckdb.connect("/data2/mal/p86/work.duckdb")
con.execute("PRAGMA threads=8; PRAGMA memory_limit='24GB'; PRAGMA temp_directory='/data2/mal/p86/tmp'")
con.register("vdf", vdf)
con.execute("CREATE OR REPLACE TABLE V AS SELECT * FROM vdf")
glob_ = PS + "/*.parquet"
ph = "(" + ",".join(f"'{x}'" for x in PH) + ")"
con.execute(f"""CREATE OR REPLACE TABLE M AS
 SELECT r.pool, r.bt//60 AS m, count(*) AS n, sum(CASE WHEN side='buy' THEN sol ELSE 0 END)::DOUBLE AS bs, sum(CASE WHEN side='sell' THEN sol ELSE 0 END)::DOUBLE AS ss,
        max(bt) AS lastbt, max_by(q::DOUBLE, slot*10000000+coalesce(txi,0)*1000+coalesce(evi,0)) AS ql,
        max_by((q::DOUBLE+V.v)/b::DOUBLE, slot*10000000+coalesce(txi,0)*1000+coalesce(evi,0)) AS pl, max((q::DOUBLE+V.v)/b::DOUBLE) AS pmax, any_value(V.v) AS v
 FROM read_parquet('{glob_}') r JOIN V ON V.pool=r.pool
 WHERE r.wsol AND r.sig <> '{'1'*64}' AND r.trader NOT IN {ph} AND r.b > 0
 GROUP BY r.pool, r.bt//60""")
con.execute("CREATE OR REPLACE TABLE FIRST AS SELECT pool, min(bt) AS first_bt FROM read_parquet('" + glob_ + "') WHERE wsol GROUP BY pool")
print("M rows", con.execute("select count(*) from M").fetchone(), flush=True)
con.execute(f"""CREATE OR REPLACE TABLE T AS
 SELECT r.pool, r.bt//60 AS m, r.trader, sum(sol)::DOUBLE AS vs FROM read_parquet('{glob_}') r JOIN V ON V.pool=r.pool
 WHERE r.wsol AND r.sig <> '{'1'*64}' AND r.trader NOT IN {ph} GROUP BY r.pool, r.bt//60, r.trader""")
print("T rows", con.execute("select count(*) from T").fetchone(), flush=True)
# hourly bucket stats
con.execute("""CREATE OR REPLACE TABLE B AS
 SELECT pool, (m-2)//60 AS hb, sum(n) AS n60, sum(CASE WHEN m-2-((m-2)//60)*60 >= 45 THEN n ELSE 0 END) AS n15,
        (sum(bs)+sum(ss))/1e9 AS vol60, (sum(bs)-sum(ss))/1e9 AS net60, max(lastbt) AS lastbt, max(m) AS lastm
 FROM M GROUP BY pool, (m-2)//60""")
con.execute("""CREATE OR REPLACE TABLE HRS AS SELECT (m-2)//60 AS hb, sum(n) AS rows FROM M GROUP BY 1""")
# screen on cheap criteria
con.execute("""CREATE OR REPLACE TABLE S0 AS
 SELECT B.pool, B.hb, (B.hb+1)*3600+120 AS td, B.n60, B.n15, B.vol60, B.net60, B.lastbt, B.lastm,
        ((B.hb+1)*3600+120) - F.first_bt AS age_s, ml.ql/1e9 AS q_real, ml.v/1e9 AS v_sol
 FROM B JOIN FIRST F ON F.pool=B.pool JOIN M ml ON ml.pool=B.pool AND ml.m=B.lastm
 WHERE B.n15 >= 20 AND B.vol60 >= 10 AND ((B.hb+1)*3600+120) - B.lastbt <= 120
   AND ((B.hb+1)*3600+120) - F.first_bt BETWEEN 7200 AND 604800 AND ml.ql/1e9 >= 30
   AND 2*0.25/(ml.ql/1e9 + ml.v/1e9 + 0.25) <= 0.03""")
print("S0 rows", con.execute("select count(*) from S0").fetchone(), flush=True)
# signers15 and top3 share, per pool-hour
con.execute("""CREATE OR REPLACE TABLE SG AS
 SELECT S0.pool, S0.hb, count(DISTINCT CASE WHEN T.m >= (S0.hb*60+2)+45 THEN T.trader END) AS signers15
 FROM S0 JOIN T ON T.pool=S0.pool AND T.m >= S0.hb*60+2 AND T.m < S0.hb*60+62 GROUP BY S0.pool, S0.hb""")
con.execute("""CREATE OR REPLACE TABLE TP AS
 SELECT pool, hb, sum(vs) FILTER (WHERE rk<=3)/sum(vs) AS top3 FROM (
   SELECT S0.pool, S0.hb, T.trader, sum(T.vs) AS vs, row_number() OVER (PARTITION BY S0.pool, S0.hb ORDER BY sum(T.vs) DESC) AS rk
   FROM S0 JOIN T ON T.pool=S0.pool AND T.m >= S0.hb*60+2 AND T.m < S0.hb*60+62 GROUP BY S0.pool, S0.hb, T.trader) GROUP BY pool, hb""")
con.execute("""CREATE OR REPLACE TABLE S1 AS
 SELECT S0.*, SG.signers15, TP.top3 FROM S0 JOIN SG USING (pool, hb) JOIN TP USING (pool, hb) WHERE SG.signers15 >= 8""")
print("S1 rows", con.execute("select count(*) from S1").fetchone(), flush=True)
# price lookups (last print at minute < cut; cut minute = hb*60+2+60 - w)
for w, nm in ((0, "p0"), (5, "p5"), (15, "p15"), (60, "p60")):
    con.execute(f"""CREATE OR REPLACE TABLE {nm} AS
     SELECT S1.pool, S1.hb, M.pl AS {nm} FROM S1 ASOF LEFT JOIN M ON S1.pool=M.pool AND (S1.hb*60+62-{w}) > M.m""")
con.execute("""CREATE OR REPLACE TABLE HI AS
 SELECT S1.pool, S1.hb, max(M.pmax) AS hi6 FROM S1 JOIN M ON M.pool=S1.pool AND M.m >= S1.hb*60+62-360 AND M.m < S1.hb*60+62 GROUP BY S1.pool, S1.hb""")
con.execute("""CREATE OR REPLACE TABLE C AS
 SELECT S1.*, p0.p0, p5.p5, p15.p15, p60.p60, HI.hi6 FROM S1 JOIN p0 USING (pool,hb) JOIN p5 USING (pool,hb) JOIN p15 USING (pool,hb) JOIN p60 USING (pool,hb) JOIN HI USING (pool,hb)""")
df = con.execute("SELECT * FROM C").df()
n_all = len(df)
df = df.dropna(subset=["p0", "p5", "p15", "p60", "hi6"])
n_undef = n_all - len(df)
import numpy as np
df["log_vol60"] = np.log1p(df.vol60)
df["ret_5m"] = df.p0 / df.p5 - 1
df["ret_15m"] = df.p0 / df.p15 - 1
df["ret_60m"] = df.p0 / df.p60 - 1
df["flow60"] = df.net60 / df.vol60
df["log_signers15"] = np.log1p(df.signers15)
df["log_age"] = np.log(df.age_s.astype(float))
df["top3_60m"] = df.top3
df["close_vs_hi"] = df.p0 / df.hi6
df["log_qeff"] = np.log(df.q_real + df.v_sol)
df = df.sort_values(["td", "vol60", "pool"], ascending=[True, False, True])
df["rank_screen"] = df.groupby("td").cumcount() + 1
npass = df.groupby("td").size()
cand = df[df.rank_screen <= 8].copy()
hc = cand.groupby("td").size()
keep = hc[hc >= 3].index
cand = cand[cand.td.isin(keep)]
hrs = con.execute("SELECT hb, rows FROM HRS ORDER BY hb").df()
cand.to_parquet(OUT)
with open(COUNTS, "w") as fh:
    fh.write(f"V-band pools {len(vdf)}\nS0 pool-hours {len(df)+n_undef} (cheap screen)\nS1 after signers>=8 {n_all}\nfeature-undefined dropped {n_undef}\n")
    fh.write(f"hours with >=1 survivor {len(npass)}; with >=3 {int((npass>=3).sum())}; candidate hours kept {len(keep)}; candidates {len(cand)}\n")
    fh.write(f"survivors per hour: mean {npass.mean():.2f} median {npass.median()} p10 {npass.quantile(.1)} p90 {npass.quantile(.9)}\n")
    fh.write("hour rows (M minute-agg rows) min/median: %d %d; hours with fewer than 50k rows: %s\n" % (hrs.rows.min(), hrs.rows.median(), [int(x) for x in hrs[hrs.rows < 50000].hb]))
print(open(COUNTS).read())
