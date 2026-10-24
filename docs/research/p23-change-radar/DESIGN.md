# P23 change radar: design (desk, 2026-10-24)

Owner's thesis (2026-10-24, relayed by manager15): edges show up when something changes (the BOOST mechanics, pump redeploys,
DBC's Swap3 switch, new launchpads, fee changes) and fade as others notice. MAL should **see the change first** and test it fast,
inside its ~1-2 s latency. This file says what to watch, where each item comes from, when it alerts, what the output looks like, and
what MAL does in the 24 h after an alert. Version 1 is built as `tools/change_radar.py`. Nothing here says any book makes money.

## 0. What the records say we missed (from records only, no new reads)

| Change | When it happened | When MAL acted | Cost |
| --- | --- | --- | --- |
| Meteora DBC program redeploy and switch to `EvtSwap3` (Swap2 events stop) | DBC programdata last deploy **2026-10-16T04:23:38Z** (public RPC, read 2026-10-24 for this design); P17 REPORT dates the switch "about 2026-10-16T04Z" | P17 found it on 2026-10-24, about 8 days later | m10 holdout read #1494 NOT_DECIDABLE (every EvtSwap3 row mislabelled a hook pool; QP ruling 2a34dfc0); P14 day 3 thin |
| pump + PumpSwap redeploy | 2026-10-19T17:47:02Z / 17:47:20Z (pins file) | A3 flag in the 2026-10-20T07:11Z daily run; halt job #1299 ran 34 h 43 min after the flag | H5 arena a1 NOT_DECIDABLE (LAB_STATE 2026-10-21) |
| pump redeploy (and the fees program on 2026-10-12T17:40:24Z) | 2026-10-13T15:40:41Z | A3 flag 2026-10-14T07:11Z, H5 STOP 07:14:07Z | EXP-024 NOT_DECIDABLE; five buys after the deploy |
| Slot time step (SIMD-0525 gate) | between the 2026-10-09 07:11Z and 19:23Z monitor runs: ms/slot 267.26 -> 217.39 | WARN only | every slot-count latency figure (k slots) changed meaning |
| A3 `synthetic_share_high` 8/19 | 2026-10-22 | noticed about 29 h late | H5 arena a2 NOT_DECIDABLE (LAB_STATE 2026-10-24) |

Two lessons. (1) The pump structure monitor (`tools/pump_structure_monitor.py`, daily ~07:11Z) watches only the three pump programs; the
DBC redeploy that changed the event format was visible to one `getMultipleAccounts` call and nobody made it. Also on the list with deploys
inside the last 10 days and never looked at: DAMM v2 (2026-10-15T03:00:13Z), DAMM v1 (2026-10-15T13:02:29Z), Raydium CLMM (2026-10-22T06:24:28Z),
Jupiter v6 (2026-10-21T03:30:22Z). (2) Detection is not the bottleneck for pump; the **reaction** was (29-35 h). The radar therefore
writes its alerts where the manager reads them (MiScusi notebook lines and `data/console.json` events), not only to a log.

## 1. What to watch daily

