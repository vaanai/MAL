#!/bin/sh
# A3 halt, FAST-0 half (POSIX sh, no bash-isms): apply the decision printed by the research-0 job to the arena registry. Run as a MiScusi job on mal-fast-0, `after` the
# research-0 job. Usage: sh scripts/ops/a3-apply-halt.sh '<the A3_HALT_DECISION line or its JSON>'     (single-quote it)
# Env (defaults): A3_REPO, A3_PY (fast-0 has no /data/mal/venv), A3_BOOKS (the books file: legacy_bot_ids / new_rule_bot_ids), A3_ARENA_ROOT.
# Needs only tools/a3_halt.py and tools/arena_h5_score.py from the repo. Idempotent: an earlier end is never moved later.
# Exit: 0 applied or nothing to apply; 2 the decision or the books file is malformed (nothing written); 12 the registry write failed.
A3_REPO=${A3_REPO:-$HOME/MAL}
A3_PY=${A3_PY:-/home/claude/venvs/c1nf-shadow/bin/python}
A3_BOOKS=${A3_BOOKS:-$HOME/data/arena/a3-books.json}
A3_ARENA_ROOT=${A3_ARENA_ROOT:-$HOME/data/arena}
if [ $# -lt 1 ] || [ -z "$1" ]; then echo "usage: a3-apply-halt.sh '<A3_HALT_DECISION ...>'" >&2; exit 2; fi
cd "$A3_REPO" || exit 1
"$A3_PY" -m tools.a3_halt --decision "$1" --books "$A3_BOOKS" --arena-root "$A3_ARENA_ROOT"
rc=$?
echo "A3_APPLY_HALT_RC=$rc"
if [ "$rc" -eq 2 ]; then exit 2; fi
if [ "$rc" -ne 0 ]; then exit 12; fi
exit 0
