# AMENDMENT 1 (pre-read) to REGISTRATION.md: DBC m10 registered forward read, causal deciding set

Status: DRAFT until it is tagged `dbc-m10-forward-v1-am1` after a reviewer verdict and a QP OK. Its content is FIXED by QP-RULING-1024-m10-lookahead.md section 2
(sha256 ca92ed6a423027e45b526832de653dd3aa5eff716d60b574117a633ce5104b32). AM1-AM6 below are copied VERBATIM from that ruling (lines 56-75). Nothing in this file adds to them;
the section-3 numbers did not change any AM item.

## AM1-AM6 (verbatim)
  - AM1 Deciding set: the live-policy decisions of tools/dbc_fwd.Engine, CAUSAL (no oracle):
    - fed the window tape in tape (slot) order;
    - first_ok_bt = 2026-10-25T01:00Z;
    - ss_live exactly as in dbc_fwd.replay.
    Each sealed decision is priced by the frozen pricer: dbc_screen.resolve(cell, P, [i], D, comp_slot) on the full-window load_exploration D, with i = the row of the same key
    (pool|slot|post sqrt price), then dbc_screen.price_cell.
  - AM2 Refusal of a SEALED decision. Never dropped silently; each count is printed.
    - bad_state: priced on D with bad_row zeroed (as the registered no_bad_row_exclusion line).
    - completed_before_entry and no_state: -160,000 lamports (one send).
    - block_edge and bad_hour: excluded (window artifacts).
    - key not found in D: full loss (stake + send + 2,039,280 rent). If more than 1% of decisions are not found: NOT_DECIDABLE.
  - AM3 R11 and the R9 2.5 s condition are applied to this set, under BOTH fail models. Every other part of R1-R14 is unchanged: window, data, config map, k = 1, one look, marker v3.
  - AM4 The as-registered screen set is printed REPORT-ONLY, with the overlap counts and per-model means of the screen-only and causal-only entries. It never decides; "PASS if either" is forbidden.
  - AM5 Pins: dbc_fwd.py merged (reviewer verdict + QP OK on the PR); the both-oracle replay re-run at the amendment head gives md5 triggers 6965028e..., entries 074a8273...;
    dbc_price.py / dbc_screen.py / arena_stack.py unchanged (7db0afdd / 607e3173 / a39b2ba6); new tag dbc-m10-forward-v1-am1; SHA256SUMS addendum with `date -u`; the section-3 result sha quoted.
  - AM6 Deadline: the tag should be placed before 2026-10-25T00:00Z. It is admissible up to 2026-11-01T00:00Z ONLY if ALL of these hold at the tag instant:
    - no window hour has been opened, counted or smoked (R3 listings and completeness markers only);
    - no DBC decision log, live-feed output or credit meter beyond the R3 counts has been produced or read for any instant >= 10-25T00Z;
    - the amendment text equals AM1-AM5.
    Otherwise the read runs unamended. Its PASS is then recorded as PASS-SCREEN-LOOKAHEAD, which licenses nothing and ends m10 (NOT_DECIDABLE by design for licensing).

## Implementation (for the pins; not part of AM1-AM6)
- Read tool: `python3 -m tools.dbc_fwd_read_am1 --confirm-one-look` (default out /data/mal/hunt-1008/push-1016/hunt29/dbc/fwd-read-v1-am1). Same time gate, preflight, setup-before-claim,
  marker v3 (`tools.dbc_fwd_read.MARKER`, O_CREAT|O_EXCL: once either tool claims it the other refuses), "LOOK CONSUMED" before the window guard opens, R4 config map, bad-hour rule.
  AM1 set from `tools/dbc_fwd.Engine` (no oracle, live policy); AM1/AM2 pricing = `tools/dbc_fwd_sec3.Pricer` + `price` (the code that produced the section-3 result);
  AM3 verdict = `tools.dbc_fwd_read.verdict` (R11 + R9) plus the R1 100-fill rule; AM4 screen set under `screen_report_only`, never in the verdict.
  AM2 edge rules declared in docs/research/dbc-p18-build1/SEC3-DECLARE.md (bad_state still refused on the zeroed D, or any other refusal: full loss) apply unchanged.
- Tests (synthetic only): `python3 -m tools.test_dbc_fwd_read_am1`. No window hour was opened, counted or smoked; nothing under fwdv1/ was read (AM6).

## Section-3 result (QP-RULING-1024-m10-lookahead.md section 3), quoted
- MiScusi job #1533, script tools/dbc_fwd_sec3.py sha256 403eae486826..., declared docs/research/dbc-p18-build1/SEC3-DECLARE.md (2026-10-24T14:31:01Z), taken 2026-10-24T14:47:43Z.
- /data/mal/hunt-1008/push-1016/hunt29/dbc/sec3-m10-causal.json sha256 addf52f3635b54a8e2ca2803868cd40c6fe6be1a23efce75850743a022738895; .md sha256 4774d492c4a731c453708a01be52be9defec637257c9b27d24ecd60357af7c2a.
- Kill test on C1: PASS. IN-SAMPLE (best of 23 screened cells, about 2.2 days, ended venue state): not evidence, not a gate pass.

## AM5 pins (sha256 at the amendment head; the tag commit's SHA256SUMS addendum with `date -u` is the manager's)
```
4c7953b6979fc25ac464d7ea98a304b0e658727c6aae0cfd4ba58ec5aa000b8b  tools/dbc_fwd_read_am1.py
b43e79558d997bb1852a0245d5a0d05220be57c6ea88b83548ad431f051b43f6  tools/dbc_fwd.py
403eae4868260f19d9990d00641885d92b5272ee9c148d1d61277e1d4b0dabdf  tools/dbc_fwd_sec3.py
ccffd905942cb0a071f981478e27aba7b90b8e3ff5ec1b6b0ed52a89515bb183  tools/dbc_fwd_read.py
7db0afdd7845c0d159f73ec438632dfb2ddc7ef45ab3ef971569625e1335a7eb  tools/dbc_price.py
607e3173cae997b30972aef1f863c9355014bb01cee6f6622e352b42b4363b66  tools/dbc_screen.py
a39b2ba68dd46b7d3a555fb8e06580718300f354020c1f91280ab7a0e0856778  tools/arena_stack.py
04a015e0c42548549457d4b1b96f2eccda62022dfe1a740cf73db26d9c72d2c1  tools/dbc_config.py
10a52a52ee382a56e13e03b3fa76c1a36cd690b414b83e2f91c8438f000f8535  tools/dbc_decode.py
b4b67f8d203722a16b2d5238f4a0f2b38776995130cd4b5691ee649c043a0783  tools/arena_declog.py
913c27256dbfa11762c039957d5977a4a01fcaae5e41ac1ce4b730ef0988ca07  docs/research/dbc-p17/data/cfg_summary.json
```
- dbc_price.py / dbc_screen.py / arena_stack.py are unchanged (7db0afdd / 607e3173 / a39b2ba6). dbc_fwd_read.py is unchanged (ccffd905), and so is dbc_fwd.py (b43e7955, merged in #878).
- Both-oracle replay at the amendment head: PENDING. It needs one MiScusi job (Swap2 hours only): `python3 -m tools.dbc_fwd --replay --start 2026-10-14T00 --end 2026-10-16T04 --out <dir>`.
  Required: equivalence_pass true, md5 triggers 6965028e46dba86f7293f4d27284a321, entries 074a827303e94e8049284f67aafe7471. Record the job id and result sha here before the tag.