| # | Item | Signal | Source (cost) | Alert |
| --- | --- | --- | --- | --- |
| W1 | Program upgrades | programdata last-deploy slot and upgrade authority, per program in `tools/change_radar_watch.json` | public RPC: 1 `getMultipleAccounts` on the program ids, 1 on the programdata slices (45 bytes each), `getBlockTime` per changed slot. About 3 calls | any slot change: **HIGH** for tier `core` (pump, PumpSwap, pump fees, DBC, LaunchLab, DAMM v2, CPMM), **MED** for `venue`, **LOW** for `router`. Authority change or "closed / not upgradeable any more": HIGH |
| W2 | Upgrade count | how many programdata writes since the last seen slot (two deploys in one day show as one slot move) | public RPC `getSignaturesForAddress(programdata)`, only for programs whose slot moved (1 call each) | reported with W1 |
| W3 | Fee / config accounts | sha256 and length of each watched account (pump Global, bonding FeeConfig, PumpSwap FeeConfig and GlobalConfig to start) | public RPC: 1 `getMultipleAccounts` | any hash or length change: HIGH. The account's fields are not decoded here (the structure monitor decodes the pump ones) |
| W4 | Instruction / event vocabulary (sampled) | instruction names (`Program log: Instruction: X`) and `Program data:` discriminators logged by each sampled program in its own invoke frame, plus the 8-byte discriminators of its outer/inner instructions and of its emit_cpi (self-CPI) events, which is where DBC's `EvtSwap3` lives | public RPC: per program 1 `getSignaturesForAddress` (limit 50) + up to K `getTransaction` (default K 20, successful txs only). About 21 x 6 = 126 calls | a key never seen before for that program: MED (HIGH when the program also moved in W1). A program's first 3 successful runs only seed its vocabulary (warm-up), so rare old instructions do not alert as new |
| W5 | Venue counts from MAL's own tapes | rows per (program, row type) per UTC day: pump.fun `create`, `trade`, `complete`; PumpSwap `trade`, `pool_create` (arena tape); LaunchLab `create`, `trade`, `ix`; DBC `ix` rows split by instruction name (`Swap`, `Swap2`, `Swap3`, `InitializeVirtualPool*`, `Migration*`) and `event_undecoded` by discriminator (venue tape) | local files, 0 RPC. Rows are counted by their `type`/`prog`/`venue` keys and DBC/LaunchLab instruction-name lists only; no price, amount, reserve, mint, pool or wallet field is read into the output | see section 2 |
| W6 | Venue share | each venue's share of daily launches and of daily trades, among the venues MAL records | derived from W5 | share move >= 10 percentage points vs the trailing median: MED |
| W7 | Structure monitor pass-through | the latest `pump_structure_monitor` record's HALT and WARN flags (BOOST share and timing, synthetic share, ms/slot, docs commits, USDC BOOST) | local file `/data/mal/structure-monitor/daily.jsonl` (its own job, 0 extra RPC) | every true HALT flag: HIGH; every true WARN: LOW. Repeated, not recomputed |

Not in version 1 (each needs its own design before code):

- **New launchpads we do not know about.** Version 1 only watches a fixed list. Discovery needs a broad view: either a sampled `getBlock`
  (public RPC does not serve full blocks at a useful rate; on Helius the per-call credit cost of `getBlock` for MAL's plan was not looked
  up for this design, so a run needs a measured estimate before it is proposed) or a free index (GeckoTerminal / DexScreener "new pools by dex", unauthenticated HTTP). Proposed v2: one daily HTTP pull of
  new-pool counts by dex id, alert on a dex id never seen before with >= 200 new pools in a day. Not built.
- **New token mechanics inside known programs** (BOOST, mayhem, cashback, USDC quote). Partly covered: a new mechanic almost always ships with a
  redeploy (W1) and new instruction or event names (W4); the pump-specific shares (mayhem flag, BOOST, quote mint) are the structure
  monitor's (W7). A per-mechanic share from the arena tape would need fields beyond row type (quote mint, flags); version 1 does not read them.
- **Large new market makers / bots.** Needs signer-level counts (top-N signer share of trades per venue per day). That is wallet-level data
  from the tape; it needs a QP ruling on whether a share of top signers is a count or an outcome, then a v2 stage. Not built.
- **Fee parameter values** on non-pump venues (DBC config, LaunchLab global config, CPMM AmmConfig). W3 can hash any account once its address is
  verified and added to the watch file; the addresses are not in the repo yet.

### Program list (tools/change_radar_watch.json)

All ids were checked on 2026-10-24 with public `getMultipleAccounts`: present, executable, owned by the upgradeable loader. Deploy slots
seen that day are recorded in the watch file as a note only; the radar's own state file is the baseline.

| name | tier | id | last deploy seen 2026-10-24 |
| --- | --- | --- | --- |
| pump | core | 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P | 2026-10-19T17:47:02Z |
| pumpswap | core | pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA | 2026-10-19T17:47:20Z |
| pump_fees | core | pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ | 2026-10-12T17:40:24Z |
| meteora_dbc | core | dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN | 2026-10-16T04:23:38Z |
| meteora_damm_v2 | core | cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG | 2026-10-15T03:00:13Z |
| raydium_launchlab | core | LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj | 2026-09-11T18:11:14Z |
| raydium_cpmm | core | CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C | 2026-10-01T19:05:48Z |
| meteora_damm_v1 | venue | Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB | 2026-10-15T13:02:29Z |
| raydium_amm_v4 | venue | 675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8 | 2026-09-10T01:52:56Z |
| raydium_clmm | venue | CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK | 2026-10-22T06:24:28Z |
| moonshot | venue | MoonCVVNZFSYkqNXP6bxHLPL6QQJiMagDL3qcqUQTrG | 2025-06-30T13:15:10Z |
| boop | venue | boop8hVGQGqehUK2iVEMEnMrL5RbjywRzHKBmBE7ry4 | 2025-08-26T10:52:26Z |
| heaven | venue | HEAVENoP2qxoeuF8Dj2oT1GHEnu49U5mJYkdeC8BAX2o | 2026-04-02T17:46:55Z |
| jupiter_v6 | router | JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4 | 2026-10-21T03:30:22Z |

