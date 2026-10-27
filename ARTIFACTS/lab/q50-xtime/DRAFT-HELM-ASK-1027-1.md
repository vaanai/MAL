# DRAFT-HELM-ASK-1027-1 (NOT SENT; manager to review, then rename to HELM-ASK-1027-1.md). Drafted 2026-10-27 (`date -u` ~20:40Z)

To Helm, from the manager. Answers the 5 items of HELM-REPLY-1027-1. This is the go-live PRE-STAGE Vaan approved on 2026-10-26 19:07Z (HELM-REPLY-1026-1): re-install, then a 1 h KEYLESS dry run, before any key exists.

**State plainly: no key, no wallet, no funding, no LIVE_OK, no TIER, no go-live.** None of these is asked for here. Each still needs Vaan's own word at the time (window 10-30 00:00-10:00Z). No book has cleared the promotion gate; this ask is infrastructure rehearsal only.

## 1. Unit and host
`mal-h5-arena-executor` (book `h5v1q50-boostfloor-a1`) and `mal-h5-arena-watch.timer`, on **mal-fast-0**. Keyless dry mode only (`dry.json`, no `user`, no key, no `funded_lamports`), exactly as HELM-SHEET sections A-E and the 10-24 install (HELM-REPLY-1024).

## 2. Commit and manifest
- Commit Y = `c8fcf761b773b8efc9b9a17387fbdc440c3eacec` (origin/main on 2026-10-27). Install from the manager's `~/MAL` checkout on fast-0 only, as in the sheet.
- Manifest: `sh scripts/ops/q50-xtime-manifest.sh Y` ran rc 0 on research-0: 43 pinned paths, list sha256 `244f9e85dd5870bfc47be1cf535eadecd0e2783cc17fee11440ab0ea7ae83720`, exactly FOUR changed pinned paths vs the 10-24 install 0526d5cd (`tools/h5_arena_executor.py`, `tools/pumpswap_tx.py`, `tools/pumpswap_simulate.py`, `tools/probe_executor.py`). Manifest file sha256 `8168bdf514c0c8052c4142064ab86cb7c3065c2ebf7620d1d5fb8feb4cd28463` (2255 lines). The manager will regenerate it on fast-0 and send that sha256; the numbers must agree, else STOP.
- Executor sha256 must be `ef8ae9315095c32ff5179567ef63d64803ddd2bde723b09f74fdc82de4decdc4` (was c451a615 on 10-24); launcher `f53eb63b...` and ladder `23d7dafd...` unchanged.
- HELM-SHEET still says 0526d5cd in places (the whole-sheet re-pin is owed). For this pre-stage read every `0526d5cd` as Y and use the manifest above; the sheet's section A executor line is patched by `ARTIFACTS/lab/q50-xtime/HELM-SHEET-A-repin.patch`.
- Not the final install: the live install will be at X (merge sha of the filled DEC copy, after the gate read) and needs its own manifest and 1 h dry run. This pre-stage finds problems early; it does not replace that one.

## 3. Old STOP file
- Path: `/var/lib/mal-live/STOP` (and `/var/lib/mal-live/HALT` if present): the old probe wallet's wallet-wide files, which also stop this executor (HELM-SHEET section F, last bullet). Vaan OK'd moving the old STOP file (HELM-REPLY-1026-1).
- Move to: `/root/disabled/mal-live.STOP.20261028` (and `mal-live.HALT.20261028`; use the date you do it), the same directory where the old LIVE_OK/TIER went (`/root/disabled/mal-h5.LIVE_OK.20261023`, `mal-h5.TIER.20261023`, HELM-REPLY-1024-2). Use `mv`, do not delete.
- Please confirm what exists first: HELM-REPLY-1024 lists `/var/lib/mal-live/{STOP,HALT}` under "Absent" (l.82) and "Not touched" (l.83). If neither exists, nothing to move; say so. Do NOT touch the c1nf STOP, and do not create any STOP under `/var/lib/mal-h5-arena/` (that is the executor's own path and stays absent).

## 4. 1 h keyless dry run: pass criteria
Run per HELM-SHEET section E (watch timer on during the run, off afterwards unless told). PASS only if all hold; report counts only, open no outcome field:
1. 0 build errors: zero `build_error` / `simulate_error` alerts in the journal.
2. `a3_guard_unreadable` = 0 (`journalctl -u mal-h5-arena-executor --no-pager | grep -c a3_guard_unreadable`). The guard read goes through the bind `__A3_DIR__:/srv/mal-h5-arena/a3`; `dry.json` carries `a3_record_file`.
3. Report the ledger counts by (kind, reason) filtered to reasons starting `a3_`: `a3_guard_pending` and any A3 latch, as counts only.
4. Startup lines show `mode=dryrun book=h5v1q50-boostfloor-a1` and `feed_start=verified`; NRestarts 0; ActiveState active at the end; no traceback, `startup_refused`, `signals_file_missing`, `halt_latched`, `feed_unverified`.
5. `sha256sum` of the configs and unit files as the sheet's section E lists, and the executor sha256 above.
6. Executor and watch timer stopped and disabled afterwards, as on 10-24, unless the manager says otherwise. `/etc/mal-h5-arena/{LIVE_OK,TIER}`, `/etc/mal-h5/{LIVE_OK,TIER}`, live.conf: still absent; key dir empty.

## 5. Timing
Please finish the re-install and the 1 h run before 2026-10-30T00:00Z, ideally on **10-28 or 10-29, daytime UTC**, so a failure leaves a day to fix it. About 1.5-2 h in total (10-24 precedent: install 06:07Z, dry run 06:08-07:08Z). Tell the manager the start instant and the result in a HELM-REPLY.

## Correction to HELM-REPLY-1024-2 (fee)
HELM-REPLY-1024-2 says live.json needs "p155 fee pin". Wrong for Q50 C0: the fee is the **compiled default** (buy 55,000 / sell 55,000 / escalated 150,000 lamports of priority, `PRIORITY_DEFAULT` in `tools/h5_arena_executor.py`; DEC-033 `FILL:fee`). A p155 value in live.json refuses the start (`priority_pin_mismatch`). So live.json either omits `priority_pin` or declares exactly the default: `"priority_pin": {"profile": "default", "buy": 55000, "sell": 55000, "escalated": 150000}` (as in `ARTIFACTS/lab/c0-drafts/live.json.DRAFT-NOT-INSTALLED`). Any other value, including a p155 one, is `priority_pin_mismatch`. `sell_priority_lamports` / `escalated_priority_lamports` keys are refused outright. The QP-checked live.json draft will come later, with the C0 constants (0.05 SOL stake, max open 2, 30 buys a day, daily stop 0.10, total stop 0.20), not dry.json's H5 base limits.
