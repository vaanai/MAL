# Keyless sim, A3 re-pin 1128 (NOT RUN by the builder; the manager runs it only if QP requires it)

Model: `/data/mal/hunt-1008/ops/manager23/s16_keyless_sim.py`; this copy changes only the deploy-slot floor for the block scan (`D_PUMP` = 474715515 replaces `D_SWAP`) and the header. Read-only, public RPC, nothing signed or sent, no key file. Buyer is a PUBLIC pubkey (the lane wallet's public address) that holds at least 0.05 SOL; pools and sellers come from recent public blocks.

Run from a CLEAN tree at the live executor commit (so `tools.pumpswap_tx` is the executor's module), cwd = tree root:

    cp ARTIFACTS/lab/program-upgrade-1128/keyless-sim/s16_keyless_sim_1128.py /data2/mal/scratch/a3-repin-1128/
    cd <clean tree at the executor commit>
    python -B -s /data2/mal/scratch/a3-repin-1128/s16_keyless_sim_1128.py --lane-pubkey <PUBLIC lane pubkey>

Bar (as 1115 (c2)): 6 of 6 simulations with err null (3 sells on third-party holders, 3 buys of 0.01 SOL with min out 1 raw unit).
Memory: one light process (a few RPC calls per second). As a MiScusi job (the manager's choice): one quoted command string, `sh`, no bash-isms.

Caveat to state with the result: PumpSwap pools aged 24 h to 200 h at this date were almost all graduated by the previous pump binary (first graduations under the new binary are at most about 10 h old at the time of writing and become FLOOR candidates from about 2026-11-28T22:02Z). The sim therefore exercises PumpSwap buy/sell shapes on pools the new pump binary did not create; the review's stage 5 (post-deploy graduation-created pools, chain evidence) covers the new-binary side.
