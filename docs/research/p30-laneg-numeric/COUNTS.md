# P30 count-only pass on explore-0814 (job #1618, rule sha at 9aa173d9; no price, no outcome read)

336 hours, 135,425,152 WSOL PumpSwap rows kept (184,983,429 non-WSOL rows dropped by the kit), 0 gap hours, 19.7M pool-minute marks examined.
Pools in the V band per hour-sum: 594,285; first printed in the block's first hour (age unknown, excluded): 160,298; age-known: 329,035; pool-marks passing the lane-G filters: 2,287,334.
Raw triggers 487,520 over 15 UTC days (all cells have trades on every day where they have any).

Structural finding (counts only, before any pricing): the 12 cells `PULL/PULLD/KNIFE` x `F1` (net flow >= +10% of 15-min volume) x {A0,A1} x {X1,X2} (12 cells) have **0 triggers**:
a >= 4% fall over 15 min with net buy flow >= +10% does not occur on these cards (price is moved by net flow). They stay in the grid as declared (empty cells are not retuned);
the effective grid is 36 live cells. Live-cell triggers (X1; X2 identical): MOM-F0-A0 34,626 (21,283 after the time-only overlap rule); MOM-F0-A1 15,092; MOM-F1-A0 31,667; MOM-F1-A1 13,939;
MOM2-F0-A0 24,652; MOM2-F0-A1 12,522; MOM2-F1-A0 22,669; MOM2-F1-A1 11,745; PULL-F0-A0 10,221; PULL-F0-A1 2,777; PULLD-F0-A0 7,795; PULLD-F0-A1 2,170; BNC-F0-A0 11,353; BNC-F0-A1 1,673;
BNC-F1-A0 9,246; BNC-F1-A1 1,075; KNIFE-F0-A0 21,407; KNIFE-F0-A1 9,131. Files: /data/mal/hunt-1008/push-1016/hunt30/p30-laneg/count/counts.json.
(The job's counts.md writer failed on a missing meta key after counts.json was written; fixed in a31168cb, no effect on counts.)