The labels "boop", "heaven" and "moonshot" are from memory: the ids are executable upgradeable programs, but which product each one is was not
checked against official docs. Meteora DLMM is left out: the id I had was not a valid address (checked 2026-10-24). Add a program by adding a
row to the watch file; its first run is a baseline, not an alert.

## 2. Alert thresholds for the tape counts (W5, W6)

Per metric m (launches, trades, graduations per venue; per-key row counts) for the closed UTC day D:

- **Coverage first.** A venue-day counts only if its tape has >= 20 hour files present for D. A day under that is written with
  `coverage_short: true` and never alerts and never enters a baseline. Counts are scaled to 24 h by hours present (`rate = n * 24 / hours`).
- **Baseline** = median of the scaled counts over up to 7 earlier covered days of the same metric. At least 3 baseline days or no alert
  ("baseline_short").
- **Shift**: rate / baseline >= 1.5 or <= 0.67, and max(rate, baseline) >= 50 rows: MED. Ratio >= 3 or <= 0.33: HIGH.
- **New key**: a (venue, type) or (venue, instruction name) or (venue, event discriminator) key with >= 5 rows on D and 0 rows on every
  baseline day: HIGH (the Swap3 case).
- **Gone key**: 0 rows on D and baseline >= 100 rows/day: HIGH (the Swap2-stops case).
- **Share**: a venue's launch share or trade share moves >= 10 percentage points from its baseline median share: MED.

The thresholds are first guesses written before any run of the tool on real tape. They are tuned only by a dated amendment to this file,
and the amendment says how many alerts per day the old setting gave.

## 2a. Sealed windows (QP verdict on PR #889, 2026-10-24)

- The radar **never counts gap or health rows (or any other row) of a sealed window.** Row counts, gap rows and hours present are all
  information about a sealed window, so a UTC day of a tape that overlaps a sealed window is skipped as a whole: no hour file of it is
  opened and no record of it is written, now or after the window ends. Only the skip itself ("arena 2026-10-23 sealed") is recorded.
- The windows are a pinned config file, `tools/change_radar_seals.json` (fail closed: a missing or malformed file refuses the run). Start:
  arena tape [2026-10-22T21:00:00Z, 2026-10-26T21:00:00Z), owner FLOOR. Changes go through the manager and QP.
- **The radar is disabled for the arena tape before 2026-10-26T21:00:00Z** (`disabled_until` in the same file): before that instant the
  arena tape directory is not even listed. Because the sealed days are skipped for good, the arena baseline restarts from 2026-10-27.
- Disclosure: the 2026-10-24 smoke runs (about 17:51Z and 18:05Z, written to a scratch directory, not `/data/mal/change-radar`) counted
  arena rows for 2026-10-22 and 2026-10-23, including gap rows and hours present, before this rule existed. See the PR #889 body.

## 3. Output

Every run writes under `--out-dir` (default `/data/mal/change-radar`):

- `radar.jsonl`: one JSON record per run (schema `change_radar.v1`): run time, RPC host, stages and their errors, program and account
  observations, vocabulary sizes, per-day counts read this run, and the list of changes.
- `state.json`: the baseline (program slots and authorities, account hashes, the vocabulary per program). Written atomically after the
  record. A missing state file means the run is a baseline run.
- `daily-counts.jsonl`: one record per (source, UTC day) with the counts; the baselines are read from it.
- `digest-<run date>.md` and `digest-<run date>.json`: the human digest.
- `console-events-<run date>.json`: zero or more objects in the `data/console.json` `events` shape
  (`id`, `kind` "finding", `title`, `detail`, `atUtc`) for the manager to merge (the radar never edits the repo).

Stdout ends with the digest lines, ready for `miscusi_notebook_add` (kind finding):

