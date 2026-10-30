# s1's feed arguments, sourced with POSIX `.` by scripts/ops/q50s1-shadow-start.sh and scripts/ops/q50s1-smoke.sh so the book and its smoke use the same providers (DEC-031 Amendment 22
# item 10, as bound by QP on #1107: no key in any process's argv, unit description or property, job log or journal). Not a job command by itself. POSIX sh. It never reads the key.
#   Q50S1_WS1        wss://api.mainnet-beta.solana.com (public)
#   Q50S1_WS2        wss://mainnet.helius-rpc.com/ : the KEY-FREE base of the keyed Helius websocket. Only this base is ever on a command line, in a unit or in a log; the
#                    registration record's `endpoints` value. The key is added INSIDE the shadow process by scripts/ops/q50s1-ws-launch.py, which reads it from Q50S1_KEY_FILE.
#   Q50S1_KEY_FILE   default /var/lib/mal/fast-listener/helius.env (ACL user:claude:r, Amendment 13 Erratum 1). This helper only tests that it is readable.
#   q50s1_ws2_build  returns 11 unless both URLs are distinct key-free wss:// URLs (no query, fragment, userinfo or key-like token) and the key file is readable.
Q50S1_WS1=wss://api.mainnet-beta.solana.com
Q50S1_WS2=wss://mainnet.helius-rpc.com/
Q50S1_KEY_FILE="${Q50S1_KEY_FILE:-/var/lib/mal/fast-listener/helius.env}"
q50s1_url_ok() {
  case "$1" in wss://?*) ;; *) return 1 ;; esac
  case "$1" in *[?#@]*|*[Kk][Ee][Yy]=*|*[Tt][Oo][Kk][Ee][Nn]*|*' '*) return 1 ;; esac
  return 0
}
q50s1_ws2_build() {
  q50s1_url_ok "$Q50S1_WS1" && q50s1_url_ok "$Q50S1_WS2" || return 11
  [ "$Q50S1_WS1" != "$Q50S1_WS2" ] || return 11
  [ -r "$Q50S1_KEY_FILE" ] || return 11
  return 0
}
