# H5 STOP removal: outputs of checklist items 5 to 11

Compiled 2026-10-13T09:44:22Z (`date -u`) by the manager from MiScusi job logs. Checklist: PR #632 comment 6142079418 section H, as amended by 6142226900, with the notes of 6142341741. No canary fill, exit or P&L row was read; the daily check's wallet line is cut to its gap figure.

## Items 1 to 4 (for reference)
- Item 1: PR #632 merged `0ebfbf5bae7c07762a5130838f671247c98c60e9` at 2026-10-13T09:19:57Z, `QP OK` 6142378328 and `Reviewer OK` 6142360263 at `3a1847d`.
- Item 2: the review at the merged head, sha256 `5f67c3dcbc85c1934af94abf602d35b1f9c5941f0a2978177266e025377aa221`; the owner's dated line is in DEC-024.
- Item 3: PR #629 merged `ddc7e1f`.
- Item 4: step-3 A3 run, job #915, record sha256 `48f796b8a33179c4bf9c569a6a2cb23f10f34c7834e29fd6fd0f96c78538fd99`; quant-proof ruling E in 6142079418.

## Item 5: per-pool counts of the step-3 run's pools (job #920, declared; 2026-10-13T09:22:04Z to 09:23:34Z)
```
69e28293176602f1fc73f376abbf6ee780e2d7c2e295f4ad0f90496e00fe552f  /data/mal/hunt-1008/h5-work/resume-1013-item5/DECLARATION.md
314a576dd25728cf0dd057e02d5bd7732da2ba7d3030739776994ba3a052d07a  /data/mal/hunt-1008/h5-work/program-upgrade-1013/r1_cadence.py
4e2d93240192c22717adf4ccf305880e49e2b719877d5e036fe162da22cca664  /data/mal/hunt-1008/h5-work/resume-1013-item5/run1.json
R1_STATUS done  calls 117
R1_RANGE 456357161-456374080 migrate_found=40 eligible=28 profiled=28 last_n=28 last_median=341.5
R1_GAPS union n=28 maxgap_ge_60=0 last_gt_400=0 (of last_n=28)
ITEM5 status=done pools_profiled=28 (record 28) truncated=0
ITEM5 sig_counts {"26": 1, "28": 1, "29": 15, "30": 9, "31": 2}
ITEM5 n_le_28=2 n_le_27=1 min=26
ITEM5 gap_ge_60=[]
ITEM5 last_gt_400=[] last_not_verified=[]
ITEM5 flag_level=False
```
Flag level (9 or more of 28 at 28 or fewer, or any gap of 60 s or more, or any last slice later than 400 s): not reached.