```
CHANGES 2026-10-25 high=1 med=0 low=0 (programs 14/14 read, accounts 4/4, vocab 7/7, tape days 2)
CHANGE HIGH program_upgrade meteora_dbc deploy_slot 457080000 -> 457502600 (2026-10-16T04:23:38Z) writes_since=1
CHANGE HIGH new_key venue_tape meteora_dbc ix:Swap3 day 2026-10-16 n=812345 baseline 0
```

`CHANGES none` when nothing moved. The digest carries counts, slots, hashes and names only.

## 4. Playbook: change detected -> forward book

Owner direction 2026-10-21: work with redeploys, do not stop for them. Owner 2026-10-22: the manager makes rule and lane decisions and tells
the owner after. The playbook keeps every existing rule (promotion gate, HOLDOUT_LEDGER, DEC-031 arena rules, no new forward book in a
kill-review week).

| Step | When | What | Who |
| --- | --- | --- | --- |
| P0 Triage | within 2 h of the digest | Is a running book or guard affected? A HIGH change on a program a registered book trades follows that book's A3-style rule (H5 Am.10, the DBC m10 guard). The radar never halts anything itself | manager |
| P1 Census | within 24 h | **Counts only**: what changed in the vocabulary (new instruction / event layout, decoded from public RPC samples or an IDL), launches/trades/graduations per hour before and after the change instant, share of rows on the new path. No price, no outcome, no screen cell. Written to `docs/research/<lane>/CENSUS.md` with the change instant from W1 | builder or Haiku agent |
| P2 Seal check | before any priced read | Which tape covers the pre- and post-change hours, and who owns them in `docs/HOLDOUT_LEDGER.md` (D1/D2/D3 domains, reserved venue blocks such as LaunchLab and DBC [10-17, 10-24), the DBC m10 window [10-25, 11-01)). Owned or reserved hours are not read. If the post-change hours are all owned, wait or buy a new block | manager + QP when unclear |
| P3 DECLARE | before the screen | A DECLARE file in the P14 pattern: cells, pricer, fail models, the window split into **pre-change** (exploration) and **post-change** hours, written and committed before any attempt count or price on post-change hours. Pre-change hours tell what the change broke; post-change hours are where the new edge could be | builder |
| P4 Screen | 24-72 h | Price the declared cells on post-change exploration hours only, the gate numbers reported as exploration. A cell that only works because of the change must show the effect inside the post-change window, not as pre/post contrast alone | builder, QP review of any positive sentence |
| P5 Forward book | next arena slot | A surviving cell goes to the DEC-031 arena (frozen sha, clock, day-1 dry score, A3-style guard that includes this radar's W1/W4 for its program). Live only through the promotion gate and the owner's OK | manager |

Speed targets: P0 same day, P1 within 24 h, P3+P4 within 72 h of the change instant. The point is to be early in the decay window, so a
lane that misses 72 h records why.

## 5. Costs and limits

- RPC: public mainnet only, about 130-150 calls per run at the defaults (the 2026-10-24 smoke made 85 calls at K 12) (hard cap `--max-calls`, default 300). **0 Helius credits.** The tool
  refuses any Helius URL and any RPC URL with a query string or credentials in it; it reads no key file.
- Disk / memory: tape counting streams `zstd -dc` per hour file and keeps only counters; memory well under 1 GB. Runtime is dominated by
  decompressing the arena tape (about 0.8 GB of text per hour on 2026-10-24); each day is counted once and stored in `daily-counts.jsonl`.
- Public RPC drops: a failed stage is recorded as an error and the other stages still run (status `partial`). An unread program is
  "unread", never "changed".
- Sampled vocabulary (W4) misses rare instructions: 20 transactions per program per day. The venue tape (W5) sees every instruction name for
  DBC and LaunchLab; pump and PumpSwap have no instruction-name tape.
- Sealed windows: section 2a.
- Paths: the tape readers refuse a directory whose path contains `holdout`, `fwdv1`, `shadow`, `probe`, `sealed`, `ledger` or `bots.json`, and
  read only `tape-<hour>.jsonl[.zst]` names. Backfill tapes (`venue-tape-backfill`, `forward-*`) are not inputs.

## 6. Run

Daily as a MiScusi job after the structure monitor (07:11Z), so W7 sees the same day's record. Command and job spec: PR body.
