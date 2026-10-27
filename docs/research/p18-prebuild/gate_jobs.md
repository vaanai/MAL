# P18 look gate: how "LOOK CONSUMED" reaches mal-fast-0 (design, pre-build; nothing here was submitted)

`start_guard` lstat()s `/data/mal/hunt-1008/push-1016/hunt29/dbc/HOLDOUT_READ_DONE_v3` (research-0 only). The P18 process runs on mal-fast-0. `start_guard` is unchanged. The live entry
`tools/dbc_p18_live.py` passes `--look-flag` (a local file on fast-0) as start_guard's existing `marker` argument, with `tools.dbc_p18_gate.flag_valid` as `marker_exists`. The 11-01T00:10Z floor
in start_guard still applies. The marker is never opened (lstat only), by the probe or by anything else.

Jobs (commands only; submit them as MiScusi jobs when QP agrees):
1. research-0, J1 `p18-look-probe`, time limit 40 min, MemoryMax 256M: `/data/mal/venv/bin/python -m tools.dbc_p18_gate probe --deadline-min 30` from the candidate-sha worktree.
   Exit 0 = marker present, 1 = absent at the deadline. Prints `{"marker_exists": ..., "polls": n}`.
2. fast-0, J2 `p18-look-arm`, `after: J1` (cross-machine support is UNVERIFIED, see below): `python3 -m tools.dbc_p18_gate arm --flag ~/p18/LOOK_CONSUMED.flag --probe-job <J1 id>`.
3. fast-0, J3 `p18-probe` (10 min) and J4 `p18-feed` (the book), each `after: J2`; both read the flag through `--look-flag ~/p18/LOOK_CONSUMED.flag`.

Fallback if `after` does not cross machines: the manager reads J1's exit through the MiScusi job tools and submits J2 by hand with J1's id. Same flag, same checks.
The flag does not carry the read's verdict. On FAIL, NOT_DECIDABLE or PASS-SCREEN-LOOKAHEAD the manager stops J3/J4 and deletes the flag (CLAUDE.md / QP Q1: stop at once).
