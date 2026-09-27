Reload, in this order: `LAB_STATE.md`, `CONSTITUTION.md`, `ARTIFACTS/SUMMARY.md`, the active `DEC/` files, and `docs/HOSTS.md`.

Then report three things:

1. What the lab is trying to prove, and whether any book clears the promotion gate. Quote LAB_STATE. Do not promote a book the gate rejects.
2. Health of both hosts, read-only. Use `scripts/mal-core/agent-ssh.sh --host core` and `--host fast`. Dry-run if secrets are missing, and say so. Stop on a host-key mismatch. Do not restart units. On Oracle, run `/var/lib/mal/eng/healthcheck.sh` or read `/var/lib/mal/logs/health-latest.json`. On the fast box, check `mal-fast-create`, `mal-fast-public-logs`, `mal-fast-pre-create`, `mal-fast-backfill`, and `mal-fast-oos-score`.
3. Open pull requests (`gh pr list --repo vaanai/MAL --state open`). Repeat the keep/close note from LAB_STATE when the PR is one of #4, #5, #8, #10, #15, #22, #90, #106. Do not close anything.

Paper only. Do not print secrets.
