# P18 look gate: how "LOOK CONSUMED" reaches mal-fast-0 (design, pre-build; nothing here was submitted)

`start_guard` lstat()s `/data/mal/hunt-1008/push-1016/hunt29/dbc/HOLDOUT_READ_DONE_v3` (research-0 only). The P18 process runs on mal-fast-0. `start_guard` is unchanged. The live entry
`tools/dbc_p18_live.py` passes `--look-flag` (a local file on fast-0) as start_guard's existing `marker` argument, with `tools.dbc_p18_gate.flag_valid` as `marker_exists`. The 11-01T00:10Z floor
in start_guard still applies. The marker is never opened (lstat only), by the probe or by anything else. QP #1067 accepted the flag; start is on LOOK CONSUMED, not PASS.

Jobs (commands only; submit as MiScusi jobs):
1. research-0, J1 `p18-look-probe`, time limit 40 min, MemoryMax 256M: `/data/mal/venv/bin/python -m tools.dbc_p18_gate probe --deadline-min 30` from the candidate-sha worktree.
   Exit 0 = marker present, 1 = absent at the deadline. Prints `{"marker_exists": ..., "polls": n}`.
2. fast-0, J2 `p18-look-arm`, `after: J1` (cross-machine `after` was verified by the manager on 2026-10-27: #1794 on research-0 then #1795 on fast-0):
   `python3 -m tools.dbc_p18_gate arm --flag ~/p18/LOOK_CONSUMED.flag --probe-job <J1 id>`.
3. fast-0, J3 `p18-probe` (10 min), `after: J2`: `python3 -m tools.dbc_p18_live --probe --env-file /var/lib/mal/fast-listener/helius.env --credit-cap <N> --look-flag ~/p18/LOOK_CONSUMED.flag --minutes 10 --out <dir>`.
4. fast-0, J4 `p18-feed` (the book), `after: J2` (and after J3 if the probe is to precede it):
   `python3 -m tools.dbc_p18_live --live --env-file /var/lib/mal/fast-listener/helius.env --credit-cap <N> --look-flag ~/p18/LOOK_CONSUMED.flag --close-lag 2 --slot-source ws --out <dir>`.
   The command PINS `--close-lag 2 --slot-source ws`. A `wire` row with other values voids that process.

Rules (QP #1067, Q-A conditions):
- Never resume, re-run or hand-submit J2 after a J1 exit other than 0, unless J1's log shows `"marker_exists": true`.
- On FAIL, NOT_DECIDABLE, PASS-SCREEN-LOOKAHEAD or no verdict: stop J3/J4 at once, DELETE the flag (`rm ~/p18/LOOK_CONSUMED.flag`), register nothing.
- The registration cites J1's id, its exit 0 and its `marker_exists` line, J2's id, and the flag's JSON.

## Config fetch and L90 (fixed before the dry score)
- A first-sight fetch runs in a thread, only for SOL-quoted trade rows (R4's scan scope). Rows keep their true receive stamps (`t_recv_ms` is set by the feed before the sink). While a fetch
  runs, every later row and slot signal waits in one FIFO and is delivered in order when the fetch ends. The wait is inside `t_decide - t_recv_trigger`, so L90 counts every stall in full, with no correction.
- Every `cfg_fetch` chain row has `t_start_ms`, `t_end_ms`, `fetch_ms`, attempts, result and reason. The dry score prints (`stall_report`): number of fetches, total and max fetch time, and the number of
  decisions whose trigger row was received during a fetch window.
- Disclosed departures from R4 (state them in the registration): (a) first-sight single `getAccountInfo` instead of the read's sorted batch before load (configs treated as static on-chain; not verified);
  (b) 2 attempts then UNMAPPED. R4 has no fetch-failure-to-unmapped path (the read uses `http_rpc` with 6 attempts and a final failure makes it NOT_DECIDABLE). One transient error here removes every pool of
  that config for the life of the process.
- Bars for that departure (fixed before the process starts): unmapped configs are split by reason (`absent` / `undecodable` / `rpc_error`, `status.json` `wire.cfg_unmapped.configs_by_reason`); the unmapped
  SOL-quoted row share (`wire.cfg_unmapped.share`) is printed every dry-score day and every P18 read, and R4's bar (> 5% = NOT_DECIDABLE) applies on the dry-score day.
