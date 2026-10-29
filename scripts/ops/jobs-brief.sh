#!/bin/sh
# Compact one-line-per-job view. State only; no status/log calls.
# Cuts at the name column (header-derived) so memory/time/metrics are not shown.
if [ "$1" = "--all" ]; then flag=""; else flag="--active"; fi
~/.miscusi/bin/miscusi-agent job list $flag | awk '
NR==1 { nc=index($0,"name"); mc=index($0,"memory"); next }
/^#/ { n=(mc>nc)?mc-nc:40; seq=$1; st=$2
  if ($3 ~ /%$/) st=st" "$3
  name=substr($0,nc,n); sub(/ +$/,"",name); print seq, st, substr(name,1,70) }'
