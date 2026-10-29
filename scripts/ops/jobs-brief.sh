#!/bin/sh
# Compact one-line-per-job view. State only; no status/log calls.
if [ "$1" = "--all" ]; then flag=""; else flag="--active"; fi
~/.miscusi/bin/miscusi-agent job list $flag | awk 'NR>1 && /^#/ {
  seq=$1; st=$2; $1=""; $2=""; sub(/^ +/,""); print seq, st, substr($0,1,70) }'