## Items 6, 7, 9, 10: final checks run (job #927 on mal-fast-0, main `d783250`, done 2026-10-13T09:43:35Z)
```
OK    /etc/mal-h5/TIER says T0
INFO  files: STOP=yes HALT=no LIVE_OK=yes wallet_STOP=no wallet_HALT=no
INFO  live state: attempts=11 open=0 pending=0 halts=[] sells_landed=11 sells_late=0
INFO  tier: executor=T0 file=T0 since=2026-10-09T19:29Z trades_in_tier=11
OK    wallet gap +0.000000 (tolerance 0.005000)
OK    shadow feed fresh (h5-shadow-2026-10-13T09.jsonl, 0 s)
ALERT h5_idle: LIVE_OK is present but the canary is not trading: STOP exists
OK    the watchdog ran 4 min ago
INFO  refusals in the last 24 h (4 decision(s)): bad_intent:base_breaks_unresolved_settled x5, s0_recv_late x2, price_moved x2, feed_gap x1
ALERT h5_executor_alert_halt_out_of_rule_entry: the executor wrote 1 ALERT halt_out_of_rule_entry row(s) in the last 24 h (see the ledger and journalctl -u mal-h5-executor)
h5_daily_check ALERTS=2
daily_rc=1
BOOSTDAY day=2026-10-11 verdict=no_halt halt_due=False all={"n": 509, "median_s": 339.799} plain={"n": 378, "median_s": 339.2075}
EXECDAY day=2026-10-11 verdict=no_halt halt_due=False evaluated=True executor={"entry": true, "median_s": 339.799, "pools": 509, "pools_source": "boost_s"}
BOOSTDAY day=2026-10-12 verdict=no_halt halt_due=False all={"n": 410, "median_s": 341.5175} plain={"n": 289, "median_s": 340.347}
EXECDAY day=2026-10-12 verdict=no_halt halt_due=False evaluated=True executor={"entry": true, "median_s": 341.518, "pools": 410, "pools_source": "boost_s"}
BOOSTRUN day=2026-10-13 now=2026-10-13T06:17:17Z verdict=no_halt all={"n": 143, "median_s": 344.232} plain={"n": 112, "median_s": 343.314}
EXECRUN day=2026-10-13 now=2026-10-13T05:47:00Z verdict=no_halt running_lt_latch=False executor={"entry": true, "median_s": 344.659, "pools": 132, "pools_source": "boost_s"}
OK state=waiting event=waiting at=2026-10-13T09:43:03Z stale_at=2026-10-13T09:50:03Z
SHADOW newest=h5-shadow-2026-10-13T09.jsonl mtime=2026-10-13T09:43:34Z
FILE /var/lib/mal-live/STOP absent
FILE /var/lib/mal-live/HALT absent
FILE /var/lib/mal-live/h5/STOP present
FILE /var/lib/mal-live/h5/HALT absent
FILE /etc/mal-h5/LIVE_OK present
current=/usr/local/lib/mal-h5-exec/b7ddeb60aa4ced9243211775cf3d5a22ccc42dff
start_row: "max_trigger_age_ms":3500
stop_file=True halt_file=False live_ok=valid (/etc/mal-h5/LIVE_OK) exp024_part1=True
[live] attempts=11 (lifetime; 11/150 in T0) open=0/2 pending=0 today_trades=0 halts=[] seal_skips=0 sells_landed=11 sells_late=0 synthetic_unconfirmed=0 excluded=111 (synthetic:111)
done=2026-10-13T09:43:35Z
```
- Item 6: day-mode rows for 2026-10-11 and 2026-10-12 show executor and plain medians at or above 337 s, both evaluated; the latest running rows are not below 335 s; `halts=[]` (rules (b) and (c) not latched).
- Item 7: `halts=[]`, open 0, pending 0, `sells_late` 0, attempts 11 (equal to job #908), `halt_file=False`, `live_ok=valid`.
- Item 9: no wallet-wide STOP or HALT, no H5 HALT, `LIVE_OK` valid, TIER T0; `end_ms` 2026-10-23T19:29:53Z is not reached; no stop that ends the canary is in `halts` or in the daily check's alerts; no seal breach is recorded in LAB_STATE [manager's statement].
- Item 10: the daily check, started before 2026-10-13T18:28:03Z, prints two alerts: `h5_idle` and the 10-12 `out_of_rule_entry` alert; no other. The newest live `start` row prints 3500. The shadow job #817 is in the session's running list (read 2026-10-13T09:43Z) and its feed is fresh (0 s).

## Item 8: synthetic-class audit for day 2026-10-12 (job #918, started 2026-10-13T08:46:34Z, ended 09:39:28Z, main `8630bba`)
```
n_lines_read=5401 n_rows=4 n_prev_rows=4
n_lines_read=4 n_rows=4 n_prev_rows=4
{"date":"2026-10-12","n_pools":8,"n_disagree":0,"n_unclassified_now":0,"halt":false}
audit_rc=0
```

## Item 11: structure unchanged since job #915
- origin/main `d783250d60edb01581376d6a4c44e6201d59abf9`: monitor blob `1ca0a88cecf0853d94336ea046ba1a910b79f198`, pins blob `cc79c0366e4b3ec3076d60ce9f31811d7fca9bf6`.
- No monitor run after job #915: `/data/mal/structure-monitor/daily.jsonl` still ends with the run of 2026-10-13T07:11:58Z (job #907), and `resume-step3-1013.jsonl` holds one record (job #915). The 2026-10-14 daily run has not happened.

## Item 12: the pre-removal message
- (d) and (g): MiScusi question `q_hw6mloW_n2goPQ`, sent 2026-10-13T09:12:57Z. Floor for the removal: 09:42:57Z.
- (a), (b), (c), (e), (f) and (d), (g) again: notebook entry `n_haAfEksjv72ZAg` (added 09:24:12Z) and Console entry `fy-20261013-h5-pre-removal` (PR #633 merged `d783250`; on the Console host at 09:28:17Z, job #923).
- The state of the question read immediately before the removal job is recorded in the removal record, not here.
