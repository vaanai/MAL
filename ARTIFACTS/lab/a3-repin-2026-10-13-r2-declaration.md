# R2 declaration: every-slice fetch on 15 post-upgrade pools

Written 2026-10-13T07:58:37Z (`date -u`), before any run of `r2_slices.py` on these pools. Required by quant-proof, PR #629 comment 6140926919, section 4 R2.

- **Why.** DEC-024 Amendment 7 item 2, item 4 requires every check on at least 15 graduations migrated after the deploy slot 456132171. The review's slice check (BoostBuyAndBurn, keeper-signed, zero fee) and its exact keeper slice counts covered 7.
- **Pools (15), fixed by quant-proof's rule** and written to `/data/mal/hunt-1008/h5-work/program-upgrade-1013-r2-pools.json` (sha256 `0634ca4ba24fceaa4a6024fe4428399aca9693c4cc07bb1a291a38920c8971c9`) before this file: the 7 post-upgrade pools already fetched by `s06_slices.py`; the 7 eligible post-upgrade pools whose signature-list count is 28, 31 or 32 and that are not among those; then the newest remaining eligible pool of `raw/s04_post_new.json` to reach 15. Eligible: migrate slot > 456132171, WSOL quote, InitBoost, not mayhem, profiled. All 15 are fetched afresh (fresh cache), the 7 included.
  1. `ArHMPHepQHkUvGWXh7oDbcNVAGMh1ProWQjPkswSzwfU`
  2. `H6wK6oQFa9iN63SdkziLBPrWxB4KDTJGRismgyf8rDRf`
  3. `F1wBbkThWZWN5JwFvAULiJqxT6QuQC7v2spec5sr8mq7`
  4. `D4jdDGjFyVZso4sYmKVRqnbKczoURtRuYVcynTDHGVb8`
  5. `3Gkawhzb2sjwixXSats1oMRaC2SSGEPZMmSCKAUScyHt`
  6. `HLaKoEZRc6zwmAGyVoGE5uvQSiPaTfxaQJLiFDtdkQgC`
  7. `7F1iiR8bDhC1VqTWLL7d5XNCWoTjj93LY6twV1aXteYB`
  8. `CGRDxvXHtqBzMJNgSaMYwbG2WyWZyoE61ps5LXq8nQV9`
  9. `7TrSgMQfLbgsF8xJmQ5AAi3LqBswSezSQfByzwveoTY5`
  10. `EspT33XU7PpXzg5QuruMfXGUkGEjJoD1U4EwdBDU4qxb`
  11. `DbZMpAFAatREVbu3WewxVxyxZUT5moUhs3PWFetFUaJW`
  12. `AnrCHvfJUzMtos8DGjpT2XKurVPPYAVLDturASNwXcd4`
  13. `ARd5zyCtLV1dsbfbuQUPzNPEPb4ZGCqk8e6xAyaVTyeV`
  14. `C43XByDY276GfjLrM6RAGqApWakrtpt5hm6z2A4VGaEi`
  15. `6sa9Sf1z1eGb1i4jhRZhzL1yb5cCnFV3t21Uo25jJfFm`
- **Script.** `/data/mal/hunt-1008/h5-work/program-upgrade-1013/r2_slices.py`, sha256 `03a03614067882e8e31c5786c57c34377c17482a18c7807ef6546a2b69ae2cb3`. **Deviation:** `s06_slices.py` (sha256 `adf89a3ab2f61f344a4773f5216aa0c33b5bd2b91ff356d6044c579f5e610d00`) runs on import, so the runner re-implements its logic and imports `common.py` (sha256 `36eff6ab624d11ec59bc3e4deacce27e13748cb67435ec584e6955bd98f48c2c`), `tools/pump_structure_monitor.py` (sha256 `ff85f14f5113ea1dc454cec10057d12ea5a1174865cd8c4f655ab84b428ea00d`) and `observe/trade_decode.py` (sha256 `1638468fcc83799951c2287c54ab71b0eb136167a7520c9de3f8a3bc5ad9f6a3`). Its only run so far is its author's test on one pre-upgrade pool (`2Msp3sqm…`): 29 slices, largest gap 13 s, last slice 336 s, equal to `s06_slices.py`'s earlier result for that pool.
- **Reported per pool.** Signatures fetched, exact keeper BoostBuyAndBurn slice count, keeper-signed count, zero-fee count, non-keeper transactions excluded, largest gap, last slice, fully fetched or not.
- **Test (the runner's `R2_TEST`).** Clean only if all 15 pools are fully fetched, no BoostBuyAndBurn transaction is not keeper-signed, and zero-fee equals keeper-signed over all slices. Not clean sends the decision to the owner, and the review says so.
- **The exact slice counts are reported as they come.** They feed the "Slices per pool: 29–30" band, which quant-proof has ruled is the owner's decision on the per-pool reading. No count is dropped, and no pool is added, dropped or replaced after the run.
- **Run.** One MiScusi job on mal-research-0 after R1's job, public RPC, fresh cache directory. The job prints this file's sha256 before the runner starts.
- **If the run does not finish.** A run that stops at the call cap or dies on RPC errors before printing `R2_TEST` is not a result; one further run is allowed with `--max-calls 1800` and a new fresh cache. A run that prints `R2_TEST` is the result.
- Outcome-blind: chain data only.
