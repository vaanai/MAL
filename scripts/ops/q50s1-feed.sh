# s1's feed arguments (DEC-031 Amendment 22 item 10), sourced with POSIX `.` by scripts/ops/q50s1-shadow-start.sh and scripts/ops/q50s1-smoke.sh so the book and its smoke use the
# same two providers. Not a job command by itself. POSIX sh. It never prints, logs or exports the key.
#   Q50S1_WS1        wss://api.mainnet-beta.solana.com (public)
#   Q50S1_WS2_BASE   wss://mainnet.helius-rpc.com/ (key-free: the only form ever echoed, and the `endpoints` value of the registration record)
#   q50s1_ws2_build  sets Q50S1_WS2 = Q50S1_WS2_BASE + "?api-key=<HELIUS_API_KEY>" read from $Q50S1_HELIUS_ENV (default /var/lib/mal/fast-listener/helius.env, a KEY=VALUE file;
#                    it is read with sed, never sourced or executed). Returns 11 (and leaves Q50S1_WS2 empty) if the file is unreadable or the value is empty or has a character
#                    outside [A-Za-z0-9-]. The caller passes Q50S1_WS2 only as an argument of the shadow process (the residual: the key is in that process's argv,
#                    /proc/<pid>/cmdline, readable by the same uid; S*'s h5_shadow strips the query from the logged argv and the start record's endpoints).
Q50S1_WS1=wss://api.mainnet-beta.solana.com
Q50S1_WS2_BASE=wss://mainnet.helius-rpc.com/
Q50S1_HELIUS_ENV="${Q50S1_HELIUS_ENV:-/var/lib/mal/fast-listener/helius.env}"
Q50S1_WS2=""
q50s1_ws2_build() {
  Q50S1_WS2=""
  [ -r "$Q50S1_HELIUS_ENV" ] || return 11
  q50s1_k="$(sed -n -e 's/^HELIUS_API_KEY=//p' -e 's/^export HELIUS_API_KEY=//p' "$Q50S1_HELIUS_ENV" 2>/dev/null | head -n 1 | tr -d "\"'\r")" || q50s1_k=""
  case "$q50s1_k" in ''|*[!A-Za-z0-9-]*) q50s1_k=""; return 11 ;; esac
  Q50S1_WS2="${Q50S1_WS2_BASE}?api-key=${q50s1_k}"
  q50s1_k=""
  return 0
}
