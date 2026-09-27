## Data layer
- [ ] [Trade tape](https://github.com/vaanai/MAL/pull/73) — live on public RPC; [Helius firehose](https://cursor.com/agents/bc-bc62c5da-bf6f-569d-92a7-54a2aab434ca) too costly (~172k credits/h), later use per-mint subscriptions for held positions only
- [ ] [Helius backfill](https://cursor.com/agents/bc-a176f9bd-c1a4-5d5d-9a78-02d62d51eed7) — Oracle on Sep 22 at half a core; fast box walking back from Sep 21 at ~3 hours of history per hour, capped at +2M credits
- [ ] [Funding graph](https://cursor.com/agents/bc-d4079567-2fbe-5379-90ae-0c6ee47d1984) — versioned-tx lookups succeeding after the restart; fix is in [stale-fill PR](https://github.com/vaanai/MAL/pull/102)
- [ ] [Attention layer](https://github.com/vaanai/MAL/pull/82) — poller up; [unsealed-hour fix](https://github.com/vaanai/MAL/pull/100) under review by [nightly fix](https://cursor.com/agents/bc-c7e6be03-f574-5337-96c4-8edef8f2c65f), rescore rerun before tonight

## Signal search
- [ ] [Forward candidates](https://github.com/vaanai/MAL/pull/95) — clean main live, lag 39 ms with capped backfill; clean days start 2026-09-28 00:00 UTC
- [ ] [Graduated swing](https://github.com/vaanai/MAL/pull/94) — same 1.25% fee; honest-latency gross is about zero, not a lead
- [ ] [LAYA](https://github.com/vaanai/MAL/pull/91) — Sep 27 train OOM at its 11G cap; [nightly fix](https://cursor.com/agents/bc-c7e6be03-f574-5337-96c4-8edef8f2c65f) trimming the 04:15 job to what frozen books need, tested before tonight
- [ ] [Latency curve](https://cursor.com/agents/bc-62f43346-aca0-54a5-86d6-bc6dbeacf527) — flat; speed alone adds about +0.2 pp gross per slot
- [ ] [Migrate direct cell](https://cursor.com/agents/bc-eb57b4f6-05b1-5a36-9135-d64d6e6c973c) — unseen days n=60, net +0.5% at 0.5 SOL, CI lower bound −3.2%; fast box backfilling Sep 21 back, 5 unseen days around 20:00 UTC Sep 28
- [ ] [Backward holdout](https://cursor.com/agents/bc-f772e4fe-d409-5a16-ac18-26ac5f06bc91) — [PR](https://github.com/vaanai/MAL/pull/97) merged; fake +114 SOL now −0.62 SOL, nothing promotes; table grows nightly
- [ ] Daily 05:00 UTC review — Sep 27: nothing promotes; runner at 39 ms with capped backfill; kill review 05:00 UTC Oct 5

## Host
- [ ] [Claude handoff](https://cursor.com/agents/bc-7efc3ee4-8dd0-55f2-9fc0-a8eaf81960a9) — one PR moving store content into git plus Claude setup; owner's reviewer merges
- [ ] [Fast listener](https://cursor.com/agents/bc-0a2c37cf-eaab-5e7d-9c18-b96559be6cea) — Helius create stream on (+110 ms); per-mint curve stream 95% bonding coverage, +436 ms, too costly at 120s; free Frankfurt tape +128 ms, off; waiting on latency curve
- [ ] [Host hardening](https://cursor.com/agents/bc-3010be97-788f-5230-b249-96c7b15fe53f) — forward paper is a clean checkout of main; old hand-patched copy kept for rollback

## Done
- [x] [Latency and fee scoring](https://github.com/vaanai/MAL/pull/107) — merged; fee math audited and correct
- [x] [Backfill CPU cap](https://github.com/vaanai/MAL/pull/104) — merged; half of one core, runner lag stayed under 200 ms
- [x] [Exit-scan fix](https://github.com/vaanai/MAL/pull/103) — merged and live; one-core exit replay was the lag, now 168 ms with backfill off

[Archived](archived.md)
