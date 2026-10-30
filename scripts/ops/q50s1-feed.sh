# s1's feed arguments, sourced with POSIX `.` by scripts/ops/q50s1-shadow-start.sh and scripts/ops/q50s1-smoke.sh so the book and its smoke use the same providers (DEC-031 Amendment 22
# item 10; QP on #1107: no key in any process's argv, unit description or property, job log or journal). Not a job command by itself. POSIX sh. It never reads a key.
#   Q50S1_WS1        wss://api.mainnet-beta.solana.com (public; always the first provider)
#   Q50S1_WS2        the SECOND provider, a parameter (`--ws2 URL` of the start and smoke scripts). There is NO default: empty means mainnet-beta alone (all sockets on WS1).
#                    Every URL must be HOST-ONLY: wss://<host>[:<port>][/], host in [A-Za-z0-9.-], no path, query, fragment or userinfo (QP A1 on #1121: free tiers put the
#                    credential in the path). A credential may only be a query parameter named api-key, added by the launcher (--ws2-keyed); a provider whose URL host or
#                    path carries a credential is not used (DEC-031 Am.22 Erratum 1). Only this key-free form is on a command line, in a unit, in a log, and in `endpoints`.
#   Q50S1_WS2_KEYED  1 (`--ws2-keyed`) when the provider needs a key: scripts/ops/q50s1-ws-launch.py adds it INSIDE the shadow process, read from Q50S1_KEY_FILE
#                    (default /var/lib/mal/fast-listener/helius.env), variable Q50S1_KEY_VAR (default HELIUS_API_KEY), always as ?api-key=<key>: api-key is the only
#                    parameter S* itself redacts (observe/trade_source._safe_err; QP A2), so no other parameter name is allowed.
#                    Helius for the full window is OFF by the manager's decision of 2026-10-30 (probe #1891: about 2.33M credits a day); it needs the owner's word.
#   q50s1_ws2_build  returns 11 unless every URL is a distinct key-free wss:// URL (no query, fragment, userinfo or key-like token) and, when keyed, the key file is readable.
#   q50s1_ws_args    prints the h5_shadow --ws-url arguments (WS1, then WS2 if set); q50s1_endpoints prints the endpoints, one per line.
#   q50s1_key_opts   prints the launcher's key options (empty when not keyed).
Q50S1_WS1=wss://api.mainnet-beta.solana.com
Q50S1_WS2="${Q50S1_WS2:-}"
Q50S1_WS2_KEYED="${Q50S1_WS2_KEYED:-0}"
Q50S1_KEY_FILE="${Q50S1_KEY_FILE:-/var/lib/mal/fast-listener/helius.env}"
Q50S1_KEY_VAR="${Q50S1_KEY_VAR:-HELIUS_API_KEY}"
q50s1_url_ok() {  # host-only: wss://HOST[:PORT][/]
  case "$1" in wss://?*) ;; *) return 1 ;; esac
  q50s1_h="${1#wss://}"; q50s1_h="${q50s1_h%/}"
  case "$q50s1_h" in ''|*[!A-Za-z0-9.:-]*|*:*:*|:*|*:|.*|*.) return 1 ;; esac
  case "$q50s1_h" in *:*) case "${q50s1_h#*:}" in *[!0-9]*) return 1 ;; esac ;; esac
  return 0
}
q50s1_ws2_build() {
  q50s1_url_ok "$Q50S1_WS1" || return 11
  case "$Q50S1_WS2_KEYED" in 0|1) ;; *) return 11 ;; esac
  if [ -n "$Q50S1_WS2" ]; then
    q50s1_url_ok "$Q50S1_WS2" || return 11
    [ "$Q50S1_WS1" != "$Q50S1_WS2" ] || return 11
  else
    [ "$Q50S1_WS2_KEYED" = 0 ] || return 11
  fi
  if [ "$Q50S1_WS2_KEYED" = 1 ]; then
    [ -r "$Q50S1_KEY_FILE" ] || return 11
    case "$Q50S1_KEY_VAR" in *[!A-Za-z0-9_]*|'') return 11 ;; esac
    case "$Q50S1_KEY_FILE" in ''|*' '*) return 11 ;; esac
  fi
  return 0
}
q50s1_ws_args() { if [ -n "$Q50S1_WS2" ]; then printf '%s\n' "--ws-url $Q50S1_WS1 --ws-url $Q50S1_WS2"; else printf '%s\n' "--ws-url $Q50S1_WS1"; fi; }
q50s1_endpoints() { printf '%s\n' "$Q50S1_WS1"; [ -z "$Q50S1_WS2" ] || printf '%s\n' "$Q50S1_WS2"; }
q50s1_key_opts() {  # the variable name is given only when it differs from the launcher's default, so no key-like name is on a command line by default
  [ "$Q50S1_WS2_KEYED" = 1 ] || return 0
  q50s1_o="--key-file $Q50S1_KEY_FILE --keyed-url $Q50S1_WS2"
  [ "$Q50S1_KEY_VAR" = HELIUS_API_KEY ] || q50s1_o="$q50s1_o --key-var $Q50S1_KEY_VAR"
  printf '%s\n' "$q50s1_o"
}
